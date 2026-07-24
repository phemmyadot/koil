"""Exhaustion dashboard backend.

Run from the project root:
    .\\.venv\\Scripts\\python.exe -m uvicorn webapp.app:app --port 8123

Architecture: fetch and compute are decoupled from the request path.
webapp/data.py owns a shared raw-OHLCV cache (one fetch serves all four
strategy evaluators, refreshed on a market-hours-aware schedule -- see that
module for the exact rule). On startup, and whenever the background
refresher decides the cache is stale, every ticker's full payload (all four
strategies) is recomputed once and held in memory. A page request is then
just a fast in-memory read -- no network call, no per-request backtest run.
"""
import importlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import webapp.build_universe as build_universe
import webapp.data as data
import webapp.optimizer as optimizer
import webapp.prebreak as prebreak
import webapp.score as score
from webapp.scoring import evaluate
import webapp.tickers as tickers_module
import webapp.strategy_vcp as strategy_vcp
import webapp.strategy_vcpo as strategy_vcpo

app = FastAPI(title="Exhaustion Dashboard")

TICKERS = tickers_module.TICKERS
_computed: list[dict] = []
_computed_errors: dict[str, str] = {}
_computed_asof: str | None = None
_compute_lock = threading.Lock()

_STRATEGY_MODULES = {"strategy_vcp": strategy_vcp, "strategy_vcpo": strategy_vcpo}


def _eval_other_strategy(key: str, module, ticker: str, bars) -> dict | None:
    """Independently error-isolated -- one strategy failing on a ticker
    shouldn't drop the other strategies' results for that same ticker.
    Note: "optimized" is NOT looked up here -- it's overlaid fresh at request
    time (see _with_fresh_optimized below), since this function's result is
    baked into the static _computed snapshot at compute_all() time, which
    only reruns every 2h/on refresh. The optimizer finishes on its own,
    unrelated cadence, so baking its result in here would mean newly-swept
    configs sit invisible until the next unrelated price refresh."""
    try:
        baseline = module.evaluate(ticker, bars)
    except Exception:  # noqa: BLE001
        return None
    return {"baseline": baseline, "baseline_config": module.BASELINE_CONFIG}


def _compute_one(ticker: str) -> tuple[str, dict | None, str | None]:
    bars = data.get_bars(ticker)
    if bars is None:
        return ticker, None, data.get_error(ticker) or "no data"
    try:
        payload = evaluate(ticker, bars)
        for key, module in _STRATEGY_MODULES.items():
            payload[key] = _eval_other_strategy(key, module, ticker, bars)
        # Ticker-level, not strategy-specific -- applies across VEXH/VCP/VCPO
        # alike, same as the Pine indicator overlays regardless of which
        # strategy you're trading. A failure here shouldn't drop the ticker.
        try:
            payload["prebreak"] = prebreak.evaluate(ticker, bars)
        except Exception:  # noqa: BLE001
            payload["prebreak"] = None
        # "score" is already taken -- scoring.evaluate() uses it for VEXH's
        # legacy 6-gate condition count. setup_score is the new 0-10 composite
        # and is keyed per strategy since VCP/VCPO/VEXH each have their own
        # stats to score against.
        payload["setup_score"] = {}
        for strat_key in ("vexh", *_STRATEGY_MODULES.keys()):
            try:
                payload["setup_score"][strat_key] = score.compute_score(payload, strat_key)
            except Exception:  # noqa: BLE001
                payload["setup_score"][strat_key] = None
        return ticker, payload, None
    except Exception as e:  # noqa: BLE001 - per-ticker failures must not break the page
        return ticker, None, str(e) or type(e).__name__


def compute_all() -> None:
    """Recompute every ticker's full payload from whatever's currently in the
    raw cache. Pure CPU work, no network -- safe to call from a background
    thread without blocking request handling."""
    global _computed, _computed_errors, _computed_asof
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_compute_one, TICKERS))
    with _compute_lock:
        _computed = [p for _, p, _ in results if p is not None]
        _computed_errors = {t: e for t, _, e in results if e is not None}
        _computed_asof = datetime.now(timezone.utc).isoformat(timespec="seconds")


_UNIVERSE_DATE_PATH = os.path.join(os.path.dirname(__file__), "universe_last_screened.txt")


def _universe_last_screened_date() -> str | None:
    try:
        with open(_UNIVERSE_DATE_PATH) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def _mark_universe_screened_today() -> None:
    with open(_UNIVERSE_DATE_PATH, "w") as f:
        f.write(datetime.now(timezone.utc).date().isoformat())


def rebuild_universe() -> str | None:
    """Re-screen Yahoo and overwrite webapp/tickers.py in-process, then reload
    it into TICKERS. Runs the same screen as `python -m webapp.build_universe`
    (same env-var-driven cap/vol/price/exchange defaults) but inline, so a
    manual refresh in prod never requires SSH-ing in to run the CLI by hand.
    Best-effort: the screener API rate-limits under repeated calls, and a
    failed re-screen shouldn't block refreshing prices for the existing
    universe. Returns an error string on failure, None on success.

    Always does the real work when called -- the once-a-day gate lives in
    the background loop (see _daily_universe_refresh_if_needed), not here,
    so the manual Refresh button always forces a real re-screen regardless
    of when the last automatic one ran."""
    global TICKERS
    try:
        candidates = build_universe.fetch_candidates()
        passed = build_universe.screen_technicals(candidates)
        build_universe.write_tickers_file(passed)
    except Exception as e:  # noqa: BLE001 - fall back to the existing tickers.py
        return str(e) or type(e).__name__
    importlib.reload(tickers_module)
    TICKERS = tickers_module.TICKERS
    _mark_universe_screened_today()
    return None


def _daily_universe_refresh_if_needed() -> None:
    """Automatic counterpart to the manual Refresh button -- re-screens the
    ticker universe at most once per calendar day (UTC), so the list of
    tradeable tickers doesn't go stale for weeks just because nobody clicked
    Refresh, but also doesn't re-run the ~60-90s Yahoo screen on every
    background-loop wakeup. Errors are logged, not raised -- a failed
    automatic re-screen should never take down the price-refresh loop."""
    today = datetime.now(timezone.utc).date().isoformat()
    if _universe_last_screened_date() == today:
        return
    err = rebuild_universe()
    if err:
        print(f"app: automatic daily universe refresh failed ({err}); keeping existing tickers.py")


def refresh_and_compute(force: bool = False) -> None:
    """Fetch (if needed) then recompute. Called once at startup, by the
    background refresher whenever webapp.data says the cache is stale, and
    by the manual Refresh button (force=True, via ?refresh=1).

    force=False (startup/background): data.warm_cache() only re-fetches
    tickers whose on-disk cached bars are missing/stale (see data.py's
    per-ticker TTL) -- a container rebuild reuses today's already-fetched
    bars instead of re-fetching all ~2000 tickers from Yahoo every deploy.
    force=True: re-fetches every ticker regardless of TTL, same blocking
    full refresh as before the disk cache existed.

    Explicitly passes this module's live TICKERS -- data.py's own TICKERS
    is bound once at import time (`from webapp.tickers import TICKERS`), so
    it goes stale the moment rebuild_universe() reloads tickers_module here.
    Relying on data.warm_cache()'s no-arg fallback to its own TICKERS would
    fetch bars for the OLD universe, leaving every ticker in the NEW one
    with no cache entry -- every _compute_one() call then fails "no data"."""
    data.warm_cache(TICKERS, force=force)
    compute_all()


@app.on_event("startup")
def _on_startup():
    # The first fetch+compute pass used to run inline here, which meant
    # uvicorn didn't start accepting connections until it finished -- on a
    # cold cache (first-ever start) that's a genuine unavoidable wait, but on
    # every redeploy after that it was a needless delay before the port even
    # opened, despite most/all data already being on disk. Now it runs in the
    # background so the server (and /api/meta, /api/tickers) is reachable
    # immediately; the frontend polls "asof"/"total_tickers" and shows a
    # loading state until the first pass actually lands.
    def loop():
        refresh_and_compute()
        while True:
            time.sleep(data.CHECK_INTERVAL)
            # Ticker universe: re-screened at most once per day, automatically
            # -- was previously only refreshed by a manual Refresh click, so
            # it could go stale for weeks with nobody noticing.
            _daily_universe_refresh_if_needed()
            # Prices: independent cadence (see data.is_stale) -- around
            # market open, every couple hours while open, once after close.
            if data.is_stale():
                refresh_and_compute()
    threading.Thread(target=loop, daemon=True).start()

    # Per-ticker parameter optimization runs on its own slow (daily) cadence,
    # completely decoupled from signal computation above -- see webapp/optimizer.py.
    optimizer.start_background_optimizer()


@app.get("/api/meta")
def meta():
    return {"total_tickers": len(TICKERS), "last_fetch": data.last_fetch_time()}


def _with_fresh_optimized(payload: dict) -> dict:
    """Cheap dict-lookup overlay, done at request time so a ticker's optimized
    config shows up as soon as the optimizer finishes it -- not just after the
    next unrelated price-data refresh rebuilds _computed."""
    out = dict(payload)
    for key in _STRATEGY_MODULES:
        strat = out.get(key)
        if strat is not None:
            out[key] = {**strat, "optimized": optimizer.get_optimized(payload["ticker"], key)}
    return out


@app.get("/api/tickers")
def tickers(refresh: int = 0):
    universe_error = None
    if refresh:
        universe_error = rebuild_universe()
        refresh_and_compute(force=True)
    with _compute_lock:
        computed_snapshot = list(_computed)
        asof, errors = _computed_asof, dict(_computed_errors)
    return {
        "asof": asof,
        "cached": not refresh,
        "tickers": [_with_fresh_optimized(p) for p in computed_snapshot],
        "errors": errors,
        "universe_error": universe_error,
    }


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"),
                           html=True), name="static")

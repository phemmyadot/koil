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
import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# The data.py _fetched_at epoch each ticker's _computed entry was computed
# against -- lets a fresh process tell "this cached result still matches the
# current price data" (data.py's own price_cache.pkl hasn't refetched this
# ticker since) apart from "this cached result is for stale/old price data
# and must be recomputed," without needing to actually recompute to find out.
_computed_source_fetch: dict[str, float] = {}
_compute_lock = threading.Lock()

# Persists _computed the same way data.py persists _raw_cache -- a container
# restart used to always force a full ~2100-ticker recompute (several
# minutes) even when the price cache itself was fully warm, because
# _computed is pure in-memory state with nothing backing it on disk. Bind-
# mounted like price_cache.pkl/tickers.py; see docker-compose.yml/deploy.sh.
_COMPUTED_CACHE_PATH = os.path.join(os.path.dirname(__file__), "computed_cache.pkl")


def _load_computed_cache() -> None:
    """Best-effort load on import -- same failure posture as data.py's
    _load_price_cache(): missing/corrupt file just means everything gets
    recomputed, same as a truly cold start. Must never raise."""
    global _computed, _computed_errors, _computed_asof, _computed_source_fetch
    if not os.path.isfile(_COMPUTED_CACHE_PATH):
        return
    try:
        with open(_COMPUTED_CACHE_PATH, "rb") as f:
            payload = pickle.load(f)
        _computed = payload.get("computed", [])
        _computed_errors = payload.get("errors", {})
        _computed_asof = payload.get("asof")
        _computed_source_fetch = payload.get("source_fetch", {})
    except Exception as e:  # noqa: BLE001 - corrupted cache file, not a crash
        print(f"app: computed cache load failed ({e}); starting cold.")
        _computed, _computed_errors, _computed_asof, _computed_source_fetch = [], {}, None, {}


def _save_computed_cache() -> None:
    # Written in place, not via a .tmp-then-os.replace() atomic swap -- same
    # reason as data.py's _save_price_cache(): this path is a Docker bind
    # mount, and os.replace() onto a bind-mounted file fails with EBUSY.
    try:
        with open(_COMPUTED_CACHE_PATH, "wb") as f:
            pickle.dump({"computed": _computed, "errors": _computed_errors,
                         "asof": _computed_asof, "source_fetch": _computed_source_fetch}, f)
    except Exception as e:  # noqa: BLE001 - failing to persist shouldn't crash a refresh
        print(f"app: computed cache save failed ({e})")


_load_computed_cache()

# Live progress for the current compute_all() call, if one is in flight --
# mirrors data.py's fetch_progress so the frontend can show a separate
# "now computing" stage after "now fetching tickers" finishes, instead of
# one loader that silently covers both phases.
_compute_progress: dict[str, int] | None = None


def compute_progress() -> dict[str, int] | None:
    with _compute_lock:
        return dict(_compute_progress) if _compute_progress is not None else None

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
    """Recompute each ticker's full payload, but only for tickers whose
    underlying price bars actually changed since the last compute pass --
    everything else is reused from _computed as-is. Without this, a
    container restart always forced a full ~2100-ticker recompute (several
    minutes of pure CPU work) even when data.py's price cache was fully
    warm and nothing had actually changed, since _computed itself used to
    be pure in-memory state with nothing backing it on disk.

    Staleness is keyed off data.get_fetched_at(ticker) -- data.py's own
    per-ticker fetch timestamp -- not a separate TTL, so this can never
    disagree with what data.py considers "this ticker's bars are current.\""""
    global _computed, _computed_errors, _computed_asof, _computed_source_fetch, _compute_progress

    with _compute_lock:
        prior_by_ticker = {p["ticker"]: p for p in _computed}
        prior_source_fetch = dict(_computed_source_fetch)
        prior_errors = dict(_computed_errors)

    to_compute = []
    reused_payloads: dict[str, dict] = {}
    reused_source_fetch: dict[str, float] = {}
    reused_errors: dict[str, str] = {}
    for tk in TICKERS:
        fetched_at = data.get_fetched_at(tk)
        if fetched_at is not None and prior_source_fetch.get(tk) == fetched_at:
            # Bars unchanged since this ticker was last computed -- reuse
            # whichever prior outcome it had (a payload, or a compute error).
            if tk in prior_by_ticker:
                reused_payloads[tk] = prior_by_ticker[tk]
                reused_source_fetch[tk] = fetched_at
            elif tk in prior_errors:
                reused_errors[tk] = prior_errors[tk]
                reused_source_fetch[tk] = fetched_at
            else:
                to_compute.append(tk)  # stale bookkeeping, e.g. after a cache format change
        else:
            to_compute.append(tk)

    with _compute_lock:
        _compute_progress = {"done": 0, "total": len(to_compute)}
    try:
        results = []
        if to_compute:
            with ThreadPoolExecutor(max_workers=16) as pool:
                futures = [pool.submit(_compute_one, tk) for tk in to_compute]
                for future in as_completed(futures):
                    results.append(future.result())
                    with _compute_lock:
                        _compute_progress["done"] += 1
        with _compute_lock:
            new_source_fetch = {tk: data.get_fetched_at(tk) for tk, payload, err in results
                                 if payload is not None or err is not None}
            _computed = list(reused_payloads.values()) + [p for _, p, _ in results if p is not None]
            _computed_errors = {**reused_errors, **{t: e for t, _, e in results if e is not None}}
            _computed_source_fetch = {**reused_source_fetch, **new_source_fetch}
            _computed_asof = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save_computed_cache()
        # Force a final flush -- scoring.py's earnings-date cache saves are
        # debounced per-ticker during the batch above (up to ~2100 misses in
        # one pass), so the last few tickers computed within the debounce
        # window of an earlier save might not have been persisted yet.
        import webapp.scoring as scoring
        scoring._save_earnings_cache(force=True)
    finally:
        with _compute_lock:
            _compute_progress = None


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
    return {
        "total_tickers": len(TICKERS),
        "last_fetch": data.last_fetch_time(),
        # Non-null only while a warm_cache() fetch is actively in flight --
        # lets the frontend show real "N of M loaded" progress during the
        # first post-deploy fetch instead of a bare spinner.
        "fetch_progress": data.fetch_progress(),
        # Same shape, next stage -- non-null only while compute_all() is
        # actively running the strategy evaluations over already-fetched
        # bars. Fetching and computing are sequential (refresh_and_compute
        # calls warm_cache then compute_all), so these two are never both
        # non-null at once -- frontend can show "now fetching" then "now
        # computing" as two distinct stages instead of one loader covering
        # both with no visibility into which phase is actually running.
        "compute_progress": compute_progress(),
    }


@app.get("/api/debug/memory")
def debug_memory():
    """Live process memory breakdown -- queries the actual running server's
    in-memory state (unlike `docker compose exec ... python -c "..."`, which
    starts a fresh interpreter and would only show a reloaded-from-disk
    approximation, not what the live server is really holding). Not secured
    -- fine for a LAN-only box behind Cloudflare Tunnel with no public route
    to this path configured, but don't expose this publicly as-is."""
    import gc
    import resource
    import sys
    import webapp.scoring as scoring

    with _compute_lock:
        computed_count = len(_computed)

    before_gc = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    collected = gc.collect()
    after_gc = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    return {
        "rss_mb": round(after_gc, 1),
        "rss_mb_before_gc": round(before_gc, 1),
        "gc_collected_objects": collected,
        "raw_cache_tickers": len(data._raw_cache),
        "raw_cache_pickled_mb": round(len(__import__("pickle").dumps(data._raw_cache)) / 1024 / 1024, 1),
        "computed_entries": computed_count,
        "computed_pickled_mb": round(len(__import__("pickle").dumps(_computed)) / 1024 / 1024, 1),
        "raw_errors": len(data._raw_errors),
        "computed_errors": len(_computed_errors),
        "earnings_cache_tickers": len(scoring._earnings_cache),
        "earnings_cache_pickled_mb": round(
            len(__import__("pickle").dumps(scoring._earnings_cache)) / 1024 / 1024, 1),
        "total_gc_tracked_objects": len(gc.get_objects()),
    }


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

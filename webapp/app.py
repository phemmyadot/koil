"""Exhaustion dashboard backend.

Run from the project root:
    .\\.venv\\Scripts\\python.exe -m uvicorn webapp.app:app --port 8123

Architecture: fetch and compute are decoupled from the request path entirely
-- see webapp/refresh_architecture.md for the full rules. webapp/data.py
owns a shared raw-OHLCV cache, gap-fetched on a fixed background-loop
cadence (no per-request or per-page-load fetch ever happens). Whatever
tickers' prices actually changed get recomputed; everything else is reused
from the last compute pass. A page request is always just a fast in-memory
read -- no network call, no per-request backtest run, regardless of how
stale or fresh the underlying data happens to be at that moment.
"""
import importlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import webapp.build_universe as build_universe
import webapp.data as data
import webapp.db as db
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
# The last_bar_date each ticker's _computed entry was computed against --
# lets a fresh process tell "this cached result still matches the current
# price data" (db.get_last_bar_date(ticker) hasn't advanced since) apart
# from "this cached result is for stale/old price data and must be
# recomputed," without needing to actually recompute to find out. Keyed off
# last_bar_date, NOT a fetch timestamp -- a gap-fetch attempt always
# advances a fetch timestamp even when zero new rows come back, which would
# make every ticker look "changed" on every background-loop wake.
_computed_source_fetch: dict[str, str] = {}
_compute_lock = threading.Lock()


def _load_computed_from_db() -> None:
    """Best-effort load on import -- same failure posture as data.py's
    _load_from_db(): an empty/corrupt DB just means everything gets
    recomputed, same as a truly cold start. Must never raise.

    Restores _computed_asof from the DB's own MAX(computed_at) too --
    without this, a restart with a fully warm DB still read back asof=None,
    which the frontend treats as "nothing computed yet" and shows the
    cold-start loader for, even though every ticker's result was already
    there and correct."""
    global _computed, _computed_errors, _computed_source_fetch, _computed_asof
    try:
        _computed, _computed_errors, _computed_source_fetch = db.load_all_computed()
        max_computed_at = db.get_max_computed_at()
        if max_computed_at is not None:
            _computed_asof = datetime.fromtimestamp(max_computed_at, timezone.utc).isoformat(timespec="seconds")
    except Exception as e:  # noqa: BLE001 - corrupted DB, not a crash
        print(f"app: loading computed results from db failed ({e}); starting cold.")
        _computed, _computed_errors, _computed_source_fetch = [], {}, {}


_load_computed_from_db()

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

    Staleness is keyed off db.get_last_bar_date(ticker) -- the most recent
    date actually stored for this ticker -- NOT data.get_fetched_at(ticker).
    A gap-fetch attempt (data.py's warm_cache, every background-loop wake)
    always advances fetched_at, even when Yahoo returns zero new rows (e.g.
    re-checking mid-day, weekends, market closed) -- keying off fetched_at
    would treat every single wake as "new data" for every ticker and
    recompute the entire universe every 2 hours regardless of whether
    anything actually changed. last_bar_date only advances when a real new
    row was actually stored, which is the correct "did this ticker's data
    change" signal."""
    global _computed, _computed_errors, _computed_asof, _computed_source_fetch, _compute_progress

    with _compute_lock:
        prior_by_ticker = {p["ticker"]: p for p in _computed}
        prior_source_fetch = dict(_computed_source_fetch)
        prior_errors = dict(_computed_errors)

    to_compute = []
    reused_payloads: dict[str, dict] = {}
    reused_source_fetch: dict[str, str] = {}
    reused_errors: dict[str, str] = {}
    for tk in TICKERS:
        last_bar_date = db.get_last_bar_date(tk)
        if last_bar_date is not None and prior_source_fetch.get(tk) == last_bar_date:
            # Bars unchanged since this ticker was last computed -- reuse
            # whichever prior outcome it had (a payload, or a compute error).
            if tk in prior_by_ticker:
                reused_payloads[tk] = prior_by_ticker[tk]
                reused_source_fetch[tk] = last_bar_date
            elif tk in prior_errors:
                reused_errors[tk] = prior_errors[tk]
                reused_source_fetch[tk] = last_bar_date
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
            new_source_fetch = {tk: db.get_last_bar_date(tk) for tk, payload, err in results
                                 if payload is not None or err is not None}
            _computed = list(reused_payloads.values()) + [p for _, p, _ in results if p is not None]
            _computed_errors = {**reused_errors, **{t: e for t, _, e in results if e is not None}}
            _computed_source_fetch = {**reused_source_fetch, **new_source_fetch}
            _computed_asof = datetime.now(timezone.utc).isoformat(timespec="seconds")
            computed_at = time.time()
            # Only the tickers actually (re)computed this pass get written --
            # reused entries are already correct in the DB from a prior pass,
            # so this is a targeted per-ticker upsert, not a whole-table
            # rewrite (the whole point of moving off a whole-blob pickle).
            for tk, payload, err in results:
                if payload is not None or err is not None:
                    db.upsert_computed(tk, payload, new_source_fetch[tk], computed_at, err)
    finally:
        with _compute_lock:
            _compute_progress = None


def rebuild_universe() -> str | None:
    """Re-screen Yahoo and overwrite webapp/tickers.py in-process, then reload
    it into TICKERS. Runs the same screen as `python -m webapp.build_universe`
    (same env-var-driven cap/vol/price/exchange defaults) but inline, so a
    manual refresh in prod never requires SSH-ing in to run the CLI by hand.
    Best-effort: the screener API rate-limits under repeated calls, and a
    failed re-screen shouldn't block refreshing prices for the existing
    universe. Returns an error string on failure, None on success.

    Always does the real work when called -- the interval gate lives in
    the background loop (see _universe_refresh_if_needed), not here, so the
    manual Refresh button always forces a real re-screen regardless of when
    the last automatic one ran."""
    global TICKERS
    try:
        candidates = build_universe.fetch_candidates()
        passed = build_universe.screen_technicals(candidates)
        build_universe.write_tickers_file(passed)
    except Exception as e:  # noqa: BLE001 - fall back to the existing tickers.py
        return str(e) or type(e).__name__
    importlib.reload(tickers_module)
    TICKERS = tickers_module.TICKERS
    db.set_last_screened_at(time.time())
    return None


UNIVERSE_REFRESH_INTERVAL = 2 * 60 * 60  # matches data.CHECK_INTERVAL -- see refresh_architecture.md


def _universe_refresh_if_needed() -> None:
    """Automatic counterpart to the manual Refresh button -- re-screens the
    ticker universe at most once every UNIVERSE_REFRESH_INTERVAL, so the
    list of tradeable tickers doesn't go stale for hours just because nobody
    clicked Refresh, but also doesn't re-run the ~60-90s Yahoo screen on
    every background-loop wakeup if wakeups ever become more frequent than
    this interval. Errors are logged, not raised -- a failed automatic
    re-screen should never take down the price-refresh loop."""
    last_screened = db.get_last_screened_at()
    if last_screened is not None and time.time() - last_screened < UNIVERSE_REFRESH_INTERVAL:
        return
    err = rebuild_universe()
    if err:
        print(f"app: automatic universe refresh failed ({err}); keeping existing tickers.py")


def refresh_and_compute(force: bool = False) -> None:
    """Gap-fetch prices then recompute whatever changed. Called by the
    background loop on every wake, and by the manual Refresh button
    (force=True, via ?refresh=1).

    force=False (background loop): data.warm_cache() gap-fetches every
    ticker unconditionally (see data.py) -- cheap even for already-current
    tickers, so no separate staleness check gates this call itself.
    force=True (manual Refresh): every ticker's FULL history is re-fetched
    regardless of what's already stored.

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
    # See webapp/refresh_architecture.md. App load itself (the HTTP request
    # path -- GET /api/tickers etc.) never fetches or computes anything, only
    # reads whatever's already in memory/DB. This background thread is what
    # actually keeps that data fresh, on its own schedule, decoupled from any
    # request. The first pass runs immediately (not after waiting a full
    # CHECK_INTERVAL) so a cold DB doesn't sit empty for 2 hours after a
    # fresh deploy -- the frontend's loading state covers that first wait.
    # Every pass after that fires on a fixed CHECK_INTERVAL cadence, not
    # gated by any staleness check -- see data.warm_cache's docstring for why
    # gap-fetching an already-current ticker is cheap enough not to skip.
    def loop():
        while True:
            _universe_refresh_if_needed()
            refresh_and_compute()
            time.sleep(data.CHECK_INTERVAL)
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

    with _compute_lock:
        computed_count = len(_computed)

    before_gc = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    collected = gc.collect()
    after_gc = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    with db._lock:
        earnings_ticker_count = db._conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM earnings_dates").fetchone()[0]

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
        "earnings_cache_tickers": earnings_ticker_count,
        "db_file_mb": round(os.path.getsize(db.DB_PATH) / 1024 / 1024, 1) if os.path.isfile(db.DB_PATH) else 0,
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

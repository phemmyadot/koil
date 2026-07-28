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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# _compute_one() is CPU-bound, unlike data.py's network-bound FETCH_WORKERS -- a high worker count isn't free here.
COMPUTE_WORKERS = os.cpu_count() or 4

# Persistent, process-lifetime pool -- NOT recreated per compute_all() call. Same reasoning as
# data.py's _fetch_executor: yfinance's cookie-cache DB (peewee, thread-local connections) leaks
# one open FD per new OS thread that ever touches it, and a fresh ThreadPoolExecutor every call
# (every CHECK_INTERVAL, forever, via strategy_common's earnings-date lookups on a cache miss)
# meant a fresh batch of worker threads leaking FDs on every pass -- see webapp/data.py.
_compute_executor = ThreadPoolExecutor(max_workers=COMPUTE_WORKERS)

# Bump whenever _compute_one()'s payload SHAPE changes (new/renamed/moved fields, not just new tickers/data) -- forces
# compute_all() to recompute every ticker once instead of reusing an old-shaped cached payload forever just because
# that ticker's bars happened not to change since the shape changed.
PAYLOAD_SCHEMA_VERSION = 5

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles

import webapp.build_universe as build_universe
import webapp.csv_export as csv_export
import webapp.data as data
import webapp.db as db
import webapp.pdf_export as pdf_export
import webapp.prebreak as prebreak
import webapp.score as score
import webapp.tickers as tickers_module
import webapp.strategy_common as strategy_common
import webapp.strategy_vcp as strategy_vcp
import webapp.strategy_vcpo as strategy_vcpo
import webapp.strategy_vexh as strategy_vexh


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _on_startup()
    yield
    # No shutdown-side cleanup needed: the background thread is a daemon and db's connection needs no explicit close.


app = FastAPI(title="Exhaustion Dashboard", lifespan=_lifespan)

TICKERS = tickers_module.TICKERS
_computed: list[dict] = []
_computed_errors: dict[str, str] = {}
_computed_asof: str | None = None
# The last_bar_date each ticker's _computed entry was computed against, keyed off bars not a fetch timestamp.
_computed_source_fetch: dict[str, str] = {}
_compute_lock = threading.Lock()

# Serializes whole refresh_and_compute() passes (distinct from _compute_lock, which only
# guards individual dict mutations within a pass).
_refresh_pass_lock = threading.Lock()

# Serializes compute_all() itself, since it's also called directly from _on_startup(),
# bypassing _refresh_pass_lock.
_compute_pass_lock = threading.Lock()


def _load_computed_from_db() -> None:
    """Best-effort load on import; restores _computed_asof from the DB too so a warm restart isn't treated as cold."""
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

# Live progress for the current compute_all() call, if one is in flight.
_compute_progress: dict[str, int] | None = None

# Live progress for rebuild_universe()'s screen_technicals() chunk loop.
_screen_progress: dict[str, int] | None = None


def compute_progress() -> dict[str, int] | None:
    with _compute_lock:
        return dict(_compute_progress) if _compute_progress is not None else None


def screen_progress() -> dict[str, int] | None:
    with _compute_lock:
        return dict(_screen_progress) if _screen_progress is not None else None

_STRATEGY_MODULES = {"vexh": strategy_vexh, "strategy_vcp": strategy_vcp, "strategy_vcpo": strategy_vcpo}


def _eval_strategy(module, ticker: str, bars, ind: dict | None) -> dict | None:
    """Independently error-isolated -- one strategy failing on a ticker shouldn't drop the others."""
    try:
        return module.evaluate(ticker, bars, ind=ind)
    except Exception:  # noqa: BLE001
        return None


def _compute_one(ticker: str) -> tuple[str, dict | None, str | None]:
    bars = data.get_bars(ticker)
    if bars is None:
        return ticker, None, data.get_error(ticker) or "no data"
    try:
        if bars.empty:
            raise ValueError("no data")
        # VCP/VCPO need identical ATR/EMA/resistance -- compute once, share the dict (~17ms/ticker saved).
        # VEXH computes its own indicators (different set entirely), so it gets ind=None.
        try:
            shared_ind = strategy_vcp.compute_indicators(bars)
        except Exception:  # noqa: BLE001
            shared_ind = None

        payload = {
            "ticker": ticker,
            "price": round(float(bars.Close.iloc[-1]), 4),
            "date": str(bars.index[-1].date()),
        }
        for key, module in _STRATEGY_MODULES.items():
            ind = shared_ind if key != "vexh" else None
            payload[key] = _eval_strategy(module, ticker, bars, ind)
        # earnings_risk mirrors any one strategy's own earnings-flagged bars -- flagging is
        # identical across strategies (strategy_common.with_earnings_flags()), so VEXH's
        # result (if it succeeded) is as good a source as any other.
        vexh_result = payload.get("vexh")
        if vexh_result is not None:
            df = strategy_common.with_earnings_flags(bars, ticker)
            payload["earnings_risk"] = bool(df["EarningsWithinAvoidWindow"].iloc[-1])
        else:
            payload["earnings_risk"] = None
        if all(payload[key] is None for key in _STRATEGY_MODULES):
            raise ValueError("insufficient history")
        # Ticker-level, not strategy-specific -- same as a Pine indicator overlaying any strategy's chart.
        try:
            payload["prebreak"] = prebreak.evaluate(ticker, bars)
        except Exception:  # noqa: BLE001
            payload["prebreak"] = None
        # "score" is VEXH's legacy 6-gate count; setup_score is the 0-10 composite, keyed per strategy.
        payload["setup_score"] = {}
        for strat_key in _STRATEGY_MODULES:
            try:
                payload["setup_score"][strat_key] = score.compute_score(payload, strat_key)
            except Exception:  # noqa: BLE001
                payload["setup_score"][strat_key] = None
        payload["_schema_version"] = PAYLOAD_SCHEMA_VERSION
        return ticker, payload, None
    except Exception as e:  # noqa: BLE001 - per-ticker failures must not break the page
        return ticker, None, str(e) or type(e).__name__


def _active_tickers() -> list[str]:
    """TICKERS (the screened universe) plus any ticker a client has ever reported as
    watchlisted -- a watchlisted ticker that later fails re-screening must keep being
    fetched/computed, or its saved watchlist row silently goes stale forever."""
    watchlisted = db.get_watchlist_tickers()
    seen = set(TICKERS)
    return TICKERS + [tk for tk in watchlisted if tk not in seen]


def compute_all() -> None:
    """Recompute only tickers whose bars changed since the last pass; staleness keyed off last_bar_date, not fetch time.
    Never runs two passes concurrently -- see _compute_pass_lock."""
    global _computed, _computed_errors, _computed_asof, _computed_source_fetch, _compute_progress

    if not _compute_pass_lock.acquire(blocking=False):
        print("app: compute_all() already running -- skipping this overlapping call.")
        return
    try:
        with _compute_lock:
            prior_by_ticker = {p["ticker"]: p for p in _computed}
            prior_source_fetch = dict(_computed_source_fetch)
            prior_errors = dict(_computed_errors)

        to_compute = []
        reused_payloads: dict[str, dict] = {}
        reused_source_fetch: dict[str, str] = {}
        reused_errors: dict[str, str] = {}
        for tk in _active_tickers():
            last_bar_date = db.get_last_bar_date(tk)
            prior_payload = prior_by_ticker.get(tk)
            # Bars unchanged AND the cached payload's shape is current -- otherwise force a recompute even
            # though bars didn't change, so a payload-shape change (PAYLOAD_SCHEMA_VERSION bump) can't leave
            # old-shaped entries frozen in the cache forever just because that ticker's bars happen to be stable.
            shape_current = prior_payload is None or prior_payload.get("_schema_version") == PAYLOAD_SCHEMA_VERSION
            if last_bar_date is not None and prior_source_fetch.get(tk) == last_bar_date and shape_current:
                if tk in prior_by_ticker:
                    reused_payloads[tk] = prior_payload
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
                futures = [_compute_executor.submit(_compute_one, tk) for tk in to_compute]
                for future in as_completed(futures):
                    results.append(future.result())
                    with _compute_lock:
                        _compute_progress["done"] += 1
            with _compute_lock:
                # Lock-ordering invariant: _compute_lock is always acquired before db._lock, never the reverse.
                new_source_fetch = {tk: db.get_last_bar_date(tk) for tk, payload, err in results
                                     if payload is not None or err is not None}
                _computed = list(reused_payloads.values()) + [p for _, p, _ in results if p is not None]
                _computed_errors = {**reused_errors, **{t: e for t, _, e in results if e is not None}}
                _computed_source_fetch = {**reused_source_fetch, **new_source_fetch}
                _computed_asof = datetime.now(timezone.utc).isoformat(timespec="seconds")
                computed_at = time.time()
                # Only tickers actually (re)computed this pass get written; one bad DB write must not abort the pass.
                for tk, payload, err in results:
                    if payload is not None or err is not None:
                        source_bar_date = new_source_fetch.get(tk)
                        if source_bar_date is None:
                            print(f"app: skipping DB persist for {tk} -- no last_bar_date "
                                  f"available (bars missing or not yet recorded); will retry next pass.")
                            continue
                        try:
                            db.upsert_computed(tk, payload, source_bar_date, computed_at, err)
                        except Exception as e:  # noqa: BLE001
                            print(f"app: db.upsert_computed failed for {tk} ({e}); "
                                  f"continuing with the rest of this pass.")
        finally:
            with _compute_lock:
                _compute_progress = None
    finally:
        _compute_pass_lock.release()


def rebuild_universe() -> str | None:
    """Re-screen Yahoo and overwrite webapp/tickers.py in-process; returns an error string on failure, None on success."""
    global TICKERS, _screen_progress

    def _on_progress(done: int, total: int) -> None:
        # A nested function needs its own `global` declaration, separate from the enclosing function's.
        global _screen_progress
        with _compute_lock:
            _screen_progress = {"done": done, "total": total}

    try:
        candidates = build_universe.fetch_candidates()
        with _compute_lock:
            _screen_progress = {"done": 0, "total": len(candidates)}
        passed = build_universe.screen_technicals(candidates, on_progress=_on_progress)
        build_universe.write_tickers_file(passed)
    except Exception as e:  # noqa: BLE001 - fall back to the existing tickers.py
        return str(e) or type(e).__name__
    finally:
        with _compute_lock:
            _screen_progress = None
    importlib.reload(tickers_module)
    TICKERS = tickers_module.TICKERS
    db.set_last_screened_at(time.time())
    return None


UNIVERSE_REFRESH_INTERVAL = 2 * 60 * 60  # matches data.CHECK_INTERVAL -- see refresh_architecture.md


def _universe_refresh_if_needed() -> None:
    """Automatic counterpart to the manual Refresh button, re-screening at most once every UNIVERSE_REFRESH_INTERVAL."""
    last_screened = db.get_last_screened_at()
    if last_screened is not None and time.time() - last_screened < UNIVERSE_REFRESH_INTERVAL:
        return
    err = rebuild_universe()
    if err:
        print(f"app: automatic universe refresh failed ({err}); keeping existing tickers.py")


def refresh_and_compute(force: bool = False) -> None:
    """Gap-fetch prices then recompute whatever changed; force=True re-fetches everything (manual Refresh).
    Never runs two passes concurrently -- see _refresh_pass_lock."""
    if not _refresh_pass_lock.acquire(blocking=False):
        print("app: refresh_and_compute() already running -- skipping this overlapping call.")
        return
    try:
        active = _active_tickers()
        fetch_time_before = data.last_fetch_time()
        data.warm_cache(active, force=force)
        fetch_time_after = data.last_fetch_time()

        with _compute_lock:
            computed_count = len(_computed)
        compute_caught_up = computed_count >= len(active) * 0.9  # allow for a few per-ticker errors

        if not force and fetch_time_before == fetch_time_after and compute_caught_up:
            print(f"app: nothing fetched this pass and compute is already caught up "
                  f"({computed_count}/{len(TICKERS)}) -- skipping compute_all()'s per-ticker check entirely.")
            return
        compute_all()
    finally:
        _refresh_pass_lock.release()


def _on_startup():
    # Must return immediately -- everything below runs in the background thread, never inline before uvicorn's yield.
    def loop():
        # Two independent existence checks: empty bars means fetch+compute everything; bars with no computed_results
        # means compute_all() alone is enough.
        if not db.has_any_bars():
            print("app: DB is empty (no bars at all) -- running an eager fetch+compute.")
            try:
                _universe_refresh_if_needed()
                refresh_and_compute()
            except Exception as e:  # noqa: BLE001 - the loop below still starts either way
                print(f"app: eager startup fetch+compute failed ({e}); will retry on the normal cadence.")
        elif not db.has_any_computed():
            print("app: bars exist but computed_results is empty -- running compute_all() immediately.")
            try:
                compute_all()
            except Exception as e:  # noqa: BLE001 - the loop below still starts either way
                print(f"app: eager startup compute_all() failed ({e}); will retry on the normal cadence.")

        while True:
            time.sleep(data.CHECK_INTERVAL)
            try:
                _universe_refresh_if_needed()
                refresh_and_compute()
            except Exception as e:  # noqa: BLE001 - one bad pass must not permanently kill the refresh loop
                print(f"app: background refresh loop pass failed ({e}); will retry next cycle instead of stopping.")
    threading.Thread(target=loop, daemon=True).start()


@app.get("/api/meta")
def meta():
    return {
        "total_tickers": len(TICKERS),
        "last_fetch": data.last_fetch_time(),
        # Non-null only while rebuild_universe()'s screening is actively running (earliest phase of a Refresh).
        "screen_progress": screen_progress(),
        # Non-null only while a warm_cache() fetch is actively in flight.
        "fetch_progress": data.fetch_progress(),
        # Non-null only while compute_all() is actively running; never overlaps fetch_progress.
        "compute_progress": compute_progress(),
    }


@app.get("/api/debug/memory")
def debug_memory():
    """Live process memory breakdown; not secured, fine for a LAN-only box with no public route to this path."""
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
        "tickers": computed_snapshot,
        "errors": errors,
        "universe_error": universe_error,
    }


@app.post("/api/watchlist-tickers")
def sync_watchlist_tickers(tickers: list[str]):
    """Client reports the full set of tickers currently on any saved watchlist (watchlists
    themselves stay in the browser's localStorage -- this just tells the background fetch/
    compute loop which extra tickers to keep alive so one that later fails universe
    re-screening doesn't silently go stale). Called by watchlist.html on load/save."""
    db.set_watchlist_tickers(sorted(set(tickers)), time.time())
    return {"ok": True}


_EXPORT_STRATEGY_KEYS = {"vexh", "strategy_vcp", "strategy_vcpo"}


def _resolve_export_payloads(tickers: list[str]) -> list[dict]:
    with _compute_lock:
        by_ticker = {p["ticker"]: p for p in _computed}
    return [by_ticker[tk] for tk in tickers if tk in by_ticker]


def _resolve_export_strategy(body: dict) -> str:
    """body.strategy must be one of the 3 real payload keys -- matches the dashboard's
    Advance Filter strategy selector (ADV_STRAT_KEY in index.html), so the export only
    covers whichever strategy the user is currently looking at, not all three."""
    strategy = body.get("strategy")
    if strategy not in _EXPORT_STRATEGY_KEYS:
        raise HTTPException(status_code=400, detail=f"invalid strategy: {strategy!r}")
    return strategy


@app.post("/api/export/pdf")
def export_pdf(body: dict):
    """PDF export for a user-selected subset of tickers, scoped to one strategy, built
    entirely from already-computed payloads. body: {tickers: [...], strategy: "vexh"|
    "strategy_vcp"|"strategy_vcpo", timezone: <IANA name, e.g. "America/New_York">} -- the
    timezone is the browser's own (Intl.DateTimeFormat().resolvedOptions().timeZone), so the
    "Generated ..." header and the download filename's date reflect the exporting user's local
    date/time, not the server's. Falls back to UTC if missing or not a real IANA name."""
    strategy = _resolve_export_strategy(body)
    tz_name = body.get("timezone")
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    generated_at_local = datetime.now(tz)

    payloads = _resolve_export_payloads(body.get("tickers", []))
    pdf_bytes = pdf_export.build_pdf(payloads, strategy, generated_at_local=generated_at_local)
    filename = f"exhaustion-export-{strategy}-{generated_at_local.strftime('%Y-%m-%d')}.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                     headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.post("/api/export/csv")
def export_csv(body: dict):
    """CSV export for a user-selected subset of tickers, scoped to one strategy -- same
    scope/body shape as /api/export/pdf (see _resolve_export_strategy), one row per ticker."""
    strategy = _resolve_export_strategy(body)
    tz_name = body.get("timezone")
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    local_date = datetime.now(tz).strftime("%Y-%m-%d")

    payloads = _resolve_export_payloads(body.get("tickers", []))
    csv_text = csv_export.build_csv(payloads, strategy)
    filename = f"exhaustion-export-{strategy}-{local_date}.csv"
    return Response(content=csv_text, media_type="text/csv",
                     headers={"Content-Disposition": f"attachment; filename={filename}"})


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"),
                           html=True), name="static")

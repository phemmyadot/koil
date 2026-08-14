"""Exhaustion dashboard backend.

Run from the project root:
    .\\.venv\\Scripts\\python.exe -m uvicorn backend.app:app --port 8123

Architecture: fetch and compute are decoupled from the request path entirely
-- see backend/refresh_architecture.md for the full rules. A page request is
always just a fast in-memory read -- no network call, no per-request
backtest run, regardless of how stale or fresh the data happens to be.

The refresh cycle (see backend/SCREENING_FETCH_REFACTOR.md), run by
refresh_and_compute():
  1. Fetch candidate tickers (Yahoo screener) -> save to DB (candidate_tickers).
  2. Pull each candidate's price data -> save to DB (backend/data.py, incremental
     gap-fetch if already stored, full history if new).
  3. Run the technical entry-condition filter once, over all candidates
     (backend/build_universe.py's passes_technical_filters). For each ticker
     that passes: compute only if its bars checksum changed since last
     compute -> save results to DB.

Runs when: no computed data exists yet (cold start, blocks until done);
the user clicks Refresh; the background loop wakes -- on a market-hours-aware cadence, see
docs/superpowers/specs/2026-08-03-market-hours-background-fetch-design.md
(MARKET_OPEN_FETCH_INTERVAL_SECONDS while open, once per close period while closed).
A plain page load with existing computed data just reads it -- no cycle.
"""
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

# _compute_one() is CPU-bound, unlike data.py's network-bound FETCH_WORKERS -- a high worker count isn't free here.
COMPUTE_WORKERS = os.cpu_count() or 4

# Persistent, process-lifetime pool -- NOT recreated per compute_all() call. Same reasoning as
# data.py's _fetch_executor: yfinance's cookie-cache DB (peewee, thread-local connections) leaks
# one open FD per new OS thread that ever touches it, and a fresh ThreadPoolExecutor every call
# (every CHECK_INTERVAL, forever, via strategy_common's earnings-date lookups on a cache miss)
# meant a fresh batch of worker threads leaking FDs on every pass -- see backend/data.py.
_compute_executor = ThreadPoolExecutor(max_workers=COMPUTE_WORKERS)

# Bump whenever _compute_one()'s payload SHAPE changes (new/renamed/moved fields, not just new tickers/data) -- forces
# compute_all() to recompute every ticker once instead of reusing an old-shaped cached payload forever just because
# that ticker's bars happened not to change since the shape changed.
PAYLOAD_SCHEMA_VERSION = 7

# No real auth/accounts yet -- every user-scoped table (daily review chatbot, see
# docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md) is written and read
# under this one hardcoded id. Swapping this for a resolved current_user.id, once real multi-user
# auth lands (docs/superpowers/specs/2026-08-04-multi-user-trades-design.md), is the only change
# needed -- the schema, queries, and retrieval logic don't change shape.
DEFAULT_USER_ID = 1

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from yfinance.exceptions import YFRateLimitError

import backend.build_universe as build_universe
import backend.data as data
import backend.db as db
import backend.entry_estimate as entry_estimate
import backend.market_hours as market_hours
import backend.options_pricing as options_pricing
import backend.push as push
import backend.quality_filter as quality_filter
import backend.review_claude as review_claude
import backend.review_ingest as review_ingest
import backend.review_stream as review_stream
import backend.support_resistance as support_resistance
import backend.prebreak as prebreak
import backend.score as score
import backend.strategy_common as strategy_common
import backend.strategy_vcp as strategy_vcp
import backend.strategy_vcpo as strategy_vcpo
import backend.strategy_vexh as strategy_vexh

# See docs/superpowers/specs/2026-08-03-market-hours-background-fetch-design.md. Default (120
# min) matches CHECK_INTERVAL's existing 2-hour cadence exactly -- this is a drop-in replacement
# for the always-on interval during market hours, not a behavior change until tuned down.
MARKET_OPEN_FETCH_INTERVAL_SECONDS = int(os.environ.get("MARKET_OPEN_FETCH_INTERVAL_MINUTES", 120)) * 60
# How often the loop wakes just to check "is it time yet" while the market is closed -- cheap
# (local clock + one DB read, no network call), so this can be short without cost concern.
CLOSED_MARKET_POLL_SECONDS = 5 * 60


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _on_startup()
    yield
    # No shutdown-side cleanup needed: the background thread is a daemon and db's connection needs no explicit close.


app = FastAPI(title="Exhaustion Dashboard", lifespan=_lifespan)

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

RATE_LIMIT_BACKOFF = 20 * 60
_rate_limited_until: float | None = None


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


def compute_progress() -> dict[str, int] | None:
    with _compute_lock:
        return dict(_compute_progress) if _compute_progress is not None else None

_STRATEGY_MODULES = {"vexh": strategy_vexh, "strategy_vcp": strategy_vcp, "strategy_vcpo": strategy_vcpo}
_STRATEGY_LABELS = {"vexh": "VEXH", "strategy_vcp": "VCP", "strategy_vcpo": "VCPO"}

# See docs/superpowers/specs/2026-08-04-strategy-signal-transition-notifications-design.md.
# Collapses each strategy's own `verdict` string down to the entry-side states the dashboard
# card shows (NO SIGNAL -> PENDING -> OPEN) -- TAKE/SKIP both mean "signal fired, not yet in the
# strategy's own simulated position," i.e. PENDING. TP HIT is deliberately NOT mapped here (left
# untracked/None) -- a real trade's actual TP/stop progress already alerts via
# _fire_threshold_alerts()/_update_trade_marks_and_alerts() below, scoped to the user's real
# positions; tracking the strategy's own simulated TP here too would be a redundant, noisier
# duplicate of that.
_VERDICT_TO_ENTRY_STATE = {
    "NO SIGNAL": "NO SIGNAL",
    "TAKE": "PENDING",
    "SKIP": "PENDING",
    "IN TRADE": "OPEN",
}


def _strategy_entry_state(strat_payload: dict | None) -> str | None:
    if strat_payload is None:
        return None
    return _VERDICT_TO_ENTRY_STATE.get(strat_payload.get("verdict"))


# strat_key is a wire key (e.g. "strategy_vcpo") -- compare against the wire-key form of
# quality_filter.DEFAULT_FILTER["strategies"].
_DEFAULT_FILTER_WIRE_STRATEGIES = {
    quality_filter.STRATEGY_WIRE_KEY[s] for s in quality_filter.DEFAULT_FILTER["strategies"]
}


def _passes_alert_quality_bar(payload: dict | None, strat_key: str, strat_payload: dict | None) -> bool:
    if strat_key not in _DEFAULT_FILTER_WIRE_STRATEGIES:
        return False
    return quality_filter.passes_default_filter(payload, strat_payload)


def _fire_strategy_state_alert(ticker: str, strat_key: str, prior_state: str, new_state: str, now_iso: str) -> None:
    label = _STRATEGY_LABELS.get(strat_key, strat_key)
    # A transition INTO "NO SIGNAL" means two different things depending on where it came from --
    # OPEN->NO SIGNAL is a real exit; PENDING->NO SIGNAL is a signal that expired before ever
    # being entered. Only the former is an "EXIT."
    if new_state == "NO SIGNAL" and prior_state == "OPEN":
        display_state = "EXIT"
    elif new_state == "NO SIGNAL" and prior_state == "PENDING":
        display_state = "NO SIGNAL (pending signal expired)"
    else:
        display_state = new_state
    message = f"{ticker} — {label} is now {display_state}"
    db.insert_notification(None, "strategy_state", None, message, now_iso)
    payload = json.dumps({"title": f"{ticker} — {label}", "body": message, "ticker": ticker})
    push.send_push_to_all(payload, now_iso)


SCORE_ALERT_THRESHOLD = 9


def _fire_score_alert(ticker: str, strat_key: str, prior_payload: dict | None, new_payload: dict | None, now_iso: str) -> None:
    """setup_score reaches 9+/10, 🟡 -- an in-memory prior-vs-new diff, same shape as
    _fire_strategy_state_alert's verdict-transition check, just keyed off setup_score instead of
    verdict. prior_payload/new_payload are the TICKER-level payloads (setup_score is a per-ticker
    dict keyed by strategy, not part of the per-strategy payload) -- fires once per ticker+strategy
    on the crossing, not every cycle it stays >=9, no separate dedup column needed since
    prior_payload's own setup_score already carries last cycle's value for the comparison.

    prior_payload is None the first time a ticker is EVER computed (cold start, first deploy,
    cache miss) -- requires a REAL prior score to compare against, or every ticker already
    scoring >=9 on its first compute would fire a false "just crossed the threshold" alert."""
    if new_payload is None or prior_payload is None:
        return
    new_score = new_payload.get("setup_score", {}).get(strat_key)
    if new_score is None or new_score < SCORE_ALERT_THRESHOLD:
        return
    prior_score = prior_payload.get("setup_score", {}).get(strat_key)
    if prior_score is None or prior_score >= SCORE_ALERT_THRESHOLD:
        return
    label = _STRATEGY_LABELS.get(strat_key, strat_key)
    message = f"{ticker} — {label} setup score reached {new_score}/10 — top setup in universe"
    db.insert_notification(None, "score_high", float(new_score), message, now_iso)
    push_payload = json.dumps({"title": f"🟡 {ticker} — {label} score {new_score}", "body": message, "ticker": ticker})
    push.send_push_to_all(push_payload, now_iso)


def _eval_strategy(module, ticker: str, bars, ind: dict | None) -> tuple[dict | None, Exception | None]:
    """Independently error-isolated -- one strategy failing on a ticker shouldn't drop the
    others. Returns the exception alongside None so the caller can report what actually broke
    (e.g. a DB IntegrityError) instead of every failure reading as "insufficient history"."""
    try:
        return module.evaluate(ticker, bars, ind=ind), None
    except Exception as e:  # noqa: BLE001
        return None, e


def _compute_one(ticker: str) -> tuple[str, dict | None, str | None, str | None]:
    bars = data.get_bars(ticker)
    if bars is None:
        return ticker, None, data.get_error(ticker) or "no data", None
    try:
        if bars.empty:
            raise ValueError("no data")
        # Read from the DB here (not derived from the in-memory bars object) so the checksum
        # matches exactly what db.get_bars_checksum() will compare against on the next pass.
        checksum = db.get_bars_checksum(ticker)
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
        # Ticker-level, not strategy-specific -- same as a Pine indicator overlaying any
        # strategy's chart. Computed BEFORE the strategies below and never discarded by their
        # failure: prebreak has no dependency on _STRATEGY_MODULES or the earnings-date cache,
        # so a ticker whose strategies fail (e.g. the earnings_dates race below, now fixed, but
        # any other per-strategy failure too) still keeps its correctly-computed prebreak state
        # instead of the whole payload being thrown away. This matters most for an open
        # position's or watchlisted ticker's prebreak (see _active_tickers/always_include),
        # since those must always compute -- a strategy-only failure shouldn't blank them.
        try:
            payload["prebreak"] = prebreak.evaluate(ticker, bars)
            payload["prebreak"]["last_7_close"] = [round(float(c), 4) for c in bars.Close.tail(7)]
        except Exception:  # noqa: BLE001
            payload["prebreak"] = None
        strategy_errors: dict[str, Exception] = {}
        for key, module in _STRATEGY_MODULES.items():
            ind = shared_ind if key != "vexh" else None
            payload[key], err = _eval_strategy(module, ticker, bars, ind)
            if err is not None:
                strategy_errors[key] = err
        # earnings_risk mirrors any one strategy's own earnings-flagged bars -- flagging is
        # identical across strategies (strategy_common.with_earnings_flags()), so VEXH's
        # result (if it succeeded) is as good a source as any other.
        vexh_result = payload.get("vexh")
        if vexh_result is not None:
            df = strategy_common.with_earnings_flags(bars, ticker)
            payload["earnings_risk"] = bool(df["EarningsWithinAvoidWindow"].iloc[-1])
            # Card-facing countdown (21-day window, same source as earnings_risk above) --
            # display only, does not feed score.py's own earnings_risk-gated scoring dimension.
            payload["days_to_earnings"] = strategy_common.days_to_earnings(ticker, df.index[-1])
        else:
            payload["earnings_risk"] = None
            payload["days_to_earnings"] = None
        # Only bail entirely when there's truly nothing usable (no strategy AND no prebreak) --
        # a ticker with prebreak but no strategy data still gets a real payload instead of being
        # discarded. If any strategy raised a real exception, report that instead of the generic
        # "insufficient history" -- the two failure modes look identical from here otherwise.
        if all(payload[key] is None for key in _STRATEGY_MODULES) and payload["prebreak"] is None:
            if strategy_errors:
                raise next(iter(strategy_errors.values()))
            raise ValueError("insufficient history")
        # "score" is VEXH's legacy 6-gate count; setup_score is the 0-10 composite, keyed per strategy.
        payload["setup_score"] = {}
        for strat_key in _STRATEGY_MODULES:
            try:
                payload["setup_score"][strat_key] = score.compute_score(payload, strat_key)
            except Exception:  # noqa: BLE001
                payload["setup_score"][strat_key] = None
        payload["_schema_version"] = PAYLOAD_SCHEMA_VERSION
        return ticker, payload, None, checksum
    except Exception as e:  # noqa: BLE001 - per-ticker failures must not break the page
        return ticker, None, str(e) or type(e).__name__, checksum


# Index/commodity symbols for the daily review's Market Context section (see
# docs/superpowers/specs/2026-08-04-daily-review-format-template.md) -- fetched the same way as
# Index/commodity ETF proxies for the daily review's Market Context section -- fetched via the
# same universe cycle as any watchlisted/traded ticker; they fail passes_technical_filters()
# (built for stocks) so are fetched only, never scored.
CONTEXT_TICKERS = ["SPY", "QQQ", "DIA", "^TNX", "USO"]


def _active_tickers() -> list[str]:
    """Candidate tickers (from the DB table, not a live Yahoo screener call) plus any
    watchlisted ticker, any open position's ticker, and CONTEXT_TICKERS -- all must keep being
    fetched even if they fail the technical filter, or they silently go stale forever."""
    candidates = db.get_candidate_tickers()
    watchlisted = db.get_watchlist_tickers()
    traded = [p["ticker"] for p in db.list_positions("open")]
    seen = set(candidates)
    extra = []
    for tk in watchlisted + traded + CONTEXT_TICKERS:
        if tk not in seen:
            extra.append(tk)
            seen.add(tk)
    return candidates + extra


def compute_all(force: bool = False) -> None:
    """Runs the technical filter once, up front, over every active ticker's stored bars --
    membership in the universe is decided here, a single time, not per ticker on each reuse
    check (see backend/SCREENING_FETCH_REFACTOR.md). Tickers that pass are then recomputed
    only if their bars checksum (db.get_bars_checksum(), a hash of every stored bar) changed
    since the last pass; force=True (manual Refresh) skips the reuse check entirely and
    recomputes every filtered ticker. Never runs two passes concurrently -- see
    _compute_pass_lock."""
    global _computed, _computed_errors, _computed_asof, _computed_source_fetch, _compute_progress

    if not _compute_pass_lock.acquire(blocking=False):
        print("app: compute_all() already running -- skipping this overlapping call.")
        return
    try:
        with _compute_lock:
            prior_by_ticker = {p["ticker"]: p for p in _computed}
            prior_source_fetch = dict(_computed_source_fetch)
            prior_errors = dict(_computed_errors)

        always_include = set(db.get_watchlist_tickers()) | {p["ticker"] for p in db.list_positions("open")}

        filtered_tickers = []
        for tk in _active_tickers():
            bars = data.get_bars(tk)
            has_data = bars is not None and not bars.empty
            if tk in always_include:
                passes = has_data
            else:
                try:
                    passes = has_data and build_universe.passes_technical_filters(bars)
                except Exception:  # noqa: BLE001 - a bad filter eval must not drop the ticker's error state
                    passes = False
            if passes:
                filtered_tickers.append(tk)

        to_compute = []
        reused_payloads: dict[str, dict] = {}
        reused_source_fetch: dict[str, str] = {}
        reused_errors: dict[str, str] = {}
        for tk in filtered_tickers:
            checksum = db.get_bars_checksum(tk)
            prior_payload = prior_by_ticker.get(tk)
            # Bars unchanged AND the cached payload's shape is current -- otherwise force a recompute even
            # though bars didn't change, so a payload-shape change (PAYLOAD_SCHEMA_VERSION bump) can't leave
            # old-shaped entries frozen in the cache forever just because that ticker's bars happen to be stable.
            shape_current = prior_payload is None or prior_payload.get("_schema_version") == PAYLOAD_SCHEMA_VERSION
            if not force and checksum is not None and prior_source_fetch.get(tk) == checksum and shape_current:
                if tk in prior_by_ticker:
                    reused_payloads[tk] = prior_payload
                    reused_source_fetch[tk] = checksum
                elif tk in prior_errors:
                    reused_errors[tk] = prior_errors[tk]
                    reused_source_fetch[tk] = checksum
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
                # checksum comes from _compute_one() itself (read from the DB right before it
                # computed), not a fresh DB read here -- a fresh read could observe bars a
                # concurrent fetch already moved past what this payload was computed from.
                new_source_fetch = {tk: fp for tk, payload, err, fp in results
                                     if payload is not None or err is not None}
                _computed = list(reused_payloads.values()) + [p for _, p, _, _ in results if p is not None]
                _computed_errors = {**reused_errors, **{t: e for t, _, e, _ in results if e is not None}}
                _computed_source_fetch = {**reused_source_fetch, **new_source_fetch}
                _computed_asof = datetime.now(timezone.utc).isoformat(timespec="seconds")
                computed_at = time.time()
                # Only tickers actually (re)computed this pass get written; one bad DB write must not abort the pass.
                for tk, payload, err, fp in results:
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
                    if payload is not None:
                        prior_payload = prior_by_ticker.get(tk)
                        for strat_key in _STRATEGY_MODULES:
                            strat_payload = payload.get(strat_key)
                            if not _passes_alert_quality_bar(payload, strat_key, strat_payload):
                                continue
                            prior_state = _strategy_entry_state((prior_payload or {}).get(strat_key))
                            new_state = _strategy_entry_state(strat_payload)
                            if prior_state is not None and new_state is not None and new_state != prior_state:
                                try:
                                    _fire_strategy_state_alert(tk, strat_key, prior_state, new_state, _computed_asof)
                                except Exception as e:  # noqa: BLE001 - one bad alert must not break the pass
                                    print(f"app: strategy state alert failed for {tk}/{strat_key} ({e}).")
                            try:
                                _fire_score_alert(tk, strat_key, prior_payload, payload, _computed_asof)
                            except Exception as e:  # noqa: BLE001
                                print(f"app: score alert failed for {tk}/{strat_key} ({e}).")
        finally:
            with _compute_lock:
                _compute_progress = None
    finally:
        _compute_pass_lock.release()


TP_STOP_ALERT_THRESHOLDS = [30, 50, 70, 80, 90, 95]
OPTION_GAIN_ALERT_THRESHOLDS = [50, 75, 90, 100]


def replay_fills(fills: list[dict], as_of_date: str | None = None) -> dict:
    """Weighted-average-cost replay of a position's fills -- see
    docs/superpowers/specs/2026-07-31-position-fills-design.md for the full derivation and a
    worked example. Fills are processed in fill_date order; if as_of_date is given, only fills
    on or before that date are replayed (for the daily-mark / historical-as-of-day case),
    otherwise all fills are used (for "current state").

    Returns: {units_remaining, avg_cost, realized_pnl, instrument, contracts_per_unit (100 for
    options, 1 for spot -- the multiplier realized/unrealized $ P&L needs), open_option_fills
    (entry fills still holding remaining units, oldest-instrument-detail first, needed by the
    expiry auto-close check since each entry fill carries its own strike/expiry/iv)}.

    price on every fill is ALWAYS the underlying stock price (see trade-tracking-design.md's
    stock-price convention) -- for options, the $ cost/proceeds use each fill's OWN premium
    field, not the stock price, since premium is what was actually paid/received."""
    ordered = sorted(fills, key=lambda f: (f["fill_date"], f["id"]))
    if as_of_date is not None:
        ordered = [f for f in ordered if f["fill_date"] <= as_of_date]

    running_units = 0.0
    running_cost = 0.0
    realized_pnl = 0.0
    instrument = ordered[0]["instrument"] if ordered else "spot"
    multiplier = 100 if instrument == "option" else 1
    # FIFO consumption of entry units purely to track which entry fills (and thus which
    # strike/expiry/iv) still have units outstanding -- NOT used for cost basis (that's
    # weighted-average, see above), only for the expiry auto-close check, which needs to know
    # a specific option fill's own expiry_date, not a position-wide blended one.
    open_lots: list[dict] = []  # [{fill, units_remaining}], oldest entry first
    # Weighted-average cost is fixed at each entry and doesn't change as units get sold off --
    # so the last computable avg_cost (right before the final exit fully closes the position) is
    # still the right number to report after closing, not None. Tracked separately from the
    # running_cost/running_units below, which legitimately hit 0/0 once nothing remains open.
    last_avg_cost: float | None = None

    for f in ordered:
        fill_value = f["premium"] if instrument == "option" else f["price"]
        if f["kind"] == "entry":
            running_cost += fill_value * f["units"] * multiplier
            running_units += f["units"]
            open_lots.append({"fill": f, "units_remaining": f["units"]})
            last_avg_cost = running_cost / running_units
        elif f["kind"] == "exit":
            if running_units <= 0:
                continue  # malformed data guard -- an exit with nothing open, ignore rather than divide by zero
            avg_cost = running_cost / running_units
            exit_value = f["premium"] if instrument == "option" else f["price"]
            realized_pnl += (exit_value * multiplier - avg_cost) * f["units"]
            running_cost -= avg_cost * f["units"]
            running_units -= f["units"]
            remaining_to_consume = f["units"]
            for lot in open_lots:
                if remaining_to_consume <= 0:
                    break
                consumed = min(lot["units_remaining"], remaining_to_consume)
                lot["units_remaining"] -= consumed
                remaining_to_consume -= consumed

    avg_cost = (running_cost / running_units) if running_units > 0 else last_avg_cost
    open_lots = [lot for lot in open_lots if lot["units_remaining"] > 0]
    return {
        "units_remaining": running_units,
        "avg_cost": avg_cost,
        "realized_pnl": realized_pnl,
        "instrument": instrument,
        "multiplier": multiplier,
        # {fill, units_remaining} per still-open entry fill -- lets the expiry auto-close check
        # know exactly how many units of THAT SPECIFIC fill's strike/expiry/iv remain, since
        # cost basis is blended (weighted-average) but expiry is necessarily per-fill.
        "open_lots": open_lots,
    }


def _position_pct_to_tp_stop(state: dict, tp: float, stop: float, current_price: float) -> tuple[float, float] | None:
    """Shared by _update_trade_marks_and_alerts() (real-time alerting) and
    review_snapshot.build_daily_snapshot() (the AI review's compact state summary) -- extracted
    so the same TP/stop-progress math isn't duplicated. Returns (pct_to_tp, pct_to_stop), or None
    if an option position has no priced lots to derive a target from (missing iv_at_entry)."""
    avg_cost = state["avg_cost"]
    if state["instrument"] == "option":
        today = datetime.now(timezone.utc).date()
        tp_value = _blended_option_value(state["open_lots"], tp, today)
        stop_value = _blended_option_value(state["open_lots"], stop, today)
        compare_value = _blended_option_value(state["open_lots"], current_price, today)
        if tp_value is None or stop_value is None or compare_value is None:
            return None
        multiplier = state["multiplier"]
        tp_value *= multiplier
        stop_value *= multiplier
        compare_value *= multiplier
        pct_to_tp = (compare_value - avg_cost) / (tp_value - avg_cost) * 100 if tp_value != avg_cost else 0
        pct_to_stop = (avg_cost - compare_value) / (avg_cost - stop_value) * 100 if stop_value != avg_cost else 0
    else:
        pct_to_tp = (current_price - avg_cost) / (tp - avg_cost) * 100 if tp != avg_cost else 0
        pct_to_stop = (avg_cost - current_price) / (avg_cost - stop) * 100 if stop != avg_cost else 0
    return pct_to_tp, pct_to_stop


def _update_trade_marks_and_alerts() -> None:
    """Runs after every compute_all() pass (scheduled 2h wake or manual refresh, same code
    path -- see refresh_and_compute()). For every open position: upsert today's daily mark
    from the ticker's already-computed price/date (no new Yahoo call), auto-close any option
    fills that have hit expiry, then check progress toward TP/stop and fire an in-app
    notification the first time each threshold band is crossed. TP/stop are always stock-price
    levels the user set, for both spot and option positions -- but progress toward them for an
    option position is measured in the option's own price terms (avg premium vs. the option's
    modeled value at the tp/stop stock price, decayed to today), not the raw stock price, since
    avg_cost for an option position is premium, not a stock price. See
    docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md."""
    open_positions = db.list_positions("open")
    if not open_positions:
        return

    with _compute_lock:
        price_by_ticker = {p["ticker"]: (p["price"], p["date"]) for p in _computed}
        payload_by_ticker = {p["ticker"]: p for p in _computed}

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = datetime.now(timezone.utc).date()
    for position in open_positions:
        current = price_by_ticker.get(position["ticker"])
        if current is None:
            # Ticker never made it into _computed (e.g. too little history for any strategy to
            # score it -- see /api/tickers/fetch-one) -- a traded ticker still needs its daily
            # mark/TP-stop check every cycle regardless of compute status, so fall back to its
            # raw bars rather than skipping it forever.
            bars = data.get_bars(position["ticker"])
            if bars is None or bars.empty:
                continue
            current = (round(float(bars.Close.iloc[-1]), 4), str(bars.index[-1].date()))
        current_price, bar_date = current

        db.upsert_trade_daily_mark(position["id"], bar_date, current_price, now_iso)

        fills = db.list_fills(position["id"])

        # Any option entry fill whose expiry has passed auto-exits ITS remaining units at the
        # stock's current confirmed close -- bar_date is only ever a real, settled close (the
        # background loop never marks an intraday quote), so this is never a same-day "still
        # trading" false positive. exit price is the stock price, per the fill-price convention
        # -- the option's actual value at that point is derived (Black-Scholes) on read, same
        # as the rest of this app's option pricing, never stored here. Recomputes the replay
        # after each insert (not just once up front) since an earlier lot's auto-exit in this
        # same pass changes what "remaining units" means for lots processed after it.
        while True:
            state = replay_fills(fills)
            expired_lot = next(
                (lot for lot in state["open_lots"]
                 if lot["fill"]["expiry_date"] and bar_date >= lot["fill"]["expiry_date"]),
                None,
            )
            if expired_lot is None:
                break
            lot_fill = expired_lot["fill"]
            # Exit premium at expiry is intrinsic value (option_price with T<=0 returns exactly
            # that) -- an expired option is worth its intrinsic value, nothing else, matching
            # option_price's own expiry-day behavior used everywhere else in this file.
            expiry_premium = options_pricing.option_price(
                lot_fill["opt_type"], current_price, lot_fill["strike"], 0.0, lot_fill["iv_at_entry"] or 0.0,
            )
            db.insert_fill({
                "position_id": position["id"], "strategy_key": lot_fill["strategy_key"],
                "signal_date": lot_fill["signal_date"], "kind": "exit", "fill_date": bar_date,
                "price": None, "units": expired_lot["units_remaining"], "instrument": "option",
                "exit_reason": "expired", "opt_side": lot_fill["opt_side"], "opt_type": lot_fill["opt_type"],
                "strike": lot_fill["strike"], "premium": round(expiry_premium, 4),
                "expiry_date": lot_fill["expiry_date"], "iv_at_entry": lot_fill["iv_at_entry"],
                "notes": None, "created_at": now_iso,
            })
            fills = db.list_fills(position["id"])

        state = replay_fills(fills)
        if state["units_remaining"] <= 0:
            db.set_position_status(position["id"], "closed", now_iso)
            continue
        db.set_position_status(position["id"], "open", None)

        # Ticker-level payload (trend state, per-strategy open_position/avg_mae_wins_pct) --
        # None if the ticker never made it into _computed this pass (same fallback case as the
        # price lookup above), in which case the alerts below that need it are skipped, not
        # crashed on.
        payload = payload_by_ticker.get(position["ticker"])
        entry_strategy_key = fills[0]["strategy_key"] if fills else "manual"
        strat_payload = payload.get(entry_strategy_key) if payload and entry_strategy_key != "manual" else None

        if state["instrument"] == "option":
            _fire_option_gain_alert(position, state, current_price)
            live_result = _blended_live_option_value(position["ticker"], state["open_lots"], current_price, today)
            current_iv = live_result[1] if live_result is not None else None
            priced_lots = [lot for lot in state["open_lots"] if lot["fill"].get("iv_at_entry") is not None]
            iv_at_entry = None
            if priced_lots:
                total_units = sum(lot["units_remaining"] for lot in priced_lots)
                iv_at_entry = sum(lot["fill"]["iv_at_entry"] * lot["units_remaining"] for lot in priced_lots) / total_units
            _fire_iv_alert(position, current_iv, iv_at_entry, now_iso)

            avg_cost_per_share = state["avg_cost"] / state["multiplier"] if state["avg_cost"] else None
            option_gain_pct = None
            if live_result is not None and avg_cost_per_share:
                option_gain_pct = (live_result[0] - avg_cost_per_share) / avg_cost_per_share * 100
            if option_gain_pct is not None and strat_payload and strat_payload.get("open_position"):
                _fire_early_profit_alert(
                    position, option_gain_pct, strat_payload["open_position"]["days_held"],
                    strat_payload.get("avg_trade_days"), now_iso,
                )
            _fire_expiry_alerts(position, state["open_lots"], option_gain_pct, today, now_iso)
        elif state["avg_cost"] not in (None, 0):
            spot_unrealized_pct = (current_price - state["avg_cost"]) / state["avg_cost"] * 100
            _fire_spot_gain_alert(position, spot_unrealized_pct, now_iso)

        if payload and payload.get("prebreak"):
            _fire_trend_alert(position, payload["prebreak"].get("state"), now_iso)

        if strat_payload and strat_payload.get("open_position"):
            _fire_mae_alert(position, strat_payload["open_position"].get("mae_pct"), strat_payload.get("avg_mae_wins_pct"), now_iso)

        tp, stop = position["tp_price"], position["stop_price"]
        # TP/stop are ALWAYS stock-price levels the user set, for both spot and option
        # positions -- same familiar input either way, see
        # docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md. See
        # _position_pct_to_tp_stop's own docstring for the option-vs-spot math.
        pcts = _position_pct_to_tp_stop(state, tp, stop, current_price)
        if pcts is None:
            continue  # no priced lots (missing iv_at_entry) -- can't derive an option-price target, skip alerting this cycle
        pct_to_tp, pct_to_stop = pcts

        _fire_threshold_alerts(position, "tp_progress", pct_to_tp, position["last_alert_tp_pct"])
        _fire_threshold_alerts(position, "stop_progress", pct_to_stop, position["last_alert_stop_pct"])

    # Ticker-scoped, not position-scoped: a ticker held across multiple open positions still
    # gets exactly one push (see earnings_alert_log's own docstring in db.py).
    _fire_earnings_alerts({p["ticker"] for p in open_positions}, now_iso)
    _fire_market_context_alerts(now_iso)
    _fire_fed_day_alert(now_iso)


def _fire_earnings_alerts(open_tickers: set[str], now_iso: str) -> None:
    """Real open positions only, never signals/candidates/watchlist-only tickers -- pushes once
    per ticker per calendar day, days 5 through 0 (the earnings day itself) inclusive.
    days_to_earnings comes from the same per-cycle compute pass as everything else here, no new
    fetch (see payload["days_to_earnings"] in _compute_one)."""
    today = now_iso[:10]
    with _compute_lock:
        days_by_ticker = {p["ticker"]: p.get("days_to_earnings") for p in _computed}
    for ticker in open_tickers:
        days = days_by_ticker.get(ticker)
        if days is None or not (0 <= days <= 5):
            continue
        if db.get_earnings_alert_date(ticker) == today:
            continue  # already pushed today for this ticker
        when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        message = f"{ticker} reports earnings {when}"
        db.insert_notification(None, "earnings", float(days), message, now_iso)
        payload = json.dumps({"title": f"{ticker} — Earnings {when}", "body": message, "ticker": ticker})
        push.send_push_to_all(payload, now_iso)
        db.set_earnings_alert_date(ticker, today)


# Manually maintained -- no live macro-calendar data source exists anywhere in this app (see
# docs/superpowers/specs/2026-08-07-alert-system-audit.md's "Fed calendar" resolution). Needs
# updating each time the Fed publishes its next set of meeting dates.
FOMC_MEETING_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _fire_fed_day_alert(now_iso: str) -> None:
    """Fed announcement day, 🟡 -- fires once, the morning of, regardless of open positions
    (a market-wide review prompt, not tied to any specific trade). Dedup uses
    market_context_alert_log with symbol="FOMC" (not a real ticker, but the table's shape fits:
    one row per (symbol, kind) per day)."""
    today = now_iso[:10]
    if today not in FOMC_MEETING_DATES_2026:
        return
    if db.get_market_context_alert_date("FOMC", "fed_day") == today:
        return
    message = "Fed announcement today — review all open options"
    db.insert_notification(None, "fed_day", None, message, now_iso)
    payload = json.dumps({"title": "🟡 Fed day", "body": message})
    push.send_push_to_all(payload, now_iso)
    db.set_market_context_alert_date("FOMC", "fed_day", today)


def _fire_market_context_alerts(now_iso: str) -> None:
    """Oil (USO) spike > +3% (🟡) and Nasdaq (QQQ) selloff < -1.5% (🟡) -- both are close-to-close
    day-over-day moves (this app has no intraday data feed, only daily bars via the background
    fetch loop -- see data.py's own docstring), not truly intraday despite the proposal's
    wording. Fires at most once per calendar day per symbol+kind."""
    today = now_iso[:10]
    checks = [("USO", "oil_spike", OIL_SPIKE_PCT, "above"), ("QQQ", "nasdaq_selloff", NASDAQ_SELLOFF_PCT, "below")]
    for symbol, kind, threshold, direction in checks:
        bars = data.get_bars(symbol)
        if bars is None or len(bars) < 2:
            continue
        close, prior_close = float(bars.Close.iloc[-1]), float(bars.Close.iloc[-2])
        if not prior_close:
            continue
        change_pct = (close - prior_close) / prior_close * 100
        crossed = change_pct >= threshold if direction == "above" else change_pct <= threshold
        if not crossed:
            continue
        if db.get_market_context_alert_date(symbol, kind) == today:
            continue
        if symbol == "USO":
            message = f"Oil up {change_pct:.1f}% today — macro shift, review energy exposure"
        else:
            message = f"Nasdaq down {change_pct:.1f}% today — broad selloff, check BEARISH flags"
        db.insert_notification(None, kind, round(change_pct, 2), message, now_iso)
        payload = json.dumps({"title": f"🟡 {symbol} — {kind.replace('_', ' ').title()}", "body": message})
        push.send_push_to_all(payload, now_iso)
        db.set_market_context_alert_date(symbol, kind, today)


def _fire_threshold_alerts(position: dict, kind: str, pct: float, last_alert_pct: float | None) -> None:
    crossed = [t for t in TP_STOP_ALERT_THRESHOLDS if pct >= t and (last_alert_pct is None or t > last_alert_pct)]
    if not crossed:
        return
    highest = max(crossed)
    side = "TP" if kind == "tp_progress" else "stop"
    message = f"{position['ticker']} is {highest}% of the way to {side}"
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.insert_notification(position["id"], kind, highest, message, now_iso)
    if kind == "tp_progress":
        db.update_position_alert_pct(position["id"], tp_pct=highest)
    else:
        db.update_position_alert_pct(position["id"], stop_pct=highest)

    # Same message as the in-app notification, plus position_id so the service worker's
    # notificationclick handler can deep-link to the right trade -- see
    # docs/superpowers/specs/2026-07-31-pwa-push-design.md.
    payload = json.dumps({"title": f"{position['ticker']} — {side}", "body": message, "position_id": position["id"]})
    push.send_push_to_all(payload, now_iso)


def _fire_option_gain_alert(position: dict, state: dict, underlying_price: float) -> None:
    if not state["open_lots"] or state["avg_cost"] is None:
        return
    today = datetime.now(timezone.utc).date()
    option_value = _blended_option_value(state["open_lots"], underlying_price, today)
    if option_value is None:
        return
    avg_cost_per_share = state["avg_cost"] / state["multiplier"]
    if avg_cost_per_share == 0:
        return
    gain_pct = (option_value - avg_cost_per_share) / avg_cost_per_share * 100

    last_alert_pct = position["last_alert_option_gain_pct"]
    crossed = [t for t in OPTION_GAIN_ALERT_THRESHOLDS if gain_pct >= t and (last_alert_pct is None or t > last_alert_pct)]
    if not crossed:
        return
    highest = max(crossed)
    message = f"{position['ticker']} option is up {highest}% from entry"
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.insert_notification(position["id"], "option_gain", highest, message, now_iso)
    db.update_position_alert_pct(position["id"], option_gain_pct=highest)

    payload = json.dumps({"title": f"{position['ticker']} — Option +{highest}%", "body": message, "position_id": position["id"]})
    push.send_push_to_all(payload, now_iso)


# See docs/superpowers/specs/2026-08-07-alert-system-audit.md for the full proposed alert
# framework this section implements. Priority tier is carried only in the notification title
# emoji (matches the doc's 🔴/🟡/🟢 scheme) -- there's no separate priority column, notifications
# already sort by recency and the emoji is enough to scan for severity.
IV_CRUSH_THRESHOLD = -10  # percentage points
IV_SPIKE_THRESHOLD = 15
EARLY_PROFIT_GAIN_PCT = 50
EARLY_PROFIT_DAYS_FRACTION = 1 / 3
EXPIRY_RISK_DAYS = 15
EXPIRY_FINAL_WEEK_DAYS = 7
SPOT_GAIN_ALERT_THRESHOLD = 15
DEEP_MAE_MULTIPLIER = 1.5
OIL_SPIKE_PCT = 3.0
NASDAQ_SELLOFF_PCT = -1.5


def _fire_position_alert(position: dict, kind: str, pct: float | None, message: str, title: str, now_iso: str) -> None:
    """Shared insert+push for the position-scoped alerts below -- same shape as
    _fire_threshold_alerts/_fire_option_gain_alert's own inline insert+push, factored out since
    this section adds several more of them."""
    db.insert_notification(position["id"], kind, pct, message, now_iso)
    payload = json.dumps({"title": title, "body": message, "position_id": position["id"]})
    push.send_push_to_all(payload, now_iso)


def _fire_iv_alert(position: dict, current_iv: float | None, iv_at_entry: float | None, now_iso: str) -> None:
    """IV crush (<-10pts, 🔴) / IV spike (>+15pts, 🟢) -- options only, see
    _blended_live_option_value for where current_iv/iv_at_entry come from. Re-fires only when the
    flag itself changes (e.g. crush -> back to neutral -> crush again), not every cycle it stays
    in the same flagged state -- last_alert_iv_flag stores "crush"/"spike"/None."""
    if current_iv is None or iv_at_entry is None:
        return
    change_pts = (current_iv - iv_at_entry) * 100
    flag = "crush" if change_pts < IV_CRUSH_THRESHOLD else "spike" if change_pts > IV_SPIKE_THRESHOLD else None
    if flag == position["last_alert_iv_flag"]:
        return
    db.update_position_fields(position["id"], {"last_alert_iv_flag": flag})
    if flag is None:
        return  # recovered back into the neutral band -- update the stored flag, don't alert
    if flag == "crush":
        message = f"{position['ticker']} IV crushed {change_pts:.1f}% since entry — reassess immediately"
        title = f"🔴 {position['ticker']} — IV crush"
    else:
        message = f"{position['ticker']} IV up {change_pts:.1f}% since entry — favorable, monitor"
        title = f"🟢 {position['ticker']} — IV spike"
    _fire_position_alert(position, "iv_" + flag, round(change_pts, 2), message, title, now_iso)


def _fire_expiry_alerts(position: dict, open_lots: list[dict], unrealized_pct: float | None, today, now_iso: str) -> None:
    """Expiry risk (<15 days + losing, 🔴) and final week (<7 days, any position, 🔴) -- once per
    calendar day per position (last_alert_expiry_date), same day-based dedup shape as
    earnings_alert_log but position-scoped since a ticker's expiry is specific to that position's
    own fills. Uses the nearest expiry across the position's still-open lots (a multi-lot position
    with mixed expiries is most at risk from whichever lot expires soonest)."""
    expiries = [lot["fill"]["expiry_date"] for lot in open_lots if lot["fill"].get("expiry_date")]
    if not expiries:
        return
    nearest_expiry = min(datetime.strptime(e, "%Y-%m-%d").date() for e in expiries)
    days_to_expiry = (nearest_expiry - today).days
    if days_to_expiry < 0:
        return  # already past expiry -- _update_trade_marks_and_alerts's own auto-close handles this
    today_iso = now_iso[:10]
    if position["last_alert_expiry_date"] == today_iso:
        return
    if days_to_expiry < EXPIRY_FINAL_WEEK_DAYS:
        message = f"{position['ticker']} expires in {days_to_expiry} day{'s' if days_to_expiry != 1 else ''} — final week, decision required"
        title = f"🔴 {position['ticker']} — Final week"
    elif days_to_expiry < EXPIRY_RISK_DAYS and unrealized_pct is not None and unrealized_pct < 0:
        message = f"{position['ticker']} expires in {days_to_expiry} days, currently at a loss — exit or roll"
        title = f"🔴 {position['ticker']} — Expiry risk"
    else:
        return
    db.update_position_fields(position["id"], {"last_alert_expiry_date": today_iso})
    _fire_position_alert(position, "expiry_risk", float(days_to_expiry), message, title, now_iso)


def _fire_early_profit_alert(position: dict, gain_pct: float, days_held: int | None, avg_trade_days: float | None, now_iso: str) -> None:
    """+50% unrealized in under 1/3 of this strategy's average hold time, 🟡 -- one-shot (never
    un-fires, unlike the IV/trend flags, since there's only one band to cross here). Options only
    -- gain_pct here is the same option gain% _fire_option_gain_alert already computes, this just
    adds the days-held comparison on top of it."""
    if position["last_alert_early_profit"]:
        return
    if days_held is None or avg_trade_days is None or avg_trade_days <= 0:
        return
    if gain_pct < EARLY_PROFIT_GAIN_PCT or days_held >= avg_trade_days * EARLY_PROFIT_DAYS_FRACTION:
        return
    message = f"{position['ticker']} is up {gain_pct:.0f}% after only {days_held} days (avg hold {avg_trade_days:.0f}) — consider exit"
    title = f"🟡 {position['ticker']} — Early profit target"
    db.update_position_fields(position["id"], {"last_alert_early_profit": 1})
    _fire_position_alert(position, "early_profit", round(gain_pct, 2), message, title, now_iso)


def _fire_trend_alert(position: dict, trend_state: str | None, now_iso: str) -> None:
    """BEARISH trend filter on an open position, 🔴 -- re-fires only on entering BEARISH from a
    non-BEARISH state (same change-only dedup as _fire_iv_alert), not every cycle it stays
    BEARISH. Applies to spot and option positions alike -- a bearish underlying threatens both."""
    is_bearish = trend_state == "BEARISH"
    was_bearish = position["last_alert_trend_state"] == "BEARISH"
    db.update_position_fields(position["id"], {"last_alert_trend_state": trend_state})
    if not is_bearish or was_bearish:
        return
    message = f"{position['ticker']} trend filter turned BEARISH — exit per framework"
    title = f"🔴 {position['ticker']} — BEARISH signal"
    _fire_position_alert(position, "trend_bearish", None, message, title, now_iso)


def _fire_spot_gain_alert(position: dict, unrealized_pct: float, now_iso: str) -> None:
    """Spot unrealized > +15%, 🟡 -- spot-only equivalent of _fire_option_gain_alert, single
    threshold (not a ladder) since the proposal only specifies one band for spot."""
    if unrealized_pct < SPOT_GAIN_ALERT_THRESHOLD:
        return
    last_alert_pct = position["last_alert_spot_gain_pct"]
    if last_alert_pct is not None and last_alert_pct >= SPOT_GAIN_ALERT_THRESHOLD:
        return
    message = f"{position['ticker']} is up {unrealized_pct:.1f}% — check TP distance"
    title = f"🟡 {position['ticker']} — Strong gain"
    db.update_position_fields(position["id"], {"last_alert_spot_gain_pct": unrealized_pct})
    _fire_position_alert(position, "spot_gain", round(unrealized_pct, 2), message, title, now_iso)


def _fire_mae_alert(position: dict, mae_pct: float | None, avg_mae_wins_pct: float | None, now_iso: str) -> None:
    """Open MAE exceeds this strategy's average winning-trade MAE (🟡) or 1.5x that average (🔴)
    -- mae_pct comes from the SAME ticker+strategy's own live open_position (strategy_common's
    build_open_position, computed fresh every cycle from a running low-water-mark since the
    strategy's own signal date), not a value tracked separately for the user's actual fill. See
    docs/superpowers/specs/2026-08-07-alert-system-audit.md's "MAE source" resolution.
    last_alert_mae_level ("exceeded"/"deep"/None) re-fires only when the level itself changes,
    same change-only shape as last_alert_iv_flag/last_alert_trend_state."""
    if mae_pct is None or avg_mae_wins_pct is None or avg_mae_wins_pct <= 0:
        level = None
    elif mae_pct > avg_mae_wins_pct * DEEP_MAE_MULTIPLIER:
        level = "deep"
    elif mae_pct > avg_mae_wins_pct:
        level = "exceeded"
    else:
        level = None
    if level == position["last_alert_mae_level"]:
        return
    db.update_position_fields(position["id"], {"last_alert_mae_level": level})
    if level is None:
        return  # MAE recovered back under the average -- update the stored level, don't alert
    if level == "deep":
        message = f"{position['ticker']} MAE at {mae_pct:.1f}% — 1.5x+ average, review trend filter"
        title = f"🔴 {position['ticker']} — Deep MAE"
    else:
        message = f"{position['ticker']} MAE at {mae_pct:.1f}% — exceeded average ({avg_mae_wins_pct:.1f}%), monitor"
        title = f"🟡 {position['ticker']} — MAE exceeded"
    _fire_position_alert(position, "mae_" + level, round(mae_pct, 2), message, title, now_iso)


def refresh_and_compute(force: bool = False) -> None:
    """The 3-step cycle (see backend/SCREENING_FETCH_REFACTOR.md): fetch candidate tickers and
    persist them, gap-fetch/full-fetch each one's price data, then run compute_all() (which
    itself runs the technical filter once up front and recomputes only checksum-changed
    tickers). force=True (manual Refresh) always runs the full cycle -- no fetch-time or
    compute-caught-up short-circuit -- since a hard refresh must not be a no-op just because
    a prior pass already looked complete. Never runs two passes concurrently -- see
    _refresh_pass_lock."""
    global _rate_limited_until

    if not _refresh_pass_lock.acquire(blocking=False):
        print("app: refresh_and_compute() already running -- skipping this overlapping call.")
        return
    cycle_start = time.time()
    try:
        try:
            candidates = build_universe.fetch_candidates()
            db.set_candidate_tickers(candidates, time.time())
            _rate_limited_until = None
        except YFRateLimitError:
            _rate_limited_until = time.time() + RATE_LIMIT_BACKOFF
            print(f"app: fetch_candidates() rate-limited by Yahoo; skipping this cycle "
                  f"entirely, Refresh disabled for {RATE_LIMIT_BACKOFF // 60} minutes.")
            return
        except Exception as e:  # noqa: BLE001 - any other screener failure
            print(f"app: fetch_candidates() failed ({e}); falling back to the existing "
                  f"candidate_tickers table for this pass instead of aborting the cycle.")

        active = _active_tickers()
        fetch_time_before = data.last_fetch_time()
        fetch_start = time.time()
        data.warm_cache(active, force=force)
        fetch_seconds = time.time() - fetch_start
        fetch_time_after = data.last_fetch_time()

        with _compute_lock:
            computed_count = len(_computed)
        compute_caught_up = computed_count >= len(active) * 0.9  # allow for a few per-ticker errors

        if not force and fetch_time_before == fetch_time_after and compute_caught_up:
            print(f"app: nothing fetched this pass and compute is already caught up "
                  f"({computed_count}/{len(active)}) -- skipping compute_all()'s per-ticker check entirely. "
                  f"fetch={fetch_seconds:.1f}s total={time.time() - cycle_start:.1f}s")
            return
        compute_start = time.time()
        compute_all(force=force)
        compute_seconds = time.time() - compute_start
        try:
            _update_trade_marks_and_alerts()
        except Exception as e:  # noqa: BLE001 - a bad pass here must not affect the fetch/compute cycle above
            print(f"app: _update_trade_marks_and_alerts() failed ({e}); will retry next pass.")
        print(f"app: refresh_and_compute() cycle done -- {len(active)} tickers, "
              f"fetch={fetch_seconds:.1f}s compute={compute_seconds:.1f}s total={time.time() - cycle_start:.1f}s")
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
                refresh_and_compute()
            except Exception as e:  # noqa: BLE001 - the loop below still starts either way
                print(f"app: eager startup fetch+compute failed ({e}); will retry on the normal cadence.")
        elif not db.has_any_computed():
            print("app: bars exist but computed_results is empty -- running compute_all() immediately.")
            try:
                compute_all()
            except Exception as e:  # noqa: BLE001 - the loop below still starts either way
                print(f"app: eager startup compute_all() failed ({e}); will retry on the normal cadence.")

        # Market-hours-aware cadence -- see
        # docs/superpowers/specs/2026-08-03-market-hours-background-fetch-design.md. While the
        # market is open, fetch on MARKET_OPEN_FETCH_INTERVAL_SECONDS. While closed, fetch
        # exactly once per close period (tracked via db.get/set_last_close_fetch_at) and
        # otherwise just poll cheaply to notice the next state change.
        while True:
            now = datetime.now(timezone.utc)
            if market_hours.is_market_open(now):
                time.sleep(MARKET_OPEN_FETCH_INTERVAL_SECONDS)
                try:
                    refresh_and_compute()
                except Exception as e:  # noqa: BLE001 - one bad pass must not permanently kill the refresh loop
                    print(f"app: background refresh loop pass failed ({e}); will retry next cycle instead of stopping.")
            else:
                boundary = market_hours.most_recent_close_boundary(now)
                last_close_fetch = db.get_last_close_fetch_at()
                stale = last_close_fetch is None or datetime.fromisoformat(last_close_fetch) < boundary
                if stale:
                    try:
                        refresh_and_compute()
                        db.set_last_close_fetch_at(now.isoformat())
                    except Exception as e:  # noqa: BLE001 - one bad pass must not permanently kill the refresh loop
                        print(f"app: background refresh loop close-fetch failed ({e}); will retry next cycle instead of stopping.")
                time.sleep(CLOSED_MARKET_POLL_SECONDS)
    threading.Thread(target=loop, daemon=True).start()


@app.get("/api/meta")
def meta():
    return {
        "total_tickers": len(db.get_candidate_tickers()),
        "last_fetch": data.last_fetch_time(),
        # Non-null only while a warm_cache() fetch is actively in flight.
        "fetch_progress": data.fetch_progress(),
        # Non-null only while compute_all() is actively running; never overlaps fetch_progress.
        "compute_progress": compute_progress(),
        "rate_limited_until": _rate_limited_until,
        # Gates the Analyzer nav entry -- see
        # docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md.
        "daily_review_enabled": os.environ.get("ENABLE_DAILY_REVIEW", "false").lower() == "true",
    }


@app.get("/api/filter-defaults")
def filter_defaults():
    return quality_filter.DEFAULT_FILTER


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


def _run_manual_refresh() -> None:
    refresh_and_compute(force=True)


@app.get("/api/tickers")
def tickers(refresh: int = 0):
    # refresh=1 starts the cycle in the background and returns immediately -- it used to
    # run refresh_and_compute() inline and block on the response, which could take several
    # minutes and got killed by Cloudflare Tunnel's timeout well before finishing. The
    # frontend already polls /api/meta's fetch/compute progress and re-fetches /api/tickers
    # once done (see index.html's load()), so this now behaves the same way the background
    # loop's own periodic refresh already does.
    if refresh:
        threading.Thread(target=_run_manual_refresh, daemon=True).start()
    with _compute_lock:
        computed_snapshot = list(_computed)
        asof, errors = _computed_asof, dict(_computed_errors)
    return {
        "asof": asof,
        "cached": not refresh,
        "tickers": computed_snapshot,
        "errors": errors,
        "universe_error": None,
    }


@app.post("/api/tickers/fetch-one")
def fetch_one_ticker(body: dict):
    """One-off fetch+compute for a single ticker outside the screened universe, so the trade
    form has real price data to prefill from -- see
    docs/superpowers/specs/2026-08-01-add-trade-untracked-ticker-design.md. Does NOT add the
    ticker to candidate_tickers or run passes_technical_filters -- this is explicitly a user
    override, not a screener pass. Blocking/synchronous since it's a single-ticker,
    user-initiated action, not the bulk background cycle."""
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    data.warm_cache([ticker], force=True)
    _, payload, err, _ = _compute_one(ticker)
    if payload is not None:
        now = time.time()
        db.upsert_computed(ticker, payload, payload["date"], now, None)
        with _compute_lock:
            _computed[:] = [p for p in _computed if p["ticker"] != ticker] + [payload]
        return {"ticker": ticker, "price": payload["price"], "date": payload["date"], "found": True}
    # Strategy compute failed (e.g. too little history for VEXH/VCP/VCPO), but a ticker with too
    # little history to score is still tradeable -- the trade form only needs a last price, not a
    # computed payload. Fall back to raw bars so "insufficient history" never blocks Add Trade.
    bars = data.get_bars(ticker)
    if bars is None or bars.empty:
        raise HTTPException(status_code=422, detail=err or "no data")
    return {
        "ticker": ticker,
        "price": round(float(bars.Close.iloc[-1]), 4),
        "date": str(bars.index[-1].date()),
        "found": True,
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


def _resolve_export_strategy(body: dict) -> str:
    """body.strategy must be one of the 3 real payload keys -- matches the dashboard's
    Advance Filter strategy selector (ADV_STRAT_KEY in index.html), so the export only
    covers whichever strategy the user is currently looking at, not all three."""
    strategy = body.get("strategy")
    if strategy not in _EXPORT_STRATEGY_KEYS:
        raise HTTPException(status_code=400, detail=f"invalid strategy: {strategy!r}")
    return strategy


@app.post("/api/estimate_entry")
def api_estimate_entry(body: dict):
    ticker = body.get("ticker")
    strategy = _resolve_export_strategy(body)

    with _compute_lock:
        payload = next((p for p in _computed if p["ticker"] == ticker), None)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"no computed data for {ticker!r}")

    strat = payload.get(strategy)
    open_position = strat.get("open_position") if strat else None
    if open_position is None:
        raise HTTPException(status_code=400, detail=f"{ticker} has no open {strategy} position")

    avg_mae_wins_pct = strat.get("avg_mae_wins_pct")
    if avg_mae_wins_pct is None:
        raise HTTPException(status_code=400, detail=f"{ticker}/{strategy} has no avg_mae_wins_pct")

    atr_length = 14 if strategy == "vexh" else support_resistance.DEFAULT_ATR_LENGTH
    bars = data.get_bars(ticker)
    sr_levels = (support_resistance.compute_sr_levels(bars, atr_length=atr_length)
                 if bars is not None else {"support": [], "resistance": []})
    nearest = sr_levels["support"][:1]
    nearest_price = [p for p, _ in nearest]

    result = entry_estimate.estimate_entry(
        current_price=payload["price"],
        avg_mae_wins_pct=avg_mae_wins_pct,
        support_levels=nearest_price,
    )
    result["support_touches"] = nearest[0][1] if nearest else None
    return result


def _entry_plan_line(entry_plan: dict | None, order_method: str | None) -> str:
    """One-line rendering of entry_estimate.estimate_entry()'s return dict + order_method() --
    same numbers the daily review's "Take — Enter Tomorrow" section already shows verbatim, see
    SYSTEM_PROMPT in review_claude.py."""
    if entry_plan is None:
        return "—"
    limit = f"Limit ${entry_plan['recommended_limit']:.2f}"
    method = f", {order_method}" if order_method else ""
    return f"{limit}{method}"


def _prebreak_phase_cell(pb: dict | None) -> str:
    """State + its own phase score, e.g. "PRE-BREAKOUT (4)" -- same format as the Trades
    export's Pre-Breakout Summary line (frontend's prebreakSummaryLine)."""
    if pb is None:
        return "—"
    return f"{pb['state']} ({pb['score']})"


# Same word mapping as PrebreakChips.tsx / frontend's prebreakSummaryLine -- kept in sync
# manually, no shared source of truth across the Python/TypeScript boundary.
def _prebreak_squeeze_cell(pb: dict | None) -> str:
    if pb is None:
        return "—"
    return "COMPRESSED" if pb["bb_squeeze"] else "EXPANDED"


def _prebreak_volume_cell(pb: dict | None) -> str:
    if pb is None:
        return "—"
    return "DRY" if pb["vol_dry_up"] else "NORMAL/HIGH"


def _prebreak_coil_cell(pb: dict | None) -> str:
    if pb is None:
        return "—"
    return "COILING" if pb["near_resistance"] else "CLEAR"


def _signals_markdown_table(signals: list[dict], columns: list[tuple[str, str]]) -> str:
    """columns: [(header, field_name), ...]. field_name "entry_plan"/"phase"/"squeeze"/
    "volume"/"coil" render via their own dedicated helper; everything else is read straight off
    each signal dict."""
    if not signals:
        return ""
    header = "| " + " | ".join(h for h, _ in columns) + " |\n"
    header += "|" + "|".join("---" for _ in columns) + "|"
    lines = [header]
    for sig in signals:
        cells = []
        for _, field in columns:
            if field == "entry_plan":
                cells.append(_entry_plan_line(sig.get("entry_plan"), sig.get("order_method")))
            elif field == "phase":
                cells.append(_prebreak_phase_cell(sig.get("prebreak")))
            elif field == "squeeze":
                cells.append(_prebreak_squeeze_cell(sig.get("prebreak")))
            elif field == "volume":
                cells.append(_prebreak_volume_cell(sig.get("prebreak")))
            elif field == "coil":
                cells.append(_prebreak_coil_cell(sig.get("prebreak")))
            elif field == "last_7_close":
                closes = sig.get("last_7_close") or []
                cells.append(", ".join(f"${c:.2f}" for c in closes) if closes else "—")
            elif field == "strategy":
                cells.append(_STRATEGY_LABELS.get(sig.get("strategy"), sig.get("strategy") or "—"))
            elif field == "win_rate":
                wr = sig.get("win_rate")
                cells.append(f"{wr}%" if wr is not None else "—")
            elif field in ("unrealized_pct_if_entered",):
                v = sig.get(field)
                cells.append(f"{v:+.1f}%" if v is not None else "—")
            elif field in ("current_price", "signal_entry_price"):
                v = sig.get(field)
                cells.append(f"${v:.2f}" if v is not None else "—")
            else:
                v = sig.get(field)
                cells.append(str(v) if v is not None else "—")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


DASHBOARD_EXPORT_OPEN_SIGNAL_MAX_DAYS = 5


@app.get("/api/export/dashboard-md")
def export_dashboard_markdown():
    """Markdown export of the dashboard's current pending + open signals -- replaces the old
    PDF/CSV export entirely (no ticker selection, no format choice). Same underlying
    pending_signals/open_signals computation the daily review chatbot already uses, just with a
    wider open_signals day cutoff (5, not 3) and rendered as Markdown instead of prose."""
    with _compute_lock:
        computed_snapshot = list(_computed)
        asof = _computed_asof
    open_position_tickers = {p["ticker"] for p in db.list_positions("open")}
    pending = _pending_signals(computed_snapshot)
    open_signals = _open_signals(computed_snapshot, open_position_tickers, max_days=DASHBOARD_EXPORT_OPEN_SIGNAL_MAX_DAYS)

    # Timestamp of the actual data (last compute pass), not "today" -- a calendar date silently
    # lies about freshness if the last compute happened yesterday or the cycle stalled.
    stale_warning = ""
    if asof:
        asof_dt = datetime.fromisoformat(asof).astimezone(market_hours.MARKET_TZ)
        today = asof_dt.strftime("%Y-%m-%d %H:%M %Z")
        now_dt = datetime.now(market_hours.MARKET_TZ)
        if (now_dt - asof_dt) > timedelta(minutes=5):
            stale_warning = (
                f"⚠️ Data as of {asof_dt.strftime('%H:%M %Z')} — may be stale. "
                f"Current time is {now_dt.strftime('%H:%M %Z')}\n\n"
            )
    else:
        today = "unknown"
    # Base columns shared by both tables; Open Signals appends its own extra fields (a real
    # open_position signal has more to say than a fresh pending one -- how long it's been
    # running and how it would have done).
    base_columns = [
        ("Ticker", "ticker"), ("Score", "score"), ("Trades", "n_trades"), ("WR", "win_rate"),
        ("PF", "profit_factor"), ("Price", "current_price"), ("Phase", "phase"),
        ("Squeeze", "squeeze"), ("Volume", "volume"), ("Coil", "coil"), ("Entry Plan", "entry_plan"),
        ("Last 7 Close", "last_7_close"),
    ]
    pending_table = _signals_markdown_table(pending, base_columns)
    open_table = _signals_markdown_table(open_signals, base_columns + [
        ("Days Since Signal", "days_since_signal"), ("Signal Entry", "signal_entry_price"),
        ("Unrealized % If Entered", "unrealized_pct_if_entered"),
    ])

    markdown = (
        f"# Dashboard Export — {today}\n\n"
        + stale_warning +
        "## Pending Signals (fresh TAKE, not yet entered)\n\n"
        + (pending_table or "*No pending signals right now.*") + "\n\n"
        f"## Open Signals (strategy's own simulated trade, not yet entered — within {DASHBOARD_EXPORT_OPEN_SIGNAL_MAX_DAYS} days)\n\n"
        + (open_table or f"*No open signals within {DASHBOARD_EXPORT_OPEN_SIGNAL_MAX_DAYS} days.*") + "\n"
    )
    return {"markdown": markdown}


def _fill_exit_value(fill: dict) -> float:
    """The $-per-unit value a fill's units are worth -- premium for options (what was actually
    paid/received), the recorded stock price for spot. Mirrors replay_fills()'s own fill_value
    logic; kept as a standalone helper since routes need it outside a full replay too (e.g. to
    show an individual fill's own realized P&L contribution)."""
    return fill["premium"] if fill["instrument"] == "option" else fill["price"]


def _blended_option_value(open_lots: list[dict], spot_price: float, as_of_date) -> float | None:
    """Black-Scholes value of a position's still-open option lots at a given hypothetical (or
    real) underlying spot price, as of as_of_date (a date object) -- blended across lots weighted
    by each one's own remaining units, strike, and IV, since a position can hold fills from
    different entries with different strikes/IVs. Each lot's own risk-free rate is looked up from
    its own days-to-expiry as of as_of_date (see options_pricing.risk_free_rate_for), not a flat
    constant. Returns None if no lot has iv_at_entry (can't be priced)."""
    priced_lots = [lot for lot in open_lots if lot["fill"].get("iv_at_entry") is not None]
    if not priced_lots:
        return None
    total_units = sum(lot["units_remaining"] for lot in priced_lots)
    blended_value = 0.0
    for lot in priced_lots:
        f = lot["fill"]
        expiry = datetime.strptime(f["expiry_date"], "%Y-%m-%d").date()
        days_to_expiry = max((expiry - as_of_date).days, 0)
        T = days_to_expiry / 365
        r = options_pricing.risk_free_rate_for(days_to_expiry)
        value = options_pricing.option_price(f["opt_type"], spot_price, f["strike"], T, f["iv_at_entry"], r)
        blended_value += value * (lot["units_remaining"] / total_units)
    return blended_value


def _blended_live_option_value(ticker: str, open_lots: list[dict], underlying_price: float, today) -> tuple[float, float | None] | None:
    """Live-market equivalent of _blended_option_value -- current value blended across a
    position's open lots, weighted by units remaining, but from each lot's real yfinance quote
    instead of a Black-Scholes model. A lot with no live quote (illiquid, after-hours, chain
    error) falls back to that lot's own modeled value so one bad lot doesn't blank the whole
    position. Returns (blended_value, blended_iv) -- blended_iv is None if no lot had a live
    quote (nothing to compare iv_at_entry against). None only if there are no priced lots at
    all (mirrors _blended_option_value's None case)."""
    priced_lots = [lot for lot in open_lots if lot["fill"].get("iv_at_entry") is not None]
    if not priced_lots:
        return None
    total_units = sum(lot["units_remaining"] for lot in priced_lots)
    blended_value = 0.0
    live_iv_weighted = 0.0
    live_units = 0.0
    for lot in priced_lots:
        f = lot["fill"]
        weight = lot["units_remaining"] / total_units
        quote = data.get_live_option_price(ticker, f["strike"], f["expiry_date"], f["opt_type"])
        if quote is not None:
            blended_value += quote["mark"] * weight
            live_iv_weighted += quote["iv"] * lot["units_remaining"]
            live_units += lot["units_remaining"]
        else:
            expiry = datetime.strptime(f["expiry_date"], "%Y-%m-%d").date()
            days_to_expiry = max((expiry - today).days, 0)
            T = days_to_expiry / 365
            r = options_pricing.risk_free_rate_for(days_to_expiry)
            value = options_pricing.option_price(f["opt_type"], underlying_price, f["strike"], T, f["iv_at_entry"], r)
            blended_value += value * weight
    blended_iv = live_iv_weighted / live_units if live_units > 0 else None
    return blended_value, blended_iv


def _with_option_values(position: dict, fills: list[dict], marks: list[dict]) -> list[dict]:
    """Annotates each daily stock-price mark with the position's option value that day. Past
    dates always use the modeled Black-Scholes value (_blended_option_value) -- there's no live
    chain to fetch for a date that's already gone. Today's mark instead prefers a real live
    quote (_blended_live_option_value, same source _position_with_state uses for current_price),
    falling back to the model only if the chain has no live quote right now. Spot positions, or
    option positions with no fills carrying iv_at_entry, pass marks through unchanged."""
    if not fills or fills[0]["instrument"] != "option":
        return marks
    today_iso = datetime.now(timezone.utc).date().isoformat()
    out = []
    for m in marks:
        state = replay_fills(fills, as_of_date=m["mark_date"])
        mark_date = datetime.strptime(m["mark_date"], "%Y-%m-%d").date()
        if m["mark_date"] == today_iso:
            live_result = _blended_live_option_value(position["ticker"], state["open_lots"], m["close_price"], mark_date)
            option_value = live_result[0] if live_result is not None else None
        else:
            option_value = _blended_option_value(state["open_lots"], m["close_price"], mark_date)
        if option_value is None:
            out.append(m)
            continue
        out.append({**m, "option_value": round(option_value, 4)})
    return out


_FILL_SPOT_REQUIRED = ["price", "units", "strategy_key", "signal_date", "fill_date"]
# Options never need a stock "entry/exit price" -- premium (the option's own price at this
# fill, entry or exit) is what actually drives P&L, see
# docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md. price stays spot-only.
_FILL_OPTION_REQUIRED = ["units", "strategy_key", "signal_date", "fill_date",
                         "opt_side", "opt_type", "strike", "premium", "expiry_date"]
_FILL_OPTION_ONLY_FIELDS = ["opt_side", "opt_type", "strike", "premium", "expiry_date", "iv_at_entry"]


def _validate_fill_body(body: dict, *, require_kind: bool) -> dict:
    instrument = body.get("instrument")
    if instrument not in ("spot", "option"):
        raise HTTPException(status_code=400, detail=f"invalid instrument: {instrument!r}")
    if require_kind and body.get("kind") not in ("entry", "exit"):
        raise HTTPException(status_code=400, detail="kind must be 'entry' or 'exit'")
    required = list(_FILL_OPTION_REQUIRED if instrument == "option" else _FILL_SPOT_REQUIRED)
    if body.get("kind") == "exit":
        required.append("exit_reason")
    missing = [f for f in required if body.get(f) in (None, "")]
    if missing:
        raise HTTPException(status_code=400, detail=f"missing required field(s): {', '.join(missing)}")
    if body.get("exit_reason") is not None and body["exit_reason"] not in ("tp", "stop", "manual", "expired"):
        raise HTTPException(status_code=400, detail="exit_reason must be 'tp'|'stop'|'manual'|'expired'")
    return body


def _build_fill(position_id: int, body: dict, kind: str, now_iso: str) -> dict:
    instrument = body["instrument"]
    fill = {
        "position_id": position_id,
        "strategy_key": body["strategy_key"],
        "signal_date": body["signal_date"],
        "kind": kind,
        "fill_date": body["fill_date"],
        "price": body.get("price"),  # spot-only; options carry their price in premium instead
        "units": body["units"],
        "instrument": instrument,
        "exit_reason": body.get("exit_reason") if kind == "exit" else None,
        "notes": body.get("notes"),
        "created_at": now_iso,
    }
    for f in _FILL_OPTION_ONLY_FIELDS:
        fill[f] = body.get(f) if instrument == "option" else None
    return fill


def _backfill_position_marks(position_id: int, ticker: str, from_date: str) -> None:
    """A late-logged fill (fill_date in the past) has no daily marks for the gap between
    from_date and now, since the background loop only marks positions that already exist.
    Fill that gap once, from the ticker's already-cached bars -- no new fetch."""
    bars = data.get_bars(ticker)
    if bars is None or bars.empty:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for idx, row in bars.loc[from_date:].iterrows():
        mark_date = idx.strftime("%Y-%m-%d")
        if mark_date >= today:
            continue  # today (and beyond) is seeded from _computed by _seed_todays_mark, not here
        db.upsert_trade_daily_mark(position_id, mark_date, float(row["Close"]), now_iso)


def _seed_todays_mark(position_id: int, ticker: str) -> None:
    """A position touched today has no mark for today yet -- the background loop (or the next
    manual refresh) is what normally writes it, but that could be up to CHECK_INTERVAL away.
    Seed it immediately from the ticker's already-computed price/date (same source
    _update_trade_marks_and_alerts() uses), so the Trades page has data to show right away
    instead of an empty chart until the next cycle."""
    with _compute_lock:
        payload = next((p for p in _computed if p["ticker"] == ticker), None)
    if payload is not None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.upsert_trade_daily_mark(position_id, payload["date"], payload["price"], now_iso)
        return
    bars = data.get_bars(ticker)
    if bars is None or bars.empty:
        return
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.upsert_trade_daily_mark(position_id, str(bars.index[-1].date()), float(bars.Close.iloc[-1]), now_iso)


def _position_with_state(position: dict) -> dict:
    """Annotates a position with its derived replay state (avg_cost, units_remaining,
    realized_pnl, instrument) -- computed fresh from position_fills every time, never cached
    beyond the stored status/closed_at (see db.py's positions docstring). current_price is
    per-share and comparable against avg_cost: for spot it's the ticker's latest known price
    (same source as positions_summary's total_unrealized_pnl); for options it's the real
    live-market value (_blended_live_option_value -- a fresh yfinance option-chain quote per
    lot, TTL-cached in data.py), falling back to the modeled Black-Scholes value
    (_blended_option_value's own per-lot logic) only when the chain has no live quote. None if
    unpriceable. current_iv/iv_at_entry let the frontend flag IV crush -- see
    _blended_live_option_value."""
    fills = db.list_fills(position["id"])
    state = replay_fills(fills)
    with _compute_lock:
        payload = next((p for p in _computed if p["ticker"] == position["ticker"]), None)
    underlying_price = payload["price"] if payload else None
    if underlying_price is None:
        bars = data.get_bars(position["ticker"])
        if bars is not None and not bars.empty:
            underlying_price = round(float(bars.Close.iloc[-1]), 4)
    current_iv = None
    iv_at_entry = None
    if state["instrument"] == "option" and state["open_lots"] and underlying_price is not None:
        today = datetime.now(timezone.utc).date()
        result = _blended_live_option_value(position["ticker"], state["open_lots"], underlying_price, today)
        if result is not None:
            option_value, current_iv = result
            current_price = round(option_value, 4) if option_value is not None else None
        else:
            current_price = None
        priced_lots = [lot for lot in state["open_lots"] if lot["fill"].get("iv_at_entry") is not None]
        if priced_lots:
            total_units = sum(lot["units_remaining"] for lot in priced_lots)
            iv_at_entry = sum(lot["fill"]["iv_at_entry"] * lot["units_remaining"] for lot in priced_lots) / total_units
    else:
        current_price = underlying_price
    # Realized P&L% basis is avg_cost * units actually sold (entered - still remaining), not the
    # whole position -- avg_cost is a weighted-average blend fixed at each entry, so it's still
    # correct against units already exited. Matches PositionDetailPage's own client-side calc.
    units_entered = sum(f["units"] for f in fills if f["kind"] == "entry")
    units_sold = units_entered - state["units_remaining"]
    if abs(units_sold) < 1e-6:
        units_sold = 0.0
    cost_basis_sold = state["avg_cost"] * units_sold if state["avg_cost"] is not None else None
    realized_pnl_pct = round(state["realized_pnl"] / cost_basis_sold * 100, 2) if cost_basis_sold else None

    return {
        **position,
        "instrument": state["instrument"],
        "units_remaining": state["units_remaining"],
        "units_sold": units_sold,
        "avg_cost": round(state["avg_cost"], 4) if state["avg_cost"] is not None else None,
        "realized_pnl": round(state["realized_pnl"], 2),
        "realized_pnl_pct": realized_pnl_pct,
        "fill_count": len(fills),
        "current_price": current_price,
        "strategy_key": fills[0]["strategy_key"] if fills else "manual",
        # Live-quote IV vs the position's own entry IV (units-weighted across lots) -- None for
        # spot, or for an option position whose chain has no live quote right now (illiquid /
        # after-hours). See _blended_live_option_value.
        "current_iv": round(current_iv, 4) if current_iv is not None else None,
        "iv_at_entry": round(iv_at_entry, 4) if iv_at_entry is not None else None,
    }


_CONTEXT_TICKER_LABELS = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq 100 (QQQ)", "DIA": "Dow (DIA)",
                          "^TNX": "10Y Treasury Yield", "USO": "Oil (USO)"}


def _build_market_context() -> list[dict]:
    """Last close + day-over-day change for each of CONTEXT_TICKERS, from already-fetched bars."""
    context = []
    for ticker in CONTEXT_TICKERS:
        bars = data.get_bars(ticker)
        if bars is None or len(bars) < 1:
            continue
        close = round(float(bars.Close.iloc[-1]), 4)
        change_pct = None
        if len(bars) >= 2:
            prior_close = float(bars.Close.iloc[-2])
            if prior_close:
                change_pct = round((close - prior_close) / prior_close * 100, 2)
        context.append({
            "label": _CONTEXT_TICKER_LABELS.get(ticker, ticker),
            "close": close,
            "change_pct": change_pct,
        })
    return context


_FILL_REVIEW_FIELDS = (
    "fill_date", "price", "premium", "units", "instrument",
    "opt_side", "opt_type", "strike", "expiry_date", "iv_at_entry", "exit_reason",
)


def _summarize_fills_for_review(fills: list[dict], include_all: bool = False) -> list[dict]:
    """For an OPEN position (include_all=False, the default): fills[0] (the real entry) plus the
    most recent fill if different -- see build_daily_snapshot's open-positions call site for why
    that's sufficient there (only "entry vs. now" matters for a still-open position).

    For a CLOSED position (include_all=True): every fill, not just entry+last-exit. A position
    can have multiple partial exits (e.g. two TPs then a final manual cleanup) -- trimming to
    only the last exit silently dropped the earlier ones, so the chatbot only ever saw the LAST
    exit's date/price/exit_reason and reported it as if it were the whole story (e.g. calling a
    mostly-TP'd position "Manual" because that's what its final small cleanup fill said).

    Either way, each fill is trimmed to only the fields SYSTEM_PROMPT actually reads; drops
    id/position_id/strategy_key/signal_date/kind/created_at/notes and any always-null option
    field on a spot fill, all dead weight in the snapshot otherwise."""
    if not fills:
        return []
    if include_all:
        picked = fills
    else:
        picked = [fills[0]] if len(fills) == 1 or fills[-1] is fills[0] else [fills[0], fills[-1]]
    return [{k: f[k] for k in _FILL_REVIEW_FIELDS if f.get(k) is not None} for f in picked]


def _last_n_close(ticker: str, n: int) -> list[float]:
    """Last n daily Close prices, oldest first -- from the already-fetched bars cache, no new
    Yahoo call. Fewer than n bars (thin history) just returns what's there."""
    bars = data.get_bars(ticker)
    if bars is None or bars.empty:
        return []
    return [round(float(c), 4) for c in bars.Close.tail(n)]


def _pending_signals(computed_snapshot: list[dict]) -> list[dict]:
    """Tickers with a fresh TAKE verdict today, not yet entered -- see build_daily_snapshot's
    "Take — Enter Tomorrow" use and the dashboard's Markdown export (max_days doesn't apply
    here, a pending signal is inherently today's, no accumulated age to cap)."""
    pending_signals = []
    for payload in computed_snapshot:
        ticker = payload["ticker"]
        current_price = payload.get("price")
        for strat_key in _STRATEGY_MODULES:
            strat_payload = payload.get(strat_key)
            if not strat_payload or strat_payload.get("verdict") != "TAKE" or current_price is None:
                continue
            if not _passes_alert_quality_bar(payload, strat_key, strat_payload):
                continue

            avg_mae_wins_pct = strat_payload.get("avg_mae_wins_pct")
            entry_plan = None
            if avg_mae_wins_pct is not None:
                atr_length = 14 if strat_key == "vexh" else support_resistance.DEFAULT_ATR_LENGTH
                bars = data.get_bars(ticker)
                sr_levels = (support_resistance.compute_sr_levels(bars, atr_length=atr_length)
                             if bars is not None else {"support": [], "resistance": []})
                support_levels = [p for p, _ in sr_levels["support"][:1]]
                entry_plan = entry_estimate.estimate_entry(
                    current_price=current_price,
                    avg_mae_wins_pct=avg_mae_wins_pct,
                    support_levels=support_levels,
                )

            pending_signals.append({
                "ticker": ticker,
                "strategy": strat_key,
                "current_price": current_price,
                "score": score.compute_score(payload, strat_key),
                "n_trades": strat_payload.get("n_trades"),
                "win_rate": strat_payload.get("win_rate"),
                "profit_factor": strat_payload.get("profit_factor"),
                "entry_plan": entry_plan,
                "order_method": entry_estimate.order_method("spot"),
                "prebreak": payload.get("prebreak"),
                "last_7_close": _last_n_close(ticker, 7),
            })
    return pending_signals


def _open_signals(computed_snapshot: list[dict], open_position_tickers: set[str], max_days: int) -> list[dict]:
    """Tickers where a strategy's own simulated backtest is IN TRADE (open_position exists) but
    the user never actually entered it -- distinct from real open_positions and pending_signals
    (fresh TAKE, not yet entered at all). Capped to signals that opened within the last max_days
    days -- old ones are stale, not worth resurfacing as "still might be worth it." The daily
    review calls this with max_days=3; the dashboard's Markdown export calls it with max_days=5
    -- two independent callers, not a shared constant."""
    open_signals = []
    for payload in computed_snapshot:
        ticker = payload["ticker"]
        if ticker in open_position_tickers:
            continue
        current_price = payload.get("price")
        for strat_key in _STRATEGY_MODULES:
            strat_payload = payload.get(strat_key)
            if not strat_payload or strat_payload.get("verdict") != "IN TRADE" or current_price is None:
                continue
            if not _passes_alert_quality_bar(payload, strat_key, strat_payload):
                continue
            open_position = strat_payload.get("open_position")
            if not open_position or open_position.get("days_held", 999) > max_days:
                continue

            avg_mae_wins_pct = strat_payload.get("avg_mae_wins_pct")
            entry_plan = None
            if avg_mae_wins_pct is not None:
                atr_length = 14 if strat_key == "vexh" else support_resistance.DEFAULT_ATR_LENGTH
                bars = data.get_bars(ticker)
                sr_levels = (support_resistance.compute_sr_levels(bars, atr_length=atr_length)
                             if bars is not None else {"support": [], "resistance": []})
                support_levels = [p for p, _ in sr_levels["support"][:1]]
                entry_plan = entry_estimate.estimate_entry(
                    current_price=current_price,
                    avg_mae_wins_pct=avg_mae_wins_pct,
                    support_levels=support_levels,
                )

            open_signals.append({
                "ticker": ticker,
                "strategy": strat_key,
                "current_price": current_price,
                "score": score.compute_score(payload, strat_key),
                "n_trades": strat_payload.get("n_trades"),
                "win_rate": strat_payload.get("win_rate"),
                "profit_factor": strat_payload.get("profit_factor"),
                "signal_entry_date": open_position.get("entry_date"),
                "signal_entry_price": open_position.get("entry_price"),
                "days_since_signal": open_position.get("days_held"),
                "unrealized_pct_if_entered": open_position.get("unrealized_pct"),
                "entry_plan": entry_plan,
                "prebreak": payload.get("prebreak"),
                "last_7_close": _last_n_close(ticker, 7),
            })
    return open_signals


def build_daily_snapshot(user_id: int = DEFAULT_USER_ID) -> dict:
    """Compact, code-computed summary of current trade state for the daily review chatbot -- NOT
    a raw DB dump. Claude receives the CONCLUSION of these queries, not the tables themselves.
    See docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md, Part 2.

    user_id is accepted (not yet threaded into db.list_positions, which has no user scoping
    today) purely so this function's signature is already correct for when multi-user auth lands
    -- see DEFAULT_USER_ID's own docstring. It IS used below to check which past dates already
    have a generated daily_reviews row (recently_closed_uncovered_positions)."""
    with _compute_lock:
        computed_by_ticker = {p["ticker"]: p for p in _computed}

    # Each position is exactly what /api/positions/{id} (the real Trades page) returns --
    # _position_with_state() -- plus its real fills and daily marks, the same records the Trades
    # page itself reads entry date/price/contract terms and price history from. No independent
    # re-derivation of days_held/current_price/unrealized_pct/etc. here; the model reads those
    # straight out of the same real records the app already trusts elsewhere.
    open_positions = []
    for position in db.list_positions("open"):
        state = _position_with_state(position)
        if state["units_remaining"] <= 0:
            continue

        fills = db.list_fills(position["id"])
        marks = db.get_trade_daily_marks(position["id"])
        if state["instrument"] == "option":
            marks = _with_option_values(position, fills, marks)
        # The prompt only ever compares the most recent two marks ("today vs. yesterday") --
        # sending the position's full daily history (can be 100+ days) bloated the snapshot to
        # ~75% marks by size for nothing the model actually reads.
        marks = marks[-2:]

        # The prompt only ever reads fills[0] (the real entry -- date, price/premium, and for
        # options the contract terms). A scaled-in position can have many fills (seen: 6 for one
        # real position); sending all of them was the single biggest snapshot cost for no benefit
        # the model uses. Keep fills[0] plus the most recent fill (covers a same-day add/exit
        # without re-inventing "latest" semantics), each trimmed to only the fields the prompt
        # reads -- see SYSTEM_PROMPT's "fills[0] is the real entry" / "for options, fills[0] also
        # carries opt_type, opt_side, strike, expiry_date, iv_at_entry".
        summarized_fills = _summarize_fills_for_review(fills)

        # Only the strategy(s) actually traded (quality_filter.DEFAULT_FILTER["strategies"],
        # currently VCPO only) are sent to the review chatbot -- other strategies' verdicts on a
        # held ticker are noise the user doesn't act on and shouldn't factor into its commentary.
        payload = computed_by_ticker.get(state["ticker"])
        strategy_verdicts = {
            strat_key: (payload.get(strat_key) or {}).get("verdict")
            for strat_key in _DEFAULT_FILTER_WIRE_STRATEGIES
        } if payload else {}

        # last_alert_tp_pct/last_alert_stop_pct are internal alerting-threshold bookkeeping
        # (see _fire_threshold_alerts), not decision-relevant P&L data -- dropped so the model
        # can't mistake them for real events ("TP alert fired at X%") in its narration.
        state = {k: v for k, v in state.items() if k not in ("last_alert_tp_pct", "last_alert_stop_pct")}
        open_positions.append({
            **state,
            "fills": summarized_fills,
            "marks": marks,
            "strategy_verdicts": strategy_verdicts,
            # entry_strategy_key isn't part of the prompt's object shape (SYSTEM_PROMPT never
            # mentions it) -- it's only here for the strategy/investment split below, since
            # summarized_fills no longer carries strategy_key (trimmed, see
            # _summarize_fills_for_review). Popped back off right after the split.
            "entry_strategy_key": fills[0]["strategy_key"],
        })

    # Split by how the position was entered: a real strategy signal vs. a manual long-term
    # investment (fills are ordered by fill_date, id -- the first fill is always the entry, and
    # contract/strategy identity can't change across scale-in fills on the same position). The
    # review treats these very differently (see SYSTEM_PROMPT's "2.0 Strategy Trades" /
    # "2.5 Investment" split).
    strategy_positions = []
    investment_positions = []
    for p in open_positions:
        (investment_positions if p.pop("entry_strategy_key") == "manual" else strategy_positions).append(p)

    with _compute_lock:
        computed_snapshot = list(_computed)
    pending_signals = _pending_signals(computed_snapshot)
    open_position_tickers = {p["ticker"] for p in open_positions}
    open_signals = _open_signals(computed_snapshot, open_position_tickers, max_days=3)

    # The trading-day boundary (4pm ET close), NOT raw UTC calendar date -- review_trigger_daily()
    # resolves review_date the same way, and in the evening review window (4pm-midnight ET) UTC's
    # calendar date has already rolled to the next day while the trading day being reviewed is
    # still "today" in market terms. Using UTC date here silently excluded same-day closes/alerts
    # from every review generated after ~8pm ET (UTC midnight) until the next review window.
    today_iso_date = market_hours.most_recent_close_boundary(datetime.now(timezone.utc)).date().isoformat()
    today_start = today_iso_date + "T00:00:00"
    today_notifications = db.list_notifications_since(today_start)

    def _closed_position_snapshot(position: dict) -> dict:
        """Same per-position shape closed_today_*_positions has always used -- factored out so
        recently_closed_uncovered_* below can build identical objects for older closes.

        fills carries EVERY fill (include_all=True), not just entry+last-exit -- a position can
        have multiple partial exits with different exit_reasons (e.g. two TPs then a final
        manual cleanup), and each one's own real exit_reason is in fills itself. The top-level
        exit_reason field below is kept as a quick "how did this position finally close" label
        (the LAST fill's reason) for a single-exit position, but SYSTEM_PROMPT is told to read
        every exit fill's own exit_reason from `fills` rather than treat this one field as the
        complete story for a multi-exit position."""
        state = _position_with_state(position)
        fills = db.list_fills(position["id"])
        exit_fill = fills[-1] if fills and fills[-1]["kind"] == "exit" else None
        state = {k: v for k, v in state.items() if k not in ("last_alert_tp_pct", "last_alert_stop_pct")}
        return {
            **state,
            "fills": _summarize_fills_for_review(fills, include_all=True),
            "exit_reason": exit_fill["exit_reason"] if exit_fill else None,
            "entry_strategy_key": fills[0]["strategy_key"] if fills else "manual",
        }

    all_closed = db.list_positions("closed")

    realized_pnl_today = 0.0
    closed_today_positions = []
    for position in all_closed:
        closed_at = position.get("closed_at")
        if not closed_at or closed_at[:10] != today_iso_date:
            continue
        snapshot = _closed_position_snapshot(position)
        realized_pnl_today += snapshot["realized_pnl"]
        closed_today_positions.append(snapshot)

    closed_today_strategy = []
    closed_today_investment = []
    for p in closed_today_positions:
        (closed_today_investment if p.pop("entry_strategy_key") == "manual" else closed_today_strategy).append(p)

    # Positions closed in the last few days (not today -- those are closed_today_* above)
    # whose closing date has no daily_reviews row at all, i.e. no review was ever generated
    # that day to cover them (a skipped review day, most commonly). Without this, an exit or TP
    # from a missed day would never get mentioned by the chatbot at all. Checked against
    # daily_reviews directly (not "did that day's summary_text mention this ticker," which would
    # require fragile text search) -- if a review WAS generated that day, closed_today_* already
    # covered it when it ran, so it's treated as discussed regardless of what the model actually
    # said.
    RECENTLY_CLOSED_LOOKBACK_DAYS = 3
    uncovered_dates = set()
    for days_back in range(1, RECENTLY_CLOSED_LOOKBACK_DAYS + 1):
        check_date = (date.fromisoformat(today_iso_date) - timedelta(days=days_back)).isoformat()
        if db.get_daily_review(DEFAULT_USER_ID, check_date) is None:
            uncovered_dates.add(check_date)

    recently_closed_uncovered_positions = []
    if uncovered_dates:
        for position in all_closed:
            closed_at = position.get("closed_at")
            if not closed_at or closed_at[:10] not in uncovered_dates:
                continue
            recently_closed_uncovered_positions.append(_closed_position_snapshot(position))

    recently_closed_uncovered_strategy = []
    recently_closed_uncovered_investment = []
    for p in recently_closed_uncovered_positions:
        (recently_closed_uncovered_investment if p.pop("entry_strategy_key") == "manual" else recently_closed_uncovered_strategy).append(p)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market_context": _build_market_context(),
        "closed_today_strategy_positions": closed_today_strategy,
        "closed_today_investment_positions": closed_today_investment,
        "recently_closed_uncovered_strategy_positions": recently_closed_uncovered_strategy,
        "recently_closed_uncovered_investment_positions": recently_closed_uncovered_investment,
        "strategy_positions": strategy_positions,
        "investment_positions": investment_positions,
        "pending_signals": pending_signals,
        "open_signals": open_signals,
        "realized_pnl_today": round(realized_pnl_today, 2),
        "notable_alerts_today": [
            {"message": n["message"], "kind": n["kind"]} for n in today_notifications
        ],
    }


@app.post("/api/positions")
def create_position(body: dict):
    """Opens a NEW position from a first entry fill. 409 if this ticker already has an open
    position -- use POST /api/positions/{id}/fills to add to it instead (see
    docs/superpowers/specs/2026-07-31-position-fills-design.md)."""
    ticker = body.get("ticker")
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    _validate_fill_body(body, require_kind=False)
    if body.get("tp_price") is None or body.get("stop_price") is None:
        raise HTTPException(status_code=400, detail="tp_price and stop_price are required")
    if db.find_open_position_by_ticker(ticker) is not None:
        raise HTTPException(status_code=409, detail=f"{ticker} already has an open position -- add a fill to it instead")

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        position_id = db.insert_position(ticker, body["tp_price"], body["stop_price"], now_iso, body.get("notes"))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"{ticker} already has an open position -- add a fill to it instead")

    fill = _build_fill(position_id, body, "entry", now_iso)
    db.insert_fill(fill)

    if fill["fill_date"] < datetime.now(timezone.utc).date().isoformat():
        _backfill_position_marks(position_id, ticker, fill["fill_date"])
    _seed_todays_mark(position_id, ticker)

    return _position_with_state(db.get_position(position_id))


@app.post("/api/positions/{position_id}/fills")
def add_fill(position_id: int, body: dict):
    """Adds an entry (scale-in) or exit (full or partial) fill to an existing open position.
    An exit's units may be less than units_remaining (partial) or equal to it (full -- closes
    the position); exiting more than units_remaining is rejected."""
    position = db.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail=f"no position with id {position_id}")
    if position["status"] != "open":
        raise HTTPException(status_code=400, detail="position is closed -- a new entry starts a new position")
    _validate_fill_body(body, require_kind=True)

    fills = db.list_fills(position_id)
    state = replay_fills(fills)
    if body["kind"] == "exit" and body["units"] > state["units_remaining"] + 1e-9:
        raise HTTPException(status_code=400,
                             detail=f"cannot exit {body['units']} units -- only {state['units_remaining']} remaining")

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fill = _build_fill(position_id, body, body["kind"], now_iso)
    db.insert_fill(fill)

    new_state = replay_fills(db.list_fills(position_id))
    if new_state["units_remaining"] <= 1e-9:
        db.set_position_status(position_id, "closed", now_iso)
    else:
        db.set_position_status(position_id, "open", None)

    if fill["fill_date"] < datetime.now(timezone.utc).date().isoformat():
        _backfill_position_marks(position_id, position["ticker"], fill["fill_date"])
    _seed_todays_mark(position_id, position["ticker"])

    return _position_with_state(db.get_position(position_id))


@app.get("/api/positions")
def list_positions(status: str | None = None, type: str | None = None):
    if status not in (None, "open", "closed"):
        raise HTTPException(status_code=400, detail=f"invalid status: {status!r}")
    if type not in (None, "spot", "options"):
        raise HTTPException(status_code=400, detail=f"invalid type: {type!r}")
    result = [_position_with_state(p) for p in db.list_positions(status)]
    if type == "spot":
        result = [p for p in result if p["instrument"] == "spot"]
    elif type == "options":
        result = [p for p in result if p["instrument"] == "option"]
    return result


def _open_position_unrealized(p: dict, fills: list[dict]) -> float | None:
    """$ unrealized for one open position -- spot: current_price vs avg_cost, straight.
    Options: p["current_price"] is already the modeled per-share option value (see
    _position_with_state), so this only needs to unwind avg_cost's multiplier the same way
    _position_pct_to_tp_stop already does for TP/stop progress. None if unpriceable."""
    if p["avg_cost"] is None or p["current_price"] is None:
        return None
    if p["instrument"] == "spot":
        return (p["current_price"] - p["avg_cost"]) * p["units_remaining"]

    state = replay_fills(fills)
    if not state["open_lots"]:
        return None
    avg_cost_per_share = p["avg_cost"] / state["multiplier"]
    return (p["current_price"] - avg_cost_per_share) * p["units_remaining"] * state["multiplier"]


@app.get("/api/positions/summary")
def positions_summary(type: str | None = None):
    """Win rate / avg return -- ticker-position-level only, not filterable by strategy, since a
    position can span fills from multiple strategies (see the design doc's grouping decision).
    'Simple math' per the design brief: pnl / cost basis, no Black-Scholes needed for the %
    itself, only for pricing an option's exit VALUE (handled inside replay_fills). type=spot or
    type=options filters positions to that instrument BEFORE any of the counting/summing below
    -- same computation either way, just over a smaller set; unrealized is the only place the
    math itself differs (see _open_position_unrealized)."""
    if type not in (None, "spot", "options"):
        raise HTTPException(status_code=400, detail=f"invalid type: {type!r}")

    def matches_type(p: dict) -> bool:
        if type == "spot":
            return p["instrument"] == "spot"
        if type == "options":
            return p["instrument"] == "option"
        return True

    closed = [p for p in (_position_with_state(p) for p in db.list_positions("closed")) if matches_type(p)]
    returns = []
    for p in closed:
        fills = db.list_fills(p["id"])
        entry_fills = [f for f in fills if f["kind"] == "entry"]
        if not entry_fills:
            continue
        multiplier = 100 if p["instrument"] == "option" else 1
        cost_basis = sum(_fill_exit_value(f) * f["units"] * multiplier for f in entry_fills)
        if cost_basis:
            returns.append(p["realized_pnl"] / cost_basis * 100)
    wins = [r for r in returns if r >= 0]

    open_positions = [p for p in (_position_with_state(p) for p in db.list_positions("open")) if matches_type(p)]
    total_unrealized_pnl = 0.0
    for p in open_positions:
        unrealized = _open_position_unrealized(p, db.list_fills(p["id"]))
        if unrealized is not None:
            total_unrealized_pnl += unrealized

    return {
        "open_count": len(open_positions),
        "closed_count": len(closed),
        "win_count": len(wins),
        "win_rate_pct": round(len(wins) / len(returns) * 100, 1) if returns else None,
        "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        # An open position can carry real realized_pnl of its own (a partial exit that hasn't
        # fully closed the position) -- total_realized_pnl must include that, not just fully
        # closed positions, or a partial TP/exit silently vanishes from the summary total.
        "total_realized_pnl": round(sum(p["realized_pnl"] for p in closed) + sum(p["realized_pnl"] for p in open_positions), 2),
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
    }


@app.get("/api/positions/pnl-series")
def positions_pnl_series(type: str | None = None):
    """Portfolio-wide daily cumulative realized/unrealized P&L -- ported from the frontend's
    former computePnlSeries()/replayAsOf() (frontend/src/lib/pnlSeries.ts) to a single backend
    call, so the Trades page no longer needs one marks + one fills request per position just to
    draw this chart. realized is a step function (flat between exits, steps on each exit fill's
    own date); unrealized is mark-to-market against each position's own daily marks. type=spot
    or type=options filters the input positions before the loop below -- the loop itself
    already branches on instrument internally for pricing, so filtering the input is the only
    change needed."""
    if type not in (None, "spot", "options"):
        raise HTTPException(status_code=400, detail=f"invalid type: {type!r}")
    positions = db.list_positions(None)
    position_ids = [p["id"] for p in positions]
    fills_by_position = db.list_fills_bulk(position_ids)

    # Raw position rows carry no instrument column -- it's derived from fills (fills[0], same
    # as every other instrument check in this file). A position with no fills yet can't match
    # either type filter.
    if type in ("spot", "options"):
        wanted = "spot" if type == "spot" else "option"
        positions = [
            p for p in positions
            if fills_by_position.get(p["id"]) and fills_by_position[p["id"]][0]["instrument"] == wanted
        ]
        fills_by_position = {p["id"]: fills_by_position[p["id"]] for p in positions}

    marks_by_position = db.get_trade_daily_marks_bulk([p["id"] for p in positions])

    dates = set()
    for p in positions:
        for m in marks_by_position.get(p["id"], []):
            dates.add(m["mark_date"])
        for f in fills_by_position.get(p["id"], []):
            if f["kind"] == "exit":
                dates.add(f["fill_date"])
    dates = sorted(dates)

    realized: list[float] = []
    unrealized: list[float] = []
    for as_of in dates:
        day_realized = 0.0
        day_unrealized = 0.0
        for p in positions:
            fills = fills_by_position.get(p["id"], [])
            if not fills:
                continue
            state = replay_fills(fills, as_of_date=as_of)
            day_realized += state["realized_pnl"]

            if state["units_remaining"] <= 0 or state["avg_cost"] is None:
                continue
            marks = marks_by_position.get(p["id"], [])
            if state["instrument"] == "option":
                marks = _with_option_values(p, fills, marks)
            mark = next((m for m in marks if m["mark_date"] == as_of), None)
            if mark is None:
                continue
            value = mark.get("option_value", mark["close_price"])
            if value is None:
                value = mark["close_price"]
            avg_cost_per_share = state["avg_cost"] / state["multiplier"]
            day_unrealized += (value - avg_cost_per_share) * state["units_remaining"] * state["multiplier"]

        realized.append(round(day_realized, 2))
        unrealized.append(round(day_unrealized, 2))

    return {"dates": dates, "realized": realized, "unrealized": unrealized}


@app.get("/api/positions/analytics")
def positions_analytics(ticker: str | None = None, status: str | None = None,
                         date_from: str | None = None, date_to: str | None = None):
    """No strategy filter -- a position isn't strategy-scoped (see grouping decision in the
    design doc); strategy_key still lives on each individual fill for display/history."""
    if status not in (None, "open", "closed"):
        raise HTTPException(status_code=400, detail=f"invalid status: {status!r}")
    positions = db.list_positions(status)
    if ticker:
        positions = [p for p in positions if p["ticker"] == ticker]
    if date_from:
        positions = [p for p in positions if p["opened_at"][:10] >= date_from]
    if date_to:
        positions = [p for p in positions if p["opened_at"][:10] <= date_to]

    fills_by_position = db.list_fills_bulk([p["id"] for p in positions])
    marks_by_position = db.get_trade_daily_marks_bulk([p["id"] for p in positions])
    series = []
    for p in positions:
        fills = fills_by_position.get(p["id"], [])
        marks = _with_option_values(p, fills, marks_by_position.get(p["id"], []))
        state = replay_fills(fills)
        entry_fills = [f for f in fills if f["kind"] == "entry"]
        multiplier = state["multiplier"]
        cost_basis = sum(_fill_exit_value(f) * f["units"] * multiplier for f in entry_fills)
        series.append({
            "position_id": p["id"],
            "ticker": p["ticker"],
            "status": p["status"],
            "instrument": state["instrument"],
            "opened_at": p["opened_at"],
            "closed_at": p["closed_at"],
            "avg_cost": round(state["avg_cost"], 4) if state["avg_cost"] is not None else None,
            "units_remaining": state["units_remaining"],
            "realized_pnl": round(state["realized_pnl"], 2),
            "cost_basis": round(cost_basis, 2),
            "multiplier": multiplier,
            "marks": marks,
        })
    return {"positions": series}


@app.get("/api/positions/{position_id}")
def get_position(position_id: int):
    position = db.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail=f"no position with id {position_id}")
    return _position_with_state(position)


_POSITION_EDITABLE_FIELDS = ["tp_price", "stop_price", "notes"]
_FILL_EDITABLE_FIELDS = [
    "fill_date", "price", "units", "exit_reason",
    "opt_side", "opt_type", "strike", "premium", "expiry_date", "iv_at_entry", "notes",
]


@app.patch("/api/positions/{position_id}")
def update_position(position_id: int, body: dict):
    """Position-level fields only (tp_price/stop_price/notes) -- individual fills are corrected
    via PATCH /api/positions/{id}/fills/{fill_id}, not through the position."""
    if db.get_position(position_id) is None:
        raise HTTPException(status_code=404, detail=f"no position with id {position_id}")
    editable = {k: v for k, v in body.items() if k in _POSITION_EDITABLE_FIELDS}
    if not editable:
        raise HTTPException(status_code=400,
                             detail=f"no editable fields in body ({', '.join(_POSITION_EDITABLE_FIELDS)})")
    db.update_position_fields(position_id, editable)
    return _position_with_state(db.get_position(position_id))


@app.delete("/api/positions/{position_id}")
def cancel_position(position_id: int):
    """Hard delete -- cancels the whole position and every fill on it, for a position confirmed
    in error. Cascades to marks/notifications in db.delete_position()."""
    if db.get_position(position_id) is None:
        raise HTTPException(status_code=404, detail=f"no position with id {position_id}")
    db.delete_position(position_id)
    return {"ok": True}


@app.patch("/api/positions/{position_id}/fills/{fill_id}")
def update_fill(position_id: int, fill_id: int, body: dict):
    """Corrects a single fill's recorded values -- e.g. a wrong exit price. Deliberately
    excludes kind/instrument/strategy_key/signal_date (those define what the fill IS, not a
    correctable detail) and position_id (which fill belongs to which position isn't editable
    here -- delete + re-add if a fill was logged against the wrong position)."""
    fill = db.get_fill(fill_id)
    if fill is None or fill["position_id"] != position_id:
        raise HTTPException(status_code=404, detail=f"no fill {fill_id} on position {position_id}")
    editable = {k: v for k, v in body.items() if k in _FILL_EDITABLE_FIELDS}
    if not editable:
        raise HTTPException(status_code=400,
                             detail=f"no editable fields in body ({', '.join(_FILL_EDITABLE_FIELDS)})")
    if "exit_reason" in editable and editable["exit_reason"] not in ("tp", "stop", "manual", "expired"):
        raise HTTPException(status_code=400, detail="exit_reason must be 'tp'|'stop'|'manual'|'expired'")
    db.update_fill_fields(fill_id, editable)

    # A corrected price/units can change units_remaining -- recheck status the same way
    # add_fill() does, rather than leaving a stale open/closed flag.
    new_state = replay_fills(db.list_fills(position_id))
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if new_state["units_remaining"] <= 1e-9:
        db.set_position_status(position_id, "closed", now_iso)
    else:
        db.set_position_status(position_id, "open", None)

    return _position_with_state(db.get_position(position_id))


@app.delete("/api/positions/{position_id}/fills/{fill_id}")
def delete_fill(position_id: int, fill_id: int):
    """Removes one erroneous fill (e.g. logged against the wrong position). If that was the
    position's only fill, the position itself is deleted too -- a position with zero fills
    isn't a meaningful state to leave behind."""
    fill = db.get_fill(fill_id)
    if fill is None or fill["position_id"] != position_id:
        raise HTTPException(status_code=404, detail=f"no fill {fill_id} on position {position_id}")
    db.delete_fill(fill_id)

    remaining_fills = db.list_fills(position_id)
    if not remaining_fills:
        db.delete_position(position_id)
        return {"ok": True, "position_deleted": True}

    new_state = replay_fills(remaining_fills)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if new_state["units_remaining"] <= 1e-9:
        db.set_position_status(position_id, "closed", now_iso)
    else:
        db.set_position_status(position_id, "open", None)
    return {"ok": True, "position_deleted": False}


@app.get("/api/positions/{position_id}/fills")
def get_fills(position_id: int):
    if db.get_position(position_id) is None:
        raise HTTPException(status_code=404, detail=f"no position with id {position_id}")
    return db.list_fills(position_id)


@app.get("/api/positions/{position_id}/marks")
def position_marks(position_id: int):
    position = db.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail=f"no position with id {position_id}")
    fills = db.list_fills(position_id)
    marks = db.get_trade_daily_marks(position_id)
    return _with_option_values(position, fills, marks)


@app.get("/api/notifications")
def notifications(unread: int = 0):
    return db.list_notifications(unread_only=bool(unread))


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int):
    db.mark_notification_read(notification_id, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {"ok": True}


@app.post("/api/notifications/read-all")
def read_all_notifications():
    db.mark_all_notifications_read(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {"ok": True}


@app.get("/api/push/vapid-public-key")
def push_vapid_public_key():
    key = os.environ.get("VAPID_PUBLIC_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="push notifications are not configured on this server")
    return {"public_key": key}


@app.post("/api/push/subscribe")
def push_subscribe(body: dict):
    endpoint = body.get("endpoint")
    keys = body.get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="expected a PushSubscription.toJSON() body")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.upsert_push_subscription(endpoint, p256dh, auth, now_iso)
    return {"ok": True}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: dict):
    endpoint = body.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="expected {\"endpoint\": ...}")
    db.delete_push_subscription(endpoint)
    return {"ok": True}


# ─────────────────────── daily review chatbot ───────────────────────
# See docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md. Every endpoint
# below 404s when the feature flag is off -- a determined user hitting the API directly must
# not be able to bypass a hidden UI button.

def _require_daily_review_enabled() -> None:
    if os.environ.get("ENABLE_DAILY_REVIEW", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="not found")


_USER_DOCS_DIR = os.path.join(os.path.dirname(__file__), "user_docs")


@app.get("/api/review/onboarding-status")
def review_onboarding_status():
    _require_daily_review_enabled()
    # Deliberately trivial -- reads the env var directly, no DB, no side effect. Nothing in this
    # app ever flips DAILY_REVIEW_ONBOARDED itself; that's an intentional manual step (see design
    # doc Part 1, "First-visit onboarding prompt") -- there is no endpoint that sets it.
    onboarded = os.environ.get("DAILY_REVIEW_ONBOARDED", "false").lower() == "true"
    return {"onboarded": onboarded}


@app.post("/api/review/document")
async def review_upload_document(file: UploadFile = File(...)):
    _require_daily_review_enabled()
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("pdf", "docx", "md", "txt"):
        raise HTTPException(status_code=400, detail="only .pdf, .docx, .md, .txt files are accepted")

    os.makedirs(_USER_DOCS_DIR, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Timestamp-prefixed so a re-upload never silently overwrites the file backing an OLDER
    # document_chunks row still referenced by past reviews (see design doc: old chunks are kept,
    # not pruned, on re-upload).
    safe_name = f"{int(time.time())}_{os.path.basename(filename)}"
    dest_path = os.path.join(_USER_DOCS_DIR, safe_name)
    contents = await file.read()
    with open(dest_path, "wb") as f:
        f.write(contents)

    try:
        document_id = review_ingest.ingest_document(DEFAULT_USER_ID, dest_path, filename, ext, now_iso)
    except Exception as e:  # noqa: BLE001 - a bad upload must surface as a clear error, not a 500 with no trace
        raise HTTPException(status_code=422, detail=f"could not process document: {e}")
    return {"document_id": document_id, "filename": filename, "status": "ready"}


@app.get("/api/review/document")
def review_get_document():
    _require_daily_review_enabled()
    doc = db.get_active_user_document(DEFAULT_USER_ID)
    if doc is None:
        return {"document": None}
    return {"document": {"filename": doc["filename"], "file_type": doc["file_type"], "uploaded_at": doc["uploaded_at"]}}


@app.get("/api/review/status")
def review_status():
    _require_daily_review_enabled()
    now = datetime.now(timezone.utc)
    active = db.get_active_daily_review(DEFAULT_USER_ID)
    active_out = None
    if active is not None:
        active_out = {
            "review_date": active["review_date"],
            "status": active["status"],
            # Chat never auto-closes by time -- the user decides when to start a new review vs.
            # keep talking in an existing one, so this is always true for any active review.
            "chat_open": True,
        }
    return {
        "can_start": True,
        "active_review": active_out,
    }


@app.post("/api/review/daily")
def review_trigger_daily():
    _require_daily_review_enabled()
    now = datetime.now(timezone.utc)

    review_date = market_hours.most_recent_close_boundary(now).date().isoformat()
    existing = db.get_daily_review(DEFAULT_USER_ID, review_date)
    if existing is not None:
        return _review_to_dict(existing)

    now_iso = now.isoformat(timespec="seconds")
    snapshot = build_daily_snapshot(DEFAULT_USER_ID)
    query_text = json.dumps(snapshot, default=str)
    retrieved_chunks = review_ingest.top_k_chunks(DEFAULT_USER_ID, query_text)
    memory = db.get_review_memory_summary(DEFAULT_USER_ID)
    memory_text = memory["summary_text"] if memory else None

    try:
        summary_text = review_claude.generate_daily_review(snapshot, retrieved_chunks, memory_text)
    except review_claude.ReviewTruncatedError:
        raise HTTPException(status_code=502, detail="review generation was cut off -- try again")
    embedding = review_ingest.embed_texts([summary_text])[0]
    review_id = db.insert_daily_review(
        DEFAULT_USER_ID, review_date, summary_text, embedding.tobytes(), json.dumps(snapshot, default=str), now_iso,
    )

    def _run_enrichment():
        try:
            fact = review_claude.extract_enrichment_fact(summary_text)
            if fact:
                fact_vec = review_ingest.embed_texts([fact])[0]
                db.insert_document_chunk(
                    DEFAULT_USER_ID, None, "auto_enrichment", review_id, None, fact, fact_vec.tobytes(), now_iso,
                )
        except Exception as e:  # noqa: BLE001 - enrichment is best-effort, must not fail the review itself
            print(f"app: post-review enrichment extraction failed ({e}); review still saved.")

    def _run_memory_update():
        try:
            updated_memory = review_claude.update_rolling_memory(memory_text, summary_text)
            db.upsert_review_memory_summary(DEFAULT_USER_ID, updated_memory, review_id, now_iso)
        except Exception as e:  # noqa: BLE001 - same -- rolling memory update must not fail the review itself
            print(f"app: rolling memory update failed ({e}); review still saved.")

    # Independent best-effort calls -- run concurrently rather than blocking the response in
    # series (each is its own Claude API round-trip).
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_run_enrichment), pool.submit(_run_memory_update)]
        for f in as_completed(futures):
            f.result()

    return _review_to_dict(db.get_daily_review(DEFAULT_USER_ID, review_date))


def _review_to_dict(review: dict) -> dict:
    return {
        "review_date": review["review_date"],
        "status": review["status"],
        "summary_text": review["summary_text"],
        "summary_text_chunks": review_stream.chunk_markdown_for_stream(review["summary_text"]),
        "created_at": review["created_at"],
    }


@app.get("/api/review/daily/{review_date}")
def review_get_daily(review_date: str):
    _require_daily_review_enabled()
    review = db.get_daily_review(DEFAULT_USER_ID, review_date)
    if review is None:
        raise HTTPException(status_code=404, detail=f"no review for {review_date}")
    messages = db.list_review_chat_messages(review["id"])
    return {
        **_review_to_dict(review),
        "chat_messages": [{"role": m["role"], "content": m["content"], "created_at": m["created_at"]} for m in messages],
    }


@app.post("/api/review/daily/{review_date}/chat")
def review_chat(review_date: str, body: dict):
    _require_daily_review_enabled()
    user_message = (body.get("message") or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="message is required")

    review = db.get_daily_review(DEFAULT_USER_ID, review_date)
    if review is None:
        raise HTTPException(status_code=404, detail=f"no review for {review_date}")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")

    # No time-based lock -- the user controls when a review's chat context is done, not a clock.
    # Any review, past or present, stays reachable for follow-up messages.
    db.insert_review_chat_message(review["id"], "user", user_message, now_iso)

    chat_history = [
        {"role": m["role"], "content": m["content"]}
        for m in db.list_review_chat_messages(review["id"])
        if m["role"] in ("user", "assistant")
    ][:-1]  # exclude the message just inserted -- chat_reply appends it separately

    retrieved_chunks = review_ingest.top_k_chunks(DEFAULT_USER_ID, user_message)
    memory = db.get_review_memory_summary(DEFAULT_USER_ID)
    memory_text = memory["summary_text"] if memory else None
    snapshot = json.loads(review["snapshot_json"])

    assistant_text, remembered_facts = review_claude.chat_reply(
        review["summary_text"], chat_history, retrieved_chunks, memory_text, snapshot, user_message,
    )
    db.insert_review_chat_message(review["id"], "assistant", assistant_text, now_iso)

    for fact in remembered_facts:
        try:
            vec = review_ingest.embed_texts([fact])[0]
            db.insert_document_chunk(
                DEFAULT_USER_ID, None, "user_enrichment", review["id"], None, fact, vec.tobytes(), now_iso,
            )
        except Exception as e:  # noqa: BLE001 - a failed enrichment write must not fail the chat reply itself
            print(f"app: user_enrichment write failed for fact {fact!r} ({e}).")

    return {"reply": assistant_text, "reply_chunks": review_stream.chunk_markdown_for_stream(assistant_text)}


class SPAStaticFiles(StaticFiles):
    """Serves the built React SPA, falling back to index.html for any GET that doesn't match a
    real file -- react-router's browserRouter (not hash-based) needs this: a hard refresh or
    direct link to e.g. /trades/42 is a real GET to the server, not a client-side navigation,
    and with plain StaticFiles that 404s instead of loading the app shell that would otherwise
    handle the route client-side."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or scope["method"] not in ("GET", "HEAD"):
                raise
            return await super().get_response("index.html", scope)


# backend/static/ (the old hand-written frontend) is kept in the repo purely for reference --
# not served, not deleted. The app serves the React SPA (backend/static_frontend/, built by the
# Dockerfile's frontend-build stage) exclusively.
app.mount("/", SPAStaticFiles(directory=os.path.join(os.path.dirname(__file__), "static_frontend"),
                               html=True), name="static")

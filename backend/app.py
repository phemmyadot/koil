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
the user clicks Refresh; the background loop wakes (every CHECK_INTERVAL).
A plain page load with existing computed data just reads it -- no cycle.
"""
import json
import os
import sqlite3
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
# meant a fresh batch of worker threads leaking FDs on every pass -- see backend/data.py.
_compute_executor = ThreadPoolExecutor(max_workers=COMPUTE_WORKERS)

# Bump whenever _compute_one()'s payload SHAPE changes (new/renamed/moved fields, not just new tickers/data) -- forces
# compute_all() to recompute every ticker once instead of reusing an old-shaped cached payload forever just because
# that ticker's bars happened not to change since the shape changed.
PAYLOAD_SCHEMA_VERSION = 6

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from yfinance.exceptions import YFRateLimitError

import backend.build_universe as build_universe
import backend.csv_export as csv_export
import backend.data as data
import backend.db as db
import backend.entry_estimate as entry_estimate
import backend.options_pricing as options_pricing
import backend.pdf_export as pdf_export
import backend.push as push
import backend.support_resistance as support_resistance
import backend.prebreak as prebreak
import backend.score as score
import backend.strategy_common as strategy_common
import backend.strategy_vcp as strategy_vcp
import backend.strategy_vcpo as strategy_vcpo
import backend.strategy_vexh as strategy_vexh


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


def _eval_strategy(module, ticker: str, bars, ind: dict | None) -> dict | None:
    """Independently error-isolated -- one strategy failing on a ticker shouldn't drop the others."""
    try:
        return module.evaluate(ticker, bars, ind=ind)
    except Exception:  # noqa: BLE001
        return None


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
        return ticker, payload, None, checksum
    except Exception as e:  # noqa: BLE001 - per-ticker failures must not break the page
        return ticker, None, str(e) or type(e).__name__, checksum


def _active_tickers() -> list[str]:
    """Candidate tickers (from the DB table -- see step 1 of the cycle in this module's
    docstring, NOT a live Yahoo screener call) plus any ticker a client has ever reported as
    watchlisted, plus any ticker with an open position -- both a watchlisted ticker and an
    actively-traded one must keep being fetched/computed even if they later fail the technical
    filter, or they silently go stale forever (an open position's daily marks and TP/stop
    alerts depend on this)."""
    candidates = db.get_candidate_tickers()
    watchlisted = db.get_watchlist_tickers()
    traded = [p["ticker"] for p in db.list_positions("open")]
    seen = set(candidates)
    extra = []
    for tk in watchlisted + traded:
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
        finally:
            with _compute_lock:
                _compute_progress = None
    finally:
        _compute_pass_lock.release()


TP_STOP_ALERT_THRESHOLDS = [30, 50, 70, 80, 90, 95]


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

    for f in ordered:
        fill_value = f["premium"] if instrument == "option" else f["price"]
        if f["kind"] == "entry":
            running_cost += fill_value * f["units"] * multiplier
            running_units += f["units"]
            open_lots.append({"fill": f, "units_remaining": f["units"]})
        elif f["kind"] == "exit":
            if running_units <= 0:
                continue  # malformed data guard -- an exit with nothing open, ignore rather than divide by zero
            avg_cost = running_cost / running_units
            exit_value = f["premium"] if instrument == "option" else f["price"]
            realized_pnl += (exit_value - avg_cost) * f["units"] * multiplier
            running_cost -= avg_cost * f["units"] * multiplier
            running_units -= f["units"]
            remaining_to_consume = f["units"]
            for lot in open_lots:
                if remaining_to_consume <= 0:
                    break
                consumed = min(lot["units_remaining"], remaining_to_consume)
                lot["units_remaining"] -= consumed
                remaining_to_consume -= consumed

    avg_cost = (running_cost / running_units) if running_units > 0 else None
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

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for position in open_positions:
        current = price_by_ticker.get(position["ticker"])
        if current is None:
            continue
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

        avg_cost = state["avg_cost"]
        tp, stop = position["tp_price"], position["stop_price"]
        # TP/stop are ALWAYS stock-price levels the user set, for both spot and option
        # positions -- same familiar input either way, see
        # docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md. For an option
        # position avg_cost is average premium, not a stock price, so comparing it directly
        # against a stock-price tp/stop (as this used to) mixed units and produced a meaningless
        # pct -- fixed by translating tp/stop into "what would the option be worth today if the
        # stock were at that level" (decay from entry to today already applied, since T is
        # measured as of today) and comparing THAT against avg_cost instead.
        if state["instrument"] == "option":
            today = datetime.now(timezone.utc).date()
            tp_value = _blended_option_value(state["open_lots"], tp, today)
            stop_value = _blended_option_value(state["open_lots"], stop, today)
            compare_value = _blended_option_value(state["open_lots"], current_price, today)
            if tp_value is None or stop_value is None or compare_value is None:
                continue  # no priced lots (missing iv_at_entry) -- can't derive an option-price target, skip alerting this cycle
            # _blended_option_value is per-share; avg_cost from replay_fills already has the
            # 100x contract multiplier baked in (see replay_fills's own docstring) -- scale up
            # to the same units before comparing, or every option position looks artificially
            # close to both tp and stop simultaneously.
            multiplier = state["multiplier"]
            tp_value *= multiplier
            stop_value *= multiplier
            compare_value *= multiplier
            pct_to_tp = (compare_value - avg_cost) / (tp_value - avg_cost) * 100 if tp_value != avg_cost else 0
            pct_to_stop = (avg_cost - compare_value) / (avg_cost - stop_value) * 100 if stop_value != avg_cost else 0
        else:
            # Sign-aware: a short/put-style position has tp below avg_cost and stop above --
            # these denominators are negative in that case, keeping pct_to_* positive as price
            # moves favorably either direction.
            pct_to_tp = (current_price - avg_cost) / (tp - avg_cost) * 100 if tp != avg_cost else 0
            pct_to_stop = (avg_cost - current_price) / (avg_cost - stop) * 100 if stop != avg_cost else 0

        _fire_threshold_alerts(position, "tp_progress", pct_to_tp, position["last_alert_tp_pct"])
        _fire_threshold_alerts(position, "stop_progress", pct_to_stop, position["last_alert_stop_pct"])


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
        data.warm_cache(active, force=force)
        fetch_time_after = data.last_fetch_time()

        with _compute_lock:
            computed_count = len(_computed)
        compute_caught_up = computed_count >= len(active) * 0.9  # allow for a few per-ticker errors

        if not force and fetch_time_before == fetch_time_after and compute_caught_up:
            print(f"app: nothing fetched this pass and compute is already caught up "
                  f"({computed_count}/{len(active)}) -- skipping compute_all()'s per-ticker check entirely.")
            return
        compute_all(force=force)
        try:
            _update_trade_marks_and_alerts()
        except Exception as e:  # noqa: BLE001 - a bad pass here must not affect the fetch/compute cycle above
            print(f"app: _update_trade_marks_and_alerts() failed ({e}); will retry next pass.")
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

        while True:
            time.sleep(data.CHECK_INTERVAL)
            try:
                refresh_and_compute()
            except Exception as e:  # noqa: BLE001 - one bad pass must not permanently kill the refresh loop
                print(f"app: background refresh loop pass failed ({e}); will retry next cycle instead of stopping.")
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
    if payload is None:
        raise HTTPException(status_code=422, detail=err or "no data")
    now = time.time()
    db.upsert_computed(ticker, payload, payload["date"], now, None)
    with _compute_lock:
        _computed[:] = [p for p in _computed if p["ticker"] != ticker] + [payload]
    return {"ticker": ticker, "price": payload["price"], "date": payload["date"], "found": True}


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
        entry_price=open_position["entry_price"],
        avg_mae_wins_pct=avg_mae_wins_pct,
        support_levels=nearest_price,
    )
    result["support_touches"] = nearest[0][1] if nearest else None
    return result


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


def _with_option_values(position: dict, fills: list[dict], marks: list[dict]) -> list[dict]:
    """Annotates each daily stock-price mark with the position's modeled option value that day
    (see _blended_option_value). Spot positions, or option positions with no fills carrying
    iv_at_entry, pass marks through unchanged."""
    if not fills or fills[0]["instrument"] != "option":
        return marks
    out = []
    for m in marks:
        state = replay_fills(fills, as_of_date=m["mark_date"])
        mark_date = datetime.strptime(m["mark_date"], "%Y-%m-%d").date()
        blended_value = _blended_option_value(state["open_lots"], m["close_price"], mark_date)
        if blended_value is None:
            out.append(m)
            continue
        out.append({**m, "option_value": round(blended_value, 4)})
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
    if payload is None:
        return
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.upsert_trade_daily_mark(position_id, payload["date"], payload["price"], now_iso)


def _position_with_state(position: dict) -> dict:
    """Annotates a position with its derived replay state (avg_cost, units_remaining,
    realized_pnl, instrument) -- computed fresh from position_fills every time, never cached
    beyond the stored status/closed_at (see db.py's positions docstring)."""
    fills = db.list_fills(position["id"])
    state = replay_fills(fills)
    return {
        **position,
        "instrument": state["instrument"],
        "units_remaining": state["units_remaining"],
        "avg_cost": round(state["avg_cost"], 4) if state["avg_cost"] is not None else None,
        "realized_pnl": round(state["realized_pnl"], 2),
        "fill_count": len(fills),
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
def list_positions(status: str | None = None):
    if status not in (None, "open", "closed"):
        raise HTTPException(status_code=400, detail=f"invalid status: {status!r}")
    return [_position_with_state(p) for p in db.list_positions(status)]


@app.get("/api/positions/summary")
def positions_summary():
    """Win rate / avg return -- ticker-position-level only, not filterable by strategy, since a
    position can span fills from multiple strategies (see the design doc's grouping decision).
    'Simple math' per the design brief: pnl / cost basis, no Black-Scholes needed for the %
    itself, only for pricing an option's exit VALUE (handled inside replay_fills)."""
    closed = [_position_with_state(p) for p in db.list_positions("closed")]
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
    return {
        "open_count": len(db.list_positions("open")),
        "closed_count": len(closed),
        "win_count": len(wins),
        "win_rate_pct": round(len(wins) / len(returns) * 100, 1) if returns else None,
        "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
    }


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

# Market-hours-aware background fetch — design

## Problem

The background loop (`backend/app.py::_on_startup`'s `loop()`) currently wakes
every `CHECK_INTERVAL` (2 hours, `backend/data.py`) and unconditionally
gap-fetches + recomputes every active ticker, 24/7, every day of the week.
Outside US market hours (nights, weekends, holidays) this burns Yahoo fetch
calls and compute cycles for data that cannot have changed since the last
regular-session close — every one of those wakes fetches an empty gap and
does real (if cheap) work for nothing.

Wanted: fetch every `X` minutes (env-configurable, default 60) **while the
market is open**, and **exactly once** after it closes each day to pick up
the final close bar — then go quiet until the next session opens.

## Scope

This changes only *when* the existing loop body runs, not what it does once
triggered. `refresh_and_compute()`, `compute_all()`, `warm_cache()`,
`_update_trade_marks_and_alerts()` — none of these change. This is a gating
condition wrapped around the existing `while True: time.sleep(...); refresh_and_compute()`
loop in `_on_startup()`.

## Market hours definition

- **Session**: 9:30 AM–4:00 PM **America/New_York** (`zoneinfo`, already a
  dependency — `app.py` already imports `ZoneInfo`/`ZoneInfoNotFoundError`),
  Monday–Friday only. DST is handled automatically by `zoneinfo` — no manual
  offset math.
- **Holidays**: out of scope for v1. NYSE market holidays (Thanksgiving, July
  4th, etc.) will be treated as ordinary weekdays — the loop will attempt its
  once-after-close fetch and get a same-as-yesterday gap response, which is
  harmless (same cost as today's always-on behavior), just not fully
  optimized. Flagged as a known gap, not silently pretended away — a future
  iteration could pull a holiday calendar (e.g. `pandas_market_calendars`) if
  the wasted fetch actually matters.
- **Early closes** (e.g. day after Thanksgiving, 1PM close): out of scope for
  v1, same reasoning — the loop will keep polling until 4PM believing the
  market is still open, doing a few extra no-op fetches. Not silently
  pretended away either; noted as a v2 candidate.

## Behavior

Three states the loop can be in when it wakes:

1. **Market open** (weekday, 9:30–4:00 ET): fetch on the configured cadence
   (`MARKET_OPEN_FETCH_INTERVAL_MINUTES` env, default 60 → seconds internally).
   This REPLACES `CHECK_INTERVAL` as the sleep duration while open, it does
   not run in addition to it.
2. **Market closed, no close-fetch done yet for this close period**: fetch
   once, immediately (or on the next wake, whichever is simpler to implement
   — see Implementation notes), then record that this close period's fetch
   is done.
3. **Market closed, close-fetch already done for this close period**: skip
   entirely. Sleep until the next check is worth making, rather than
   busy-waking every `CHECK_INTERVAL` for nothing.

"This close period" means the stretch from one 4:00 PM ET close to the next
9:30 AM ET open (including weekends — Friday's 4PM close through Monday's
9:30AM open is still just ONE close period, so the weekend doesn't trigger
three redundant close-fetches for Fri/Sat/Sun).

### "If the last fetch is outside market close, skip"

Interpreting the user's phrasing precisely: track the timestamp of the last
*close-period* fetch (a new, separate marker from the existing
`db.get_max_fetched_at()`, which the open-hours cadence also touches).  On
each wake while the market is closed:

- If there is no recorded close-fetch since the most recent market close
  boundary → do the fetch, then stamp `last_close_fetch_at = now`.
- If `last_close_fetch_at` is already at or after the most recent close
  boundary → skip (already done for this period).

This is what "if the last fetch is outside market close, skip" means in
practice: a last-close-fetch timestamp that falls *before* the current close
period's start is stale (do the fetch); one that falls *within/after* it is
current (skip).

## New config

| Name | Default | Meaning |
|---|---|---|
| `MARKET_OPEN_FETCH_INTERVAL_MINUTES` | `60` | Minutes between fetches while the market is open. `X` from the request. |

No new env var for the market-closed cadence — closed-market wake-checking
itself can run on a short, cheap fixed interval (e.g. every 5–10 minutes, far
cheaper than a real fetch) purely to notice "market just opened" or "haven't
done today's close-fetch yet" promptly; the fetch itself only actually runs
once per close period regardless of how often the loop wakes to check.

## Where this lives

- `backend/market_hours.py` (new, small, no dependencies beyond `zoneinfo`):
  - `is_market_open(now: datetime) -> bool`
  - `most_recent_close_boundary(now: datetime) -> datetime` — the timestamp
    of the most recent 4:00 PM ET close at or before `now` (if market is
    currently open, this is *yesterday's* close, i.e. the start of the
    current open session's "last close" reference point — needed so a
    same-day post-close fetch and the next morning's open don't get
    confused about which period they're in).
  - Pure functions, easily unit-testable with fixed `datetime` inputs — no
    reliance on `datetime.now()` internally, caller passes `now` in.
- `backend/db.py`: one new key/value row (or reuse the existing simple
  key-value table if one exists, else a tiny dedicated table) —
  `last_close_fetch_at` (ISO timestamp, nullable) — persisted so a process
  restart doesn't forget it already ran today's close-fetch and redo it
  immediately on boot.
- `backend/app.py::_on_startup`'s `loop()`: replace the unconditional
  `time.sleep(data.CHECK_INTERVAL)` with a small dispatch:
  ```
  while True:
      now = datetime.now(ZoneInfo("America/New_York"))
      if market_hours.is_market_open(now):
          refresh_and_compute()
          time.sleep(MARKET_OPEN_FETCH_INTERVAL_SECONDS)
      else:
          if db.get_last_close_fetch_at() is stale vs. most_recent_close_boundary(now):
              refresh_and_compute()
              db.set_last_close_fetch_at(now.isoformat())
          time.sleep(CLOSED_MARKET_POLL_SECONDS)  # e.g. 300s, just to notice state changes promptly
  ```

## Interactions with existing behavior

- **Manual Refresh button** (`force=True` path): unaffected, always runs
  regardless of market hours — a user-initiated refresh should never be
  blocked by this gating. This design only touches the *automatic* loop.
- **`data.warm_cache()`'s own `max_fetched_at` skip-check**: unaffected,
  stays as an independent safety net (protects against overlapping/duplicate
  fetches within the same `CHECK_INTERVAL`-ish window) — this design changes
  when the loop calls `refresh_and_compute()`, not what `warm_cache()` does
  once called.
- **Push/notification/alert cadence**: `_update_trade_marks_and_alerts()`
  runs inside `refresh_and_compute()`'s existing pass, so alert timeliness
  during market hours actually *improves* (checks every `X` minutes instead
  of every 2 hours by default), and naturally goes quiet overnight/weekends
  when there's nothing new to alert on anyway.
- **`CHECK_INTERVAL` constant**: stays as-is for anything else that
  references it (the docstring in `data.py` will need a small update noting
  the loop's actual cadence is now market-hours-aware, not a flat interval).

## Open questions for the user

1. Confirm 60 minutes is the right *default* if `MARKET_OPEN_FETCH_INTERVAL_MINUTES`
   is unset (matches the "60 mins = 1hr" example given).
2. Is holiday/early-close handling worth building now, or acceptable to skip
   for v1 as scoped above?
3. Closed-market poll cadence (how often the loop wakes just to check "is it
   time yet") — proposed 5 minutes, cheap and just a local clock/DB check,
   no network call. Fine, or prefer something else?

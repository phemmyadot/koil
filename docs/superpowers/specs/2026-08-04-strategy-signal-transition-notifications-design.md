# Strategy state-change notification — design

## What's wanted

One notification whenever a strategy's state on a ticker changes, for any
ticker in the universe (not just watchlisted/traded ones), covering the
full lifecycle in order:

```
NO SIGNAL -> PENDING -> Open -> TP -> NO SIGNAL (exit)
```

Basic version: no per-transition wording, no separate notification kinds —
just "this strategy's state changed, here's what it is now."

## The state, precisely

Every strategy (`strategy_vcp.py`, `strategy_vcpo.py`, `strategy_vexh.py`)
already computes everything needed on every pass, via
`strategy_common.evaluate_strategy()`. Two fields in each strategy's payload
fully determine the dashboard's own displayed state
(`TickerCard.tsx`/`StrategyBadgeRow.tsx` already derive the same thing for
the badge shown on screen):

- `signal_today: bool`
- `open_position: dict | None` (has `days_held`, `to_tp_pct`, etc. when set)
- `verdict: str` — `"NO SIGNAL"`, `"TAKE"`, `"SKIP"`, `"IN TRADE"`, `"TP HIT"`

`verdict` alone is a clean single string that already encodes the whole
state machine except PENDING, which the backend doesn't currently model as
part of `verdict` (`TAKE`/`SKIP` covers "fresh signal today, not yet in the
strategy's simulated position" — that IS what the dashboard calls PENDING,
just under two different verdict names depending on whether the ticker's
own backtest history shows an edge). For this notification, collapse to one
`state` label per (ticker, strategy):

| `state` | Condition |
|---|---|
| `NO SIGNAL` | `verdict == "NO SIGNAL"` |
| `PENDING` | `verdict in ("TAKE", "SKIP")` (signal fired, not in simulated position yet) |
| `OPEN` | `verdict == "IN TRADE"` |
| `TP` | `verdict == "TP HIT"` |

This is a straight relabeling of the existing `verdict` string — no new
computation, just a 4-branch mapping used only at the notification-fire
point.

## Detecting a change

`compute_all()` (`backend/app.py`) already holds the previous pass's full
payload set in memory at exactly the point each ticker gets recomputed
(`prior_by_ticker`, used today for the reuse-vs-recompute decision) — no
new storage needed:

```python
prior_by_ticker = {p["ticker"]: p for p in _computed}  # already exists

# inside the per-ticker recompute loop, once the new payload is built:
for strat_key in _STRATEGY_MODULES:
    prior_state = _strategy_state((prior_by_ticker.get(tk) or {}).get(strat_key))
    new_state = _strategy_state(payload.get(strat_key))
    if prior_state is not None and new_state != prior_state:
        _fire_strategy_state_alert(tk, strat_key, new_state, now_iso)
```

`_strategy_state(s)` is the 4-branch mapping above, `None` if `s` is `None`
(ticker had no prior payload at all — e.g. its very first successful
compute; nothing to compare against, so no alert fires on that first pass).

Only runs for tickers actually recomputed this pass (the `to_compute`
branch) — a reused payload (bars checksum unchanged) can't have a state
change by construction, so it's correctly skipped without an extra check.

## Which tickers / strategies

All of them — every ticker in `_active_tickers()` that gets recomputed,
across all three strategies (VEXH, VCP, VCPO) independently. Matches "any
strategy," not scoped to watchlist/traded-only. (Noise at full-universe
scale is a real concern — flagged below, but the basic version as
requested doesn't scope it down.)

## Notification content

Same delivery mechanism the app already has —
`db.insert_notification()` + `push.send_push_to_all()`, same pattern
`_fire_threshold_alerts()` uses today for TP/stop progress on real
positions.

**`kind`**: `"strategy_state"` (one kind for all four states — the state
itself is in the message, not encoded as separate kinds, per "basic" scope).

**Message**: `"{ticker} — {strategy} is now {state}"`, e.g.
`"NVDA — VCPO is now PENDING"`, `"NVDA — VCPO is now OPEN"`,
`"NVDA — VCPO is now TP"`, `"NVDA — VCPO is now NO SIGNAL"`.

`{strategy}` = existing human label (`stratLabel()` on the frontend side —
"VCPO", "VCP", "VEXH").

### `position_id` — doesn't apply here

`db.insert_notification()`'s current signature is `(position_id, kind, pct,
message, now_iso)` — built for real Trades-tab positions. A strategy state
alert has no real position to attach to (this is explicitly about
NOT-yet-confirmed signals). Simplest fix: make `position_id` nullable, and
`NotificationPanel`'s row click links to the ticker's dashboard card
(`/?ticker=X` or however the dashboard already deep-links, if it does)
instead of `/trades/{id}` when `position_id` is null. `pct` also doesn't
apply — pass `None`.

## Where this lives

Inside `compute_all()`'s per-ticker loop (`backend/app.py`), right where
the fresh payload is finalized and compared for reuse — same place
`prior_by_ticker` is already read. New helpers:

- `_strategy_state(payload: dict | None) -> str | None`
- `_fire_strategy_state_alert(ticker: str, strat_key: str, new_state: str, now_iso: str) -> None`

## Open questions for the user

1. **Noise at scale**: ~1400 tickers × 3 strategies, transitioning in and
   out of PENDING/OPEN/TP/NO SIGNAL independently all day — this is a much
   higher-volume notification stream than today's (which only watches the
   user's own handful of real positions). Is full-universe scope actually
   wanted for v1, or should this start out watchlist-only and expand later?
2. **`position_id` nullable, or separate table?** Touches the existing
   `notifications` schema/API — confirming nullable is fine before I change
   a contract other code already depends on.
3. Silent on the very first compute after a cold start/restart (no prior
   payload to compare against) — confirming that's the right call, since
   otherwise every ticker would fire once on every process restart.

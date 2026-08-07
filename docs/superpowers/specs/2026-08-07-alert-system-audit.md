# Alert System Audit — Proposed Alert Framework vs. Current State

Status: reference/audit only. No implementation has been done against this document — it
records what exists today so future work can pick up any row without re-deriving it.

## Context

A comprehensive alert framework was proposed covering options, spot, signal, and market-context
conditions, each with a priority tier (🔴 critical / 🟡 warning / 🟢 informational) and a specific
message. This audit checks each proposed condition against the actual codebase (as of this
session, which added `current_iv`/`iv_at_entry` live-quote fields to `Position` via
`backend/data.py`'s `get_live_option_price()` and `backend/app.py`'s `_position_with_state()`).

Three possible states per item:
- **Exists / already alerts** — the condition is both computable and already fires a
  notification today.
- **Partial** — the underlying data exists (or is trivially derivable), but no alert function
  reads it; or an alert exists but doesn't distinguish the proposed condition precisely.
- **Missing** — neither the data nor the alert exists.

## Options alerts

| Condition | Status | Detail |
|---|---|---|
| +100% unrealized → 🔴 | **Exists** | `OPTION_GAIN_ALERT_THRESHOLDS = [50, 75, 90, 100]`, fired by `_fire_option_gain_alert()` (`app.py`), called every cycle from `_update_trade_marks_and_alerts()`. Already covers 100%. |
| +50% gain in < 1/3 avg days → 🟡 | **Missing** | Gain% (option_gain alert), `days_held` (`open_position.days_held`), and `avg_trade_days` (per-strategy backtest stat) all exist independently, but nothing joins gain% with days-held-so-far for an option position. Needs new logic. |
| IV Δ < -10% → 🔴 / IV Δ > +15% → 🟢 | **Partial** | `current_iv`/`iv_at_entry` computed this session in `_position_with_state()`, rendered in `PositionDetailPage`, `OptionsPositionsTable`, and `tradesExport.ts` (thresholds already match: -10/+15, crush/spike icons ⚠️/📈). **No alert/push notification reads these fields** — display-only today. |
| <15 days to expiry + unrealized < 0% → 🔴 | **Partial** | `expiry_date` exists per fill; `days_to_expiry` is computed inline (twice) purely as an input to Black-Scholes pricing, never stored/exposed as a standalone alertable value, never combined with unrealized%. |
| <7 days to expiry (any position) → 🔴 | **Missing** | Same `days_to_expiry` computation exists internally but only feeds pricing math. No expiry countdown alert exists — only the earnings countdown alert exists, and it's a distinct code path (different data, different trigger). |

## Spot alerts

| Condition | Status | Detail |
|---|---|---|
| Open MAE > avg_mae_wins_pct → 🟡 | **Missing** | `avg_mae_wins_pct` is a historical backtest statistic only (computed once per strategy across all past trades). No live/current MAE (running low-water-mark since entry) is tracked for an open position. |
| Open MAE > 1.5× avg_mae_wins_pct → 🔴 | **Missing** | Same root gap as above — no live MAE tracking exists at all, so this can't be derived without building that first. |
| Trend filter → BEARISH on open position → 🔴 | **Partial** | `prebreak.state == "BEARISH"` is computed per ticker (`prebreak.py`) and exposed in the ticker payload. No code cross-references an open position's ticker against its current trend state to fire an alert — the data and the position both exist, they're just never joined. |
| Unrealized > +15% (spot/VCPO) → 🟡 | **Missing** | No spot-side equivalent of `_fire_option_gain_alert` exists. Generic unrealized% is already on `Position`, so this would follow the same shape as the option alert, just for spot instruments. |

## Signal alerts

| Condition | Status | Detail |
|---|---|---|
| VCPO/VEXH fires on watchlisted ticker → 🔴 | **Exists** | `_fire_strategy_state_alert()` fires on verdict-state transitions for all active/watchlist tickers already — covers this case today, not watchlist-exclusive but watchlist tickers are included in the active set. |
| Phase upgrades to PRE-BREAKOUT(4) → 🟡 | **Partial** | `prebreak.state` values (`"PRE-BREAKOUT"`, `"BREAKOUT"`) and their numeric mapping already exist and are exposed per ticker. The only transition-alert mechanism (`_fire_strategy_state_alert`) is keyed off `verdict`, not `prebreak.state` — nothing diffs prior-vs-new phase to detect an upgrade. |
| Phase upgrades to BREAKOUT(5) → 🔴 | **Partial** | Same gap as above. |
| Score reaches 9/10 → 🟡 | **Partial** | `setup_score` is computed per strategy/ticker. No threshold-crossing alert exists — the dashboard's "fire" card styling is a frontend-only visual cue, not a notification. |

## Market context alerts

| Condition | Status | Detail |
|---|---|---|
| Fed announcement day (known dates) → 🟡 | **Missing** | No macro event calendar (Fed, CPI, or otherwise) exists anywhere in the backend. Would require a new, manually-maintained or externally-sourced date list. |
| Earnings within 5 days, open position → 🔴 | **Partial** | Earnings countdown alert already exists (`days_to_earnings`, fires days 5→0, per-ticker-per-day dedup) — but it fires identically for spot and options; it does not distinguish "open options specifically" as the proposal asks. |
| Oil spike > +3% single day → 🟡 | **Partial** | Oil (USO) is already tracked and its day-over-day `change_pct` computed in `_build_market_context()` for the daily review's display. No threshold alert fires on that value — display-only today. |
| Nasdaq > -1.5% intraday → 🟡 | **Partial** | Same as oil — QQQ is tracked/displayed with `change_pct`, no alert wired to it. Note also: the existing computation is close-to-close (daily), not intraday: a true intraday check would need a different data cadence than the current once-per-cycle background loop provides. |

## Summary

- **2 of 16** conditions already fully alert today: options +100% gain, strategy signal firing
  on watchlisted tickers.
- **9 of 16** are "partial" — the underlying data already exists (often exactly because of this
  session's IV work, or pre-existing fields like `prebreak.state`/`setup_score`/market context
  `change_pct`), but no alert-firing logic reads it yet, or an existing alert doesn't distinguish
  the specific condition proposed (e.g. options vs. spot on the earnings alert).
- **5 of 16** are genuinely missing both data and alerting: the gain+days-held joint condition,
  expiry countdown, live/running MAE tracking for open positions (two conditions depend on this
  single gap), and the Fed/macro calendar.

## Priority-tier mapping (as proposed, unchanged)

🔴 Critical: BEARISH-on-open-position, IV crush < -10%, options +100%, earnings <5 days with open
options, <7 days to expiry.
🟡 Warning: options +50% early, MAE exceeded average, PRE-BREAKOUT/BREAKOUT on watchlist, Fed day,
<15 days to expiry with a loss.
🟢 Informational: IV spike (favorable), score reaches 9/10, strong spot gain approaching TP.

## Retrospective — cases this session that these alerts would have caught

| Alert | Would have triggered on | What it would have changed |
|---|---|---|
| Options +100% | VTRS reaching ~$1.10 | Exit signal before the position decayed to a loss |
| IV crush < -10% | VTRS's post-earnings IV drop (measured this session at -15.7pts) | Confirmed exit signal independent of price alone |
| Earnings <5 days, open options | VTRS's Aug 1 earnings date | Pre-emptive exit ahead of a binary earnings event |
| BEARISH trend filter | BKR (day 1), CRDO, VIST | Faster exits on trend-filter failures |
| BREAKOUT(5) phase transition | OMER, BKR (Jul 27) | Same-day notification instead of manual discovery |

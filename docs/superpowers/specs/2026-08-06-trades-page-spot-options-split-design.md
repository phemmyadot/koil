# Trades Page: Spot/Options Split

Splits the Trades page into two parallel views — Spot and Options — each with its own summary
stats, P&L chart, and positions table, switched by a single filter at the top. Motivated by two
gaps found while working on the daily review chatbot: `positions_summary()`'s
`total_unrealized_pnl` was deliberately spot-only (an option's `avg_cost` isn't comparable to a
raw stock price without re-pricing the contract via the existing Black-Scholes model), and the
Trades table showed `—` for every option position's unrealized P&L for the same reason. Rather
than bolt option-awareness onto the existing single-table/single-summary UI, this splits the
page cleanly along the one axis that actually needs different math and different columns.

## Backend

### `GET /api/positions?type=spot|options`

`list_positions()` gains a `type` query param (alongside the existing `status` param). Filters
`db.list_positions(status)` by `instrument` before returning — same `_position_with_state()`
per position, no new fields. No `type` = today's behavior (both types together), for any
existing caller that doesn't opt into the split.

### `GET /api/positions/summary?type=spot|options`

`positions_summary()` gains the same `type` param. The function's shape stays the same
(filter → compute → count → summarize, one pass): filter positions to the requested type
*first*, then run the existing open/closed/win-rate/avg-return/realized counting logic over
that subset unchanged. The only branch is in the unrealized step:

- `type=spot` (or the existing default): current behavior — `(current_price - avg_cost) *
  units_remaining`, current_price from `_computed`/`data.get_bars`.
- `type=options`: same shape, but unrealized uses `_blended_option_value()` (already proven —
  same function `PositionDetailPage.tsx`'s per-position chart and `positions_pnl_series()`
  already use) against each option position's `open_lots`, scaled by `state["multiplier"]`
  the same way `_position_pct_to_tp_stop()` already does for TP/stop progress.

No `type` param = today's spot-only `total_unrealized_pnl` (unchanged default, backward
compatible with any caller not yet updated).

### `GET /api/positions/pnl-series?type=spot|options`

`positions_pnl_series()` gains the same `type` param. Same filter → compute pattern: filter
`positions` to the requested type before the existing per-date `replay_fills` loop runs — the
loop already branches internally on `state["instrument"] == "option"` for marks/pricing, so
filtering the input list is the only change needed. Returns the same `{dates, realized[],
unrealized[]}` shape, just scoped to one instrument type.

## Frontend

### Page load

On mount, `TradesPage` fires all six calls in parallel — `positions`, `summary`, and
`pnl-series`, each once for `type=spot` and once for `type=options`. Both result sets are held
in state from the start; the top-of-page filter is a pure display toggle (which summary row /
chart / table is visible), not a new fetch. Switching the filter is instant, no loading state.

### Chart

One `PnlChart`, four lines: spot realized, spot unrealized, options realized, options
unrealized — all four always plotted together (not toggled by the spot/options filter, which
only affects the summary stats and table below it).

### Summary stats

Two stat rows, "Spot" and "Options," using the respective `summary?type=...` response —
same fields for both (open/closed/win rate/avg return/total realized/total unrealized), no
special-casing beyond which fetch backs which row.

### Positions tables

Two separate table components (or one table component taking a `mode` prop), selected by the
top filter — never shown side by side, never merged into one table with a type column.

**Spot table** — same columns as today's single table, minus the `Instrument` column (redundant
once tables are split by type): Ticker, Status, Units, Avg Cost, TP, Stop, Last / Unrealized %,
Realized $, actions.

**Options table** — different column set, since options don't have a stock-comparable Avg Cost
and do have contract terms the spot table never showed:
Ticker, Status, Units, Premium, TP, Stop, Last (option value) / Unrealized (from premium),
Realized $, actions.
"Last" is the modeled option value (`_blended_option_value()`, same source as the summary's
options `total_unrealized_pnl`); "Unrealized" is computed against the position's own entry
premium, not a stock-price comparison.

The exit-form, cancel button, and lazy per-position fills fetch (fetched only when a row's Exit
form opens, per the earlier optimization) work identically in both tables — no behavior change
there, just two different header/column layouts feeding the same row-level actions.

## What stays the same

- The lazy fills-on-exit-click behavior (added earlier this session) is unchanged in both tables.
- No `type` param on any of the three endpoints preserves today's behavior exactly, so this is
  additive, not breaking, for any caller that doesn't pass it.
- `_blended_option_value()` and the multiplier-scaling logic are reused as-is from
  `PositionDetailPage.tsx`'s existing per-position chart and `positions_pnl_series()`'s existing
  per-date option pricing -- no new pricing logic, just wiring it into two more call sites.

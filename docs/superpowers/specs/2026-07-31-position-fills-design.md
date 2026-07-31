# Positions & Fills — Partial Exits, Added Entries, Weighted-Average Cost

Date: 2026-07-31
Status: Design, not implemented
Supersedes: the single-entry/single-exit `taken_trades` model from
[2026-07-31-trade-tracking-design.md](2026-07-31-trade-tracking-design.md)

## Background

`taken_trades` (and the `/api/trades*` routes, `trades.html`, `trade.html` built on top of it)
models a trade as exactly one entry and one exit — `entry_price`, `entry_date`, `exit_price`,
`exit_reason` are single scalar columns on one row. That's wrong for how positions are actually
managed:

- **Adding to a position**: buying more of a ticker at a later, different price (scaling in).
- **Partial exits**: selling only some of a position (taking partial profit, trimming risk),
  leaving the rest open.

Both need the same underlying fix: a position is a *sequence of fill events* (entries and
exits), not one row with two scalar price fields. Cost basis, realized P&L, and unrealized
P&L all need to be computed by replaying that sequence, not read off two columns.

## Grouping: ticker only, not ticker + strategy

A position is keyed on **ticker alone** — not `(ticker, strategy_key)`. If VEXH signals an AA
entry and VCPO later signals adding to AA, those fills both belong to the *same* AA position;
there's one average cost, one open/closed status, one TP/stop, for "my AA position" regardless
of which strategy's signal contributed which fill.

**Consequence, explicitly accepted**: per-strategy win-rate/avg-return as a *position-level*
rollup stops being meaningful once a position can span strategies — you can't cleanly attribute
a partial exit's realized P&L back to "which strategy's shares were these" without arbitrary
lot-matching rules. `strategy_key` and `signal_date` are still recorded on every individual
fill (for history/display — "this fill came from a VEXH signal on 2026-07-20"), but summary
statistics (win rate, avg return) are computed at the position level only, not filtered or
grouped by strategy. This is a real capability loss versus today's per-strategy summary stats
and is being made deliberately, not accidentally.

## Position lifecycle: closing is terminal, not reopenable

Once a position's `units_remaining` hits exactly 0, that position is **done**. A later entry
signal on the same ticker starts a **new** position row — it does not reopen the closed one.
History shows "AA campaign #1: +$493, closed 2026-07-20" and "AA campaign #2: -$120, closed
2026-08-03" as two separate rows, not one row with a confusing flat gap in the middle and two
disconnected average-cost histories glued together. Only one position per ticker may be `open`
at a time; a second entry signal on a ticker that already has an open position is an *add to
that position*, not a new one.

## Data model

Two tables replace `taken_trades`.

### `positions` — one row per campaign

```
id              INTEGER PRIMARY KEY
ticker          TEXT NOT NULL
status          TEXT NOT NULL DEFAULT 'open'   -- 'open' | 'closed'
tp_price        REAL NOT NULL                  -- position-level, same as today's tp_price
stop_price      REAL NOT NULL                  -- position-level, same as today's stop_price
opened_at       TEXT NOT NULL                  -- timestamp of the first fill
closed_at       TEXT                           -- timestamp units_remaining hit 0, NULL while open
last_alert_tp_pct    REAL                      -- alert-engine bookkeeping, same role as today
last_alert_stop_pct  REAL
notes           TEXT
```

`UNIQUE` partial-index equivalent enforced in application code (SQLite has no native partial
unique index pre-3.8, but this project's SQLite is recent enough — `CREATE UNIQUE INDEX
idx_positions_one_open_per_ticker ON positions(ticker) WHERE status = 'open'` is directly
usable): guarantees only one open position per ticker at a time, so "is there already an open
AA position to add to" is a single indexed lookup, not a scan.

### `position_fills` — one row per entry or exit event, flat, append-only

```
id              INTEGER PRIMARY KEY
position_id     INTEGER NOT NULL               -- FK to positions.id
strategy_key    TEXT NOT NULL                  -- which strategy's signal produced this fill
signal_date     TEXT NOT NULL
kind            TEXT NOT NULL                  -- 'entry' | 'exit'
fill_date       TEXT NOT NULL                  -- when this fill actually happened
price           REAL NOT NULL                  -- stock price always, per the existing
                                                -- convention (see trade-tracking-design.md) --
                                                -- never a manually-entered option fill
units           REAL NOT NULL                  -- shares (spot) or contracts (option)
instrument      TEXT NOT NULL                  -- 'spot' | 'option'
exit_reason     TEXT                           -- 'tp' | 'stop' | 'manual' | 'expired', NULL for entries
-- option-only, NULL for instrument='spot'
opt_side        TEXT
opt_type        TEXT
strike          REAL
premium         REAL                           -- option premium at THIS fill's price/date --
                                                -- an added entry at a later date has its own
                                                -- premium, not the position's original one
expiry_date     TEXT
iv_at_entry     REAL                           -- IV as of THIS fill, same reasoning as premium
notes           TEXT
created_at      TEXT NOT NULL
```

No `UNIQUE` constraint on `(ticker, strategy_key, signal_date)` like today's `taken_trades` --
that constraint doesn't make sense anymore (the same strategy could legitimately signal an add
on the same ticker at two different future dates). Dedup at the application layer is instead:
reject an identical `(position_id, kind, fill_date, strategy_key, signal_date)` fill as a
probable double-submit, not a hard schema constraint.

`trade_daily_marks` and `notifications` get a straightforward rename of their foreign key
(`trade_id` → `position_id`), no shape change otherwise — a daily mark and a TP/stop alert are
still one-per-position concepts, unaffected by fills being tracked separately.

## Aggregation math (computed on read, not stored)

Given a position's fills sorted by `fill_date`, replay in order maintaining running state:

```python
running_units = 0.0
running_cost  = 0.0     # total $ cost basis of currently-held units
realized_pnl  = 0.0

for fill in fills_sorted_by_date:
    if fill.kind == "entry":
        running_cost  += fill.price * fill.units
        running_units += fill.units
    elif fill.kind == "exit":
        avg_cost = running_cost / running_units        # cost basis at the moment of this exit
        realized_pnl += (fill.price - avg_cost) * fill.units
        running_cost  -= avg_cost * fill.units          # remove only the cost of what was sold
        running_units -= fill.units
```

This is **weighted-average-cost** accounting (the same method brokerages use for a cash
account), not FIFO/LIFO lot matching — a partial exit's cost basis is the blended average of
every entry so far, not "the oldest shares first." Chosen over lot-matching because it needs no
per-lot state (just two running numbers) and matches "aggregate on read" — the explicit design
goal. FIFO lot-matching would need to track which specific entry-lot's units remain, which is
meaningfully more state and complexity for a benefit (tax-lot-accurate realized P&L) this app
has no use for.

**Key correctness property, worth stating explicitly**: after a partial exit, the average cost
of the *remaining* units is unchanged — only removing `avg_cost * exit.units` (not
`exit.price * exit.units`) from `running_cost` guarantees `running_cost / running_units` stays
identical before and after the exit. Verified in the worked example below.

**Current average cost** (for display, "Avg cost" stat box) = `running_cost / running_units`
after processing all fills to date.

**Unrealized P&L as of a given day** = `(mark_price_on_that_day - avg_cost_as_of_that_day) *
units_remaining_as_of_that_day` — replay the fills up through that day only, using that day's
`trade_daily_marks` price as `mark_price`.

**Total P&L at any point** = `realized_pnl_so_far + unrealized_pnl_now`.

### Worked example

Buy 100 @ $40 (day 1), buy 50 more @ $44 (day 5), sell 60 @ $46 (day 10), currently 90 shares
left, marked at $48 today.

```
Day 1 entry:  running_units=100  running_cost=4000                    avg_cost=40.00
Day 5 entry:  running_units=150  running_cost=4000+2200=6200          avg_cost=41.33
Day 10 exit 60 @ 46:
    avg_cost = 6200/150 = 41.33
    realized_pnl += (46 - 41.33) * 60 = 280.20
    running_cost  -= 41.33 * 60 = 2480  ->  running_cost = 3720
    running_units -= 60                ->  running_units = 90
    check: 3720 / 90 = 41.33  (avg cost of the remainder is unchanged -- correct)

Today, mark @ 48:
    unrealized_pnl = (48 - 41.33) * 90 = 600.30

Total P&L = 280.20 (realized) + 600.30 (unrealized) = 880.50
```

### Options

Same math, with `price` = premium (an entry fill) or the exit's realized/modeled premium (an
exit fill), and `units` = contracts. The existing convention that `price` recorded on a fill is
*always the stock price*, never a manually-entered option fill (see
[trade-tracking-design.md](2026-07-31-trade-tracking-design.md) and the +1600% bug it fixed),
still holds per-fill: an option exit fill's `price` column is the stock price at exit, and the
option's actual realized value is computed the same way `exit_option_value` is computed today
(Black-Scholes off that stock price, that fill's `strike`/`iv_at_entry`, and DTE remaining at
that fill's `fill_date`) — not stored, derived at read time same as now.

## Status derivation

`status` is a stored column on `positions` (not fully derived) for one practical reason:
querying "all open positions" needs to be a fast indexed lookup (feeds `_active_tickers()`,
the alert engine, the Trades page's default filter), not a full fills-replay per ticker on
every request. It's kept in sync procedurally: every fill-insert recomputes
`units_remaining` for that position and flips `status`/`closed_at` accordingly in the same
transaction — the stored value is a cache of the replay result, always re-derivable from
`position_fills` if it ever drifts (e.g. a manual DB fix), never a second source of truth a
fill could disagree with.

## API surface (replacing today's `/api/trades*`)

- `POST /api/positions` — create a new position from a first entry fill. Body: ticker,
  strategy_key, signal_date, instrument, fill_date, price, units, tp_price, stop_price, +
  option fields. 409 if an open position already exists for that ticker (use "add entry"
  instead).
- `POST /api/positions/{id}/fills` — append a fill (entry or exit) to an existing position.
  Body: kind, strategy_key, signal_date, fill_date, price, units, + option fields for an entry.
  - `kind=entry`: adds to the position (scale-in). Position must be open.
  - `kind=exit`: requires `exit_reason`; `units` may be less than `units_remaining` (partial)
    or equal to it (full — flips the position to `closed`). Rejects `units >
    units_remaining` (can't exit more than is held).
- `GET /api/positions?status=open|closed` — list positions with derived fields (avg_cost,
  units_remaining, realized_pnl, unrealized_pnl as of the latest mark) computed via the replay.
- `GET /api/positions/{id}` — one position, same derived shape, plus its full `fills` list.
- `PATCH /api/positions/{id}` — edit `tp_price`/`stop_price`/`notes` (position-level fields
  only; individual fills are corrected via a separate fill-edit endpoint, not through the
  position).
- `PATCH /api/positions/{id}/fills/{fill_id}` — correct a single fill's recorded values (the
  edge-case-correction capability from the earlier design, now scoped to one fill instead of
  the whole trade).
- `DELETE /api/positions/{id}/fills/{fill_id}` — remove a single erroneous fill (e.g. logged
  against the wrong position by mistake), recomputing the position's status/avg-cost
  afterward. If removing the fill would leave zero fills, the position itself is deleted too.
- `DELETE /api/positions/{id}` — cancel the whole position (all fills), same semantics as
  today's trade cancel.
- `GET /api/positions/{id}/marks` — unchanged in shape from today's trade marks, just FK-
  renamed.
- `GET /api/positions/summary` — open/closed counts, win rate, avg return — computed from the
  replay across all closed positions, ticker-level only (no strategy filter, per the grouping
  decision above).
- `GET /api/positions/analytics?ticker=&status=&from=&to=` — same role as today's
  `/api/trades/analytics`, drops the `strategy` filter param (no longer meaningful at the
  position level), keeps ticker/status/date-range.

## UI changes

- **Confirm form** (`index.html`'s TRADE button): if the clicked ticker already has an open
  position, the modal becomes "Add to position" (fewer fields — just fill_date/price/units,
  TP/stop pre-filled from the existing position, editable) instead of "Confirm Trade." If no
  open position exists, it's today's full confirm form, creating a new position.
- **Trades page** (`trades.html`): rows become one-per-position (not one-per-trade, though
  that's the same thing today) showing avg cost, units remaining, realized + unrealized P&L.
  "Close" becomes "Exit" and asks for units (defaulting to the full remaining amount, editable
  down for a partial). Daily P&L chart's `computePnlSeries` reimplemented against the full
  fills-replay above instead of the current single-entry/single-exit shortcut.
- **Position detail page** (`trade.html`, likely renamed `position.html`): adds a fills table
  (every entry/exit event with its date, price, units, strategy) above or alongside the
  existing daily-marks table, so the full history of how the position was built and trimmed is
  visible, not just entry/exit summary numbers.

## Migration

Existing `taken_trades` rows convert 1:1 into a `positions` row + exactly two `position_fills`
rows (one `entry` from `entry_price`/`entry_date`, and one `exit` from `exit_price`/`closed_at`
if the trade was already closed) — a straightforward one-time backfill script, not a live dual-
write period. `trade_daily_marks`/`notifications` rows get their FK column renamed and
repointed at the new `positions.id` (same value, since the migration preserves the original
`taken_trades.id` as the new `positions.id`).

## Explicitly out of scope

- FIFO/LIFO lot-matching (see the weighted-average-cost rationale above).
- Per-strategy P&L attribution for a position with fills from multiple strategies (see the
  grouping-decision consequence above) — position-level stats only.
- Reopening a fully-closed position (see the lifecycle section above) — always a new row.
- Cross-ticker portfolio-level margin/buying-power tracking — still out of scope, as it was in
  the original trade-tracking design.

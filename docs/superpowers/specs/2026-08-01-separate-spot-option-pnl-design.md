# Separate Spot and Option P&L Models — Design

Date: 2026-08-01
Status: Design, not implemented

## Background

`position_fills.price` is a single `REAL NOT NULL` column used for two different things
depending on `instrument`:

- **spot**: the actual traded price. P&L is straightforwardly `(exit_price - entry_price) /
  entry_price`.
- **option**: currently the *underlying stock's* price at the time of the fill — not the
  option's own price. The option's own economics live entirely in `premium`/`strike`/
  `opt_type`/`opt_side`/`expiry_date`/`iv_at_entry`, separate columns already option-only
  (`_FILL_OPTION_ONLY_FIELDS`).

`price` for option fills is currently required (`_FILL_SPOT_REQUIRED` is a strict subset of
`_FILL_OPTION_REQUIRED` — see `app.py:755-756`) but, on inspection, is **only ever consumed by
one piece of code**: the TP/stop alert-threshold math in `_update_trade_marks_and_alerts`
(`app.py:425-440`), which compares it against `position.tp_price`/`stop_price`. Nothing else
reads it — `replay_fills()`'s P&L math already correctly uses `premium` exclusively for options
(`app.py:334,343`, confirmed correct), and the daily option-value calc
(`_with_option_values`, `app.py:727-752`) already derives the option's value purely from
`close_price` (the stock's price that day, read from the daily mark) + `strike` + `iv_at_entry` +
`expiry_date` — never from any stored fill-level "entry stock price."

## The actual bug

`_update_trade_marks_and_alerts` does:

```python
avg_cost = state["avg_cost"]              # AVERAGE PREMIUM for an option position
pct_to_tp = (current_price - avg_cost) / (tp - avg_cost) * 100
```

`current_price` is the underlying stock's price (from `_computed`). `tp`/`stop_price` are
whatever the user typed into the Take Profit / Stop fields — currently the *same* two fields for
both spot and option trades (`TradeConfirmModal.tsx`'s `tpPrice`/`stopPrice` state, not
instrument-conditional). For a spot position this is self-consistent (`avg_cost` is a stock
price, `tp` is a stock price, `current_price` is a stock price — same units throughout). For an
**option** position, `avg_cost` is average premium (dollars per share of underlying, per
contract) while `tp`/`current_price` are stock prices — three different units in one formula.
The resulting `pct_to_tp`/`pct_to_stop` is not a meaningful percentage for options, so TP/stop
notifications can fire early, late, or effectively never for option trades. This is the concrete,
confirmed bug that motivated this design.

**Everything else — P&L display on the Trades page, position detail page, and the daily-mark
option-value calc — is already correct.** This design does not change those; it only removes a
now-unnecessary field and fixes the one place that was silently wrong.

## Decision: separate models per instrument, per user direction

> "spot is straight forward. pnl is based on entry and exit price. percentage growth between.
> option is based on premium paid and exit option price. i think we should make it that simple.
> entry units and premium on trade entry, and enter units and exit price on exit. percentage
> growth from premium to option exit price. simple and basic. for daily close value, we use
> current price, IV, premium, expiry, strike, risk free interest. we don't need entry price for
> options."

Concretely:

| | Spot | Option |
|---|---|---|
| **Entry fill needs** | entry price, units | premium, units (+ strike/type/side/expiry/IV, already required) |
| **Exit fill needs** | exit price, units | exit **option price**, units |
| **% growth (realized)** | `(exit_price - entry_price) / entry_price` | `(exit_option_price - premium) / premium` |
| **Daily mark / unrealized value** | `close_price` (stock) | Black-Scholes from `close_price` + `strike` + `iv_at_entry` + `expiry_date` + risk-free rate (**unchanged — already exactly this**) |
| **Stock "entry price" field** | Required (it IS the trade) | **Removed** — never needed anywhere in the option's own math |

This is `replay_fills()`'s existing behavior for the P&L side (already correct — no change
needed there), formalized as the explicit model and extended to *also* stop requiring/storing a
meaningless stock entry price on option fills, and to fix the one place that was actually reading
`price` incorrectly.

## What changes

### 1. `position_fills.price` becomes nullable, semantically spot-only

```sql
-- price REAL NOT NULL  →  price REAL  (nullable)
```

Migration: `ALTER TABLE position_fills ALTER COLUMN` isn't supported by SQLite directly:
standard SQLite pattern is create-new-table-copy-drop-rename, following the same style as the
existing migrations in `db.py` (`_migrate_universe_meta_date_to_epoch`,
`_migrate_taken_trades_to_positions_and_fills`) — copy the table with the column made nullable,
copy rows across, drop old, rename new. Existing option fills' stored `price` (stock price)
values are **not backfilled into anything** — they become vestigial/ignorable, not deleted
outright (no destructive `UPDATE ... SET price = NULL` pass needed; simply stop requiring or
reading it going forward). Confirm with user whether existing option fills' stale `price` values
should be explicitly nulled out as part of the migration, or just left as harmless unused data.

### 2. `_FILL_OPTION_REQUIRED` drops `price`, and exit fills need an option price instead

```python
# Before
_FILL_SPOT_REQUIRED = ["price", "units", "strategy_key", "signal_date", "fill_date"]
_FILL_OPTION_REQUIRED = _FILL_SPOT_REQUIRED + ["opt_side", "opt_type", "strike", "premium", "expiry_date"]

# After
_FILL_SPOT_REQUIRED = ["price", "units", "strategy_key", "signal_date", "fill_date"]
_FILL_OPTION_REQUIRED = ["units", "strategy_key", "signal_date", "fill_date",
                          "opt_side", "opt_type", "strike", "expiry_date"]
# premium required on ENTRY fills only; exit fills need premium too (see below) -- both entry
# and exit share the same column, "premium" is simply "the option's price at this fill," matching
# how "price" already double-duties as entry-or-exit stock price for spot fills today.
```

`premium` already exists as a column and is exactly "the option's price at the time of this
fill" — reusing it for exit fills (rather than adding a new `exit_option_price` column) matches
how the `price` column already works for spot (one column, meaning depends on `kind`). No new
column needed; `premium` becomes required on exit fills too, wherever it currently isn't
enforced (`_validate_fill_body`'s `required` list needs `premium` added for the `instrument ==
"option"` case regardless of `kind`, not just entry).

### 3. `replay_fills()` — no change needed

Already does exactly this (see Background) — `fill_value = f["premium"] if instrument ==
"option" else f["price"]` for both entry and exit. Confirming explicitly so implementation
doesn't second-guess or "fix" code that isn't broken.

### 4. TP/stop for option positions: still stock-price inputs, decayed option value derived at alert time

**Revised per user direction — this replaces the original plan of asking the user to type a TP/
stop directly in option-price terms.** TP/Stop stay exactly what they are today for *every*
position, spot or option: stock price levels, one familiar input the user already knows how to
set ("alert me when the stock hits $150"). What changes is what the alert engine does with them
for an option position — instead of comparing `avg_cost` (premium) against a stock price
directly (today's bug), it prices the option *as it would be worth if the stock were at that TP/
stop level right now* (today's date, so time-decay between entry and now is correctly factored
in — not the option's value at entry, its value **today** at that hypothetical stock price), then
compares that derived option value against `avg_cost`. Same units throughout, no new field, no
UI change to the TP/Stop inputs at all.

```python
# _update_trade_marks_and_alerts, app.py -- option position case
if instrument == "option":
    # Reuses the exact pricing call _with_option_values already makes for the daily mark's
    # option_value -- same T-to-expiry-from-today, same strike/IV, only the spot price plugged
    # in differs (tp/stop hypothetical instead of today's actual close).
    T = max((expiry_date - today).days, 0) / 365
    tp_option_value   = options_pricing.option_price(opt_type, tp,   strike, T, iv_at_entry)
    stop_option_value = options_pricing.option_price(opt_type, stop, strike, T, iv_at_entry)
    pct_to_tp   = (current_option_value - avg_cost) / (tp_option_value   - avg_cost) * 100
    pct_to_stop = (avg_cost - current_option_value) / (avg_cost - stop_option_value) * 100
else:
    # spot -- unchanged, current_price/avg_cost/tp/stop all already stock prices
    pct_to_tp   = (current_price - avg_cost) / (tp - avg_cost) * 100
    pct_to_stop = (avg_cost - current_price) / (avg_cost - stop) * 100
```

`current_option_value` is the same value `_with_option_values` already computes for today's
daily mark — reuse that computation rather than pricing it twice. `pct_to_tp`/`pct_to_stop`'s
formula shape (`app.py:436-437`) is unchanged; only what's fed into them changes.

One real subtlety worth flagging: **`T` for the TP/stop hypothetical must be time-to-expiry as
of *now* (today's date), not as of entry** — decay between entry and today has already happened
regardless of whether the stock ever reaches TP/stop, so pricing "if the stock hit TP today"
correctly reflects less time value remaining than it would have on day one. This is exactly what
"with decay factored" means and is the reason this can't be computed once at trade-entry time and
cached — it's recomputed fresh every alert-engine pass, same cadence `_with_option_values`
already recomputes daily marks.

A multi-lot position (scaled-in at different strikes/IVs) needs the same blending
`_with_option_values` already does (`app.py:743-750`, weighted by each lot's remaining units) —
not a single strike/IV, if more than one entry fill is still open.

### 5. Frontend form changes

`TradeConfirmModal.tsx`:
- Remove the shared Entry Price field for the option instrument path (keep it for spot,
  unchanged).
- Add an exit **option price** (premium at exit) field to whatever exit-fill UI exists
  (`PositionsTable.tsx`'s `submitExit`/`AddFillForm` — grep during implementation for every
  place a fill's `price` is collected on exit, since exits can happen both via the Trades page
  row-level exit form and the position detail page's fill form). This is the only new input
  option trades need — TP/Stop stay as-is (see §4), still stock-price fields, no label or
  semantics change needed there.

### 6. Display changes

Nothing changes in `PositionDetailPage.tsx` / `PositionsTable.tsx` — both already correctly
branch on `hasOptionValues` and use `option_value` vs `close_price` for the unrealized-P&L
display (confirmed correct in Background). The TP/Stop `StatBox`es
(`PositionDetailPage.tsx:88-89`) also need **no change** — per the revised §4, TP/Stop remain
stock-price values end to end, for both instruments, so their existing display is already
correct.

## Risk-free rate: tiered by time-to-expiry, not a single constant

Currently a single hardcoded constant in both places option pricing happens:

```python
# backend/options_pricing.py
RISK_FREE_RATE = 0.045  # fixed assumption, matches static/index.html's RISK_FREE_RATE
```
```ts
// frontend/src/lib/blackScholes.ts
export const RISK_FREE_RATE = 0.045;
```

Per user direction, this becomes a lookup keyed on time-to-expiry, matching the actual Treasury
bill/note tenor closest to the option's own expiry — using each instrument's own yield instead of
one flat number is more accurate Black-Scholes input, and the tiers below are the standard
market-convention mapping (T-bill/T-note maturity ↔ option expiry bucket):

| Expires in (~days) | Treasury instrument | Rate |
|---|---|---|
| 1 month (~30d) | 4-Week T-Bill | 3.78% |
| 2 months (~60d) | 8-Week T-Bill | 3.85% |
| 3 months (~90d) | 13-Week T-Bill | 3.77% |
| 6 months (~180d) | 26-Week T-Bill | 3.98% |
| 1 year (~365d) | 52-Week T-Note | 4.06% |
| 2 years (LEAPS) | 2-Year T-Note | 4.30% |

### Implementation shape

Both `option_price()` (backend) and `blackScholes()` (frontend) already receive `T` (years to
expiry) from every call site (`app.py:749`'s `_with_option_values`, `plCalc.ts:76,81`) — the
lookup slots in as a pure function of `T`, no new inputs need to be threaded through:

```python
# backend/options_pricing.py
RISK_FREE_RATE_TIERS = [
    # (days_to_expiry_upper_bound, rate) -- ordered ascending, first match wins
    (30, 0.0378),
    (60, 0.0385),
    (90, 0.0377),
    (180, 0.0398),
    (365, 0.0406),
    (730, 0.0430),   # 2-year LEAPS ceiling
]

def risk_free_rate_for(days_to_expiry: float) -> float:
    for upper_bound, rate in RISK_FREE_RATE_TIERS:
        if days_to_expiry <= upper_bound:
            return rate
    return RISK_FREE_RATE_TIERS[-1][1]  # beyond 2 years -- use the longest tier available
                                          # rather than extrapolating or erroring
```

`option_price(..., T, iv)` converts `T` (years) back to days (`T * 365`) to look up the tier,
or the call sites could pass days-to-expiry directly instead of years — worth deciding during
implementation which is the more natural unit to thread through, since `T` in years is what
Black-Scholes itself needs internally regardless.

Frontend `blackScholes.ts` gets the identical table and lookup function, kept numerically
identical to the backend per the existing "kept numerically identical so a trade's ... " comment
convention already in `options_pricing.py` — this is the same duplication pattern the codebase
already has for the Black-Scholes math itself (Abramowitz-Stegun normal-CDF approximation
ported verbatim in both places), not a new kind of drift risk.

### Where this changes behavior

- `_with_option_values` (daily mark → option value) — now uses the tiered rate based on each
  lot's own days-remaining-to-expiry on that mark date (already has `expiry` and `mark_date`
  available, `T` is already computed there).
- `plCalc.ts`'s P/L Calculator (options mode, `blackScholes.ts` call sites) — same tiering
  applied client-side for the standalone calculator's option pricing.
- The **default rate constant itself is not deleted** — kept as the fallback for `T` values
  outside all tiers (see `risk_free_rate_for`'s final `return`) and as the single source of truth
  the tier table is expressed relative to, matching how `RISK_FREE_RATE` is already referenced
  by name elsewhere.

### Open question folded into this section

Rates themselves (3.78%, 3.85%, etc.) are point-in-time market data, not something derived from
any live feed in this codebase (no Treasury-rate API integration exists or is proposed here) —
confirm these are meant as fixed constants to hardcode now (same treatment as today's single
`0.045`), not something expected to update automatically later. If they should track live
Treasury yields eventually, that's a materially different, larger design (a new data source, a
refresh cadence) and out of scope for this pass unless flagged otherwise.

## Explicitly not changing

- `replay_fills()`'s core P&L math — already correct for both instruments, per Background.
- The daily option-value calc's overall *shape* (`_with_option_values`) — still current price +
  IV + strike + expiry + a risk-free rate; only the rate itself becomes tiered instead of a flat
  constant, per the section above.
- Spot fills/positions — entirely unaffected; `price` stays required, meaning stays "the traded
  stock price," no schema or logic change.

## Open questions — all resolved

1. ~~Existing option fills' stored `price` values~~ — **moot**: no trades exist in production
   yet, so there's no existing data to preserve, backfill, or null out. §1's migration
   simplifies to a plain `CREATE TABLE`/schema change, not a data-preserving copy-and-rename —
   safe to just drop and recreate `position_fills` with the new nullable `price` column and
   updated required-field set, no rows to carry across.
2. ~~Migration destructiveness~~ — **moot for the same reason**: with zero existing trades,
   "destructive" doesn't apply; ship as a normal schema change on the next deploy, no careful
   rollout needed.
3. ~~Risk-free rate tiers as fixed constants vs. live data~~ — **confirmed: hardcoded**. The 6
   tiered rates (3.78%–4.30%) are fixed constants, same treatment as today's single `0.045`, not
   wired to any live Treasury-yield feed.

Also resolved earlier: TP/stop alert thresholds for options — settled by §4's revision.
`TP_STOP_ALERT_THRESHOLDS` and the pct-distance formula are reused unchanged; only the value fed
in changes (today's decayed option value at the TP/stop stock-price level, vs. avg_cost), so the
"is this too noisy for option volatility" concern doesn't apply the way it would have under the
original option-price-input plan — the thresholds are still measuring progress toward a
stock-price target, same cadence as spot, just translated through the option's pricing model.

**No open questions remain. This design is ready to implement pending final go-ahead.**

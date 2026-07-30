# Estimate Entry — Design

Date: 2026-07-29
Status: Design, not implemented

## Background

Original spec (user-provided) proposed a limit-order price estimator: user clicks "Estimate"
on a ticker's strategy card, sees a recommended limit price below current market, derived from
the strategy's historical max-adverse-excursion (MAE) on winning trades and a chart support
level. It also proposed an options variant (strike/expiry/IV → premium target) using
Black-Scholes.

Several of the spec's claims about "already available" backend pieces didn't match the
codebase and needed correcting before design:

- `_summarize()` doesn't exist. The real function is `strategy_common.summarize()`
  (`webapp/strategy_common.py:98`), which computes `avg_mae_wins_pct` (line 118).
- `current_price` isn't a payload field. `_compute_one()` (`webapp/app.py`) builds
  `payload["price"]`, ticker-wide, not per-strategy.
- `open_position.entry_price` is real (`strategy_common.build_open_position()`,
  `webapp/strategy_common.py:140-155`) but **only present when a strategy currently has a
  live open position** — most tickers are watching for a setup, not in one.
- The spec claimed Black-Scholes needed to be written in Python with scipy. It already
  exists, fully working, in the frontend: `blackScholes(type, S, K, T, iv, r)`
  (`webapp/static/index.html:463`), used by the existing P/L Calculator
  (`plModelAt()`, `:640`). scipy is not a dependency anywhere in this project (checked
  `requirements.txt`) and does not need to become one — the options math stays client-side.
- The spec's options TP target hardcoded `entry_price * 1.11` ("VCPO TP Half"). This is
  wrong for VEXH (whose target is a live Bollinger midline, not a fixed percent) and
  redundant for VCP/VCPO (`strategy_vcpo.py:130` already computes
  `entry_price * (1 + tp_target_pct / 100)` into `open_position.target`). The design uses
  `open_position.target` directly instead of re-deriving it.

## Scope

- **Spot price estimate** (stock/ETF limit price) and **options premium estimate** (target
  premium at a hypothetical dip), both in this pass — the options path reuses the spot
  path's `recommended_limit` as its "dip price" input, so splitting them apart would mean
  building the dependency twice.
- Estimate Entry is **only available when the strategy has an open position**
  (`open_position` non-null on that strategy's payload). No open position means no real
  simulated entry to anchor the MAE floor to — showing a number computed off
  `current_price` instead would silently change what the number means without saying so.
  The button is simply absent/disabled in that case, not degraded to a different formula.
- **Support level is a manual input field**, not computed. The Adaptive SR indicator lives
  in Pine/TradingView, not in this Python codebase; approximating it (swing-lows, pivot
  clustering) adds noise the user explicitly doesn't want for a number they're about to
  place real orders against. A future phase can add an approximation as a *pre-filled
  suggestion* the user overrides, but that's out of scope here.

## Formula

Spot:

```
mae_floor          = open_position.entry_price × (1 − avg_mae_wins_pct / 100)
nearest_support     = max(support levels < current_price), or mae_floor if none given
recommended_limit   = max(mae_floor, nearest_support)
recommended_limit   = min(recommended_limit, current_price × 0.99)   # never at/above market
pct_below_current   = (recommended_limit − current_price) / current_price × 100
```

Options (all inputs — strike, expiry, IV — typed fresh into the modal each time; not
prefilled from the P/L Calculator, which is a separate, decoupled piece of UI state):

```
T_now      = days_to_expiry / 365
option_now = blackScholes(type, current_price, strike, T_now, iv, RISK_FREE_RATE).price

T_dip      = max(days_to_expiry − days_elapsed_assumption, 0) / 365     # days_elapsed_assumption = 3, fixed
option_dip = blackScholes(type, recommended_limit, strike, T_dip, iv, RISK_FREE_RATE).price

tp_price   = open_position.target                                       # strategy's own TP, not a hardcoded %
T_tp       = max(days_to_expiry − days_to_target_assumption, 0) / 365   # days_to_target_assumption = 15, fixed
option_tp  = blackScholes(type, tp_price, strike, T_tp, iv, RISK_FREE_RATE).price

expected_return_pct = (option_tp − option_dip) / option_dip × 100
```

`type` is `"call"` for a long-side setup (VEXH/VCP/VCPO are all long-only, so this is
always `"call"` in practice, but the field is passed through rather than hardcoded in case
that ever changes). `RISK_FREE_RATE` is the existing `0.045` constant
(`index.html:459`), reused rather than duplicated.

Known limitation, carried over from the original spec and worth stating in the UI, not
hiding: IV at the dip may differ from IV now (selloffs often spike IV), so `option_dip` is
a same-IV approximation, not a forecast. This is a planning aid, not a fill guarantee.

## Backend

New file `webapp/entry_estimate.py`:

```python
def estimate_entry(current_price: float, entry_price: float, avg_mae_wins_pct: float,
                    support_levels: list[float]) -> dict:
    """Returns mae_floor, support_used, recommended_limit, pct_below_current."""
```

Pure function, no I/O — takes exactly the values the caller (an app.py endpoint) already
has from the in-memory `_computed` payload plus the support levels the user typed into the
modal. No new DB table, no new fetch. Unit-testable directly with plain floats/lists.

Options premium math (`blackScholes`) stays in JS — there is no backend
`estimate_option_entry()`. The backend only ever computes the spot-side numbers; the
frontend takes `recommended_limit` and `open_position.target` from that response and runs
the existing `blackScholes()` three times (now, dip, tp) locally. This avoids introducing
a second Black-Scholes implementation in Python that could drift from the JS one, and
avoids a scipy dependency entirely.

New endpoint in `webapp/app.py`:

```
POST /api/estimate_entry
  body: { ticker, strategy, support_levels: [float, ...] }
  reads current_price/entry_price/avg_mae_wins_pct from the in-memory _computed payload
  for that ticker+strategy (404 if the ticker isn't computed, or open_position is null)
  returns entry_estimate.estimate_entry()'s dict
```

POST (not GET) because `support_levels` is a user-entered list, not naturally a query
string. No new locking/threading concerns — this reads already-computed in-memory state
(`_computed`, guarded by the existing `_compute_lock`), same pattern as `/api/tickers`.

## Frontend

- **Entry point:** a new "Estimate" button inside `strategyModal()`
  (`webapp/static/index.html:1022`), next to the existing Entry/Target/Unrealized/MAE rows
  — only rendered when `s.open_position` is non-null (matches every other open-position-only
  row already in that modal, e.g. `:1030`).
- **Modal:** new small modal, same pattern as the existing P/L Calculator modal
  (`#plCalcBtn`/`:797`) — a separate DOM element, opened on click, closed independently.
  Shows:
  - Current price, sim entry, avg MAE (wins) — read directly off the payload, no fetch
    needed for these three.
  - A manual "nearest support" number input (plain `<input type=number>`, no dropdown —
    the spec's dropdown mockup implied a list source that doesn't exist).
  - "Calculate" button — POSTs to `/api/estimate_entry`, renders `recommended_limit` and
    `pct_below_current` once the response lands.
  - Below that, an **optional** "show as option" toggle that reveals strike/expiry/IV
    fields; filling them runs the three local `blackScholes()` calls described above and
    shows `option_dip` (the recommended limit premium) and `expected_return_pct`. This
    stays collapsed by default — most estimates will be spot-only.
  - "Copy price" button — copies whichever number is currently primary (spot
    `recommended_limit`, or `option_dip` if the options toggle is open) to the clipboard.
- No changes to `strategyChips()` or the main card grid — this is entirely inside the modal
  that's already the detail view for a strategy.

## Out of scope (explicitly deferred, not forgotten)

- Programmatic support-level detection (swing-low/pivot approximation or a real Adaptive SR
  port) — noted as a future "pre-filled suggestion" enhancement, not built now.
- Prefilling options fields from the P/L Calculator's state.
- Persisting estimates (this is a read/compute-on-click tool, nothing is saved to the DB).

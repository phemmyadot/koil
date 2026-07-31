# Strategy Architecture

Three strategies (VEXH, VCP, VCPO) compute differently, but must produce the exact same output
shape. This file is the single source of truth for that split: what's shared, what's
strategy-specific, and why.

## The rule

**Only the trading logic itself is allowed to differ between strategies.** Everything else —
input preparation, trade bookkeeping, stats math, verdict classification, output shape — is one
shared implementation, used by all three.

If two strategy files ever contain the same logic written twice, that's a bug in this
architecture, not an acceptable difference.

## Shared: `webapp/strategy_common.py`

- `with_earnings_flags(bars, ticker)` / `cached_earnings_dates(ticker)` — attaches
  `EarningsWithinAvoidWindow`/`EarningsImminent` columns to a copy of the raw bars. Called for
  every strategy, unconditionally — even VCP/VCPO, whose `run()` doesn't read those columns,
  get the same input shape as VEXH. No strategy special-cases "do I need earnings data."
- `wilder_atr(h, l, c, length)` — Wilder's ATR, used by every strategy's `compute_indicators()`.
- `record_trade(trades, df, position, entry_bar, exit_bar, exit_price, commission_rate)` —
  the one function every `run()` calls to close a trade. Always appends the same fields:
  `entry_bar`, `entry_date`, `entry_price`, `exit_bar`, `exit_date`, `exit_price`, `return_pct`,
  `dollar_pnl`, `days`, `mae_pct`. Strategies with real position sizing (VCP/VCPO) pass their
  actual `qty` so `dollar_pnl` is real; VEXH (no sizing model) passes `qty=1` so `dollar_pnl`
  equals `return_pct` in dollar terms — the field always exists, downstream code never branches
  on whether it's there.
- `summarize(trades)` — one function, not per-strategy. Since every trade now has both
  `return_pct` and `dollar_pnl` (guaranteed by `record_trade`), PF/WR/avg-days/last-5/MAE/
  outlier-fraction are always computed the same way, off `dollar_pnl`.
- `build_open_position(position, df, target, last_close)` — turns a live `position` dict into
  the `open_position` shape (`entry_date/entry_price/target/to_tp_pct/days_held/
  unrealized_pct/mae_pct`). `target` is the one genuinely different input (VEXH: live
  Bollinger midline; VCP/VCPO: fixed `entry_price * (1 + tp_target_pct)`) — every strategy's
  `run()` computes `target` itself and hands it in.
- `verdict(signal_today, in_position, n_trades, win_rate, pf, tp_hit=False)` — TAKE/SKIP/
  NO SIGNAL/IN TRADE/TP HIT classification. Identical for all three (VEXH never sets
  `tp_hit`, since it has no partial-TP mechanic).
- `evaluate_strategy(ticker, bars, run_fn, compute_indicators_fn, ind=None)` — the shared
  `evaluate()` scaffold every strategy's own `evaluate()` calls:
  1. `with_earnings_flags(bars, ticker)` → `df`
  2. `ind = ind or compute_indicators_fn(df)`
  3. `trades, signal_today, in_position, tp_hit, open_position = run_fn(df, ind)`
  4. `stats = summarize(trades)`
  5. `verdict(...)`
  6. assemble and return the shared stats dict

## Strategy-specific: `strategy_vexh.py` / `strategy_vcp.py` / `strategy_vcpo.py`

Each file contains only:
- Its own constants (`ATR_MULT`, `TRAIL_TIER_PCT`, entry gate thresholds, etc.).
- `compute_indicators(df)` — whatever indicator set that strategy's `run()` needs.
- `run(df, ind)` — the real trading logic: entry gate, stop/trail/TP rules, time stop. This is
  the only place strategies are allowed to differ, because this is what actually makes them
  different strategies. Calls `strategy_common.record_trade()` to close trades and
  `strategy_common.build_open_position()` to describe a live position — never builds those
  shapes by hand.
- `evaluate(ticker, bars, ind=None)` — a thin wrapper: `return strategy_common.evaluate_strategy(ticker, bars, run, compute_indicators, ind)`.

`vexh_engine.py` no longer exists as a separate file — its bar-loop becomes `strategy_vexh.py`'s
`run()`, in the same shape as VCP/VCPO's `run()` (same return tuple, same use of
`record_trade()`).

## `webapp/app.py`'s integration

All three strategies are called the same way, with no special-casing for VEXH:

```python
_STRATEGY_MODULES = {"vexh": strategy_vexh, "strategy_vcp": strategy_vcp, "strategy_vcpo": strategy_vcpo}

shared_ind = strategy_vcp.compute_indicators(bars)  # only VCP/VCPO share an indicator set; VEXH computes its own
payload = {}
for key, module in _STRATEGY_MODULES.items():
    payload[key] = _eval_strategy(module, ticker, bars, shared_ind if key != "vexh" else None)
```

Every strategy's result lands at `payload[key]` in the exact same shape — no `payload["vexh"]`
vs `payload[key]["baseline"]` asymmetry, no strategy read specially.

## What's NOT shared, on purpose

- Each strategy's `run()` loop body — the actual entry/stop/trail/TP/time-stop rules. This is
  real strategy logic, not duplication.
- Each strategy's own constants and `compute_indicators()` — different strategies need
  different indicators for their own `run()`.

# Pine Trend Strategy

Trend-following and mean-reversion trading strategies for optionable stocks, developed in
both Pine Script (for TradingView backtesting) and Python (for research, validation, and a
live scoring dashboard).

## Layout

### Pine Script strategies (TradingView)
`pines/` holds the 4 canonical Pine ports of the dashboard's 4 strategies (Exhaustion, VCP
Master, Strategy A, Strategy D) — each is a trade-for-trade mirror of its `webapp/` Python
module:
- `pines/strategy_d_volatility_exhaustion.pine` — the shipped/production strategy: buys
  oversold pullbacks (RSI + lower Bollinger Band) within an established SMA150 uptrend, with
  a time stop and ATR volatility ceiling/floor. This is what `p.py` and the dashboard's
  Exhaustion chip mirror. (Filename predates the webapp's A/D/VCP expansion — despite the
  `_d_`, this is Exhaustion, not Strategy D.)
- `pines/vcp.pine` — "VCP Master", ATR-compression + volume-confirmed breakout with a
  multi-tier stop/breakeven/trail and partial take-profit. Mirrors `webapp/strategy_vcp.py`.
- `pines/strategy_a_adaptive_pullback.pine` — "Strategy A", adaptive KAMA-based pullback
  entry with a fixed ATR trail. Mirrors `webapp/strategy_a.py`.
- `pines/strategy_d_vcp_fixed_bracket.pine` — "Strategy D", ADX regime + Bollinger-Band-Width
  compression/volume-dry-up setup + volume-confirmed resistance breakout, fixed-bracket ATR
  stop with pattern-low floor and chandelier trail. Mirrors `webapp/strategy_d.py`.

Root-level Pine files are earlier exploratory variants, not wired to the dashboard:
- `strategy.pine` — original v1 pullback-in-trend strategy (EMA20/50/200, RSI, ATR stops).
- `strategy_b_adx_scaled_trail.pine` — same entry logic as Strategy A, trail multiplier
  scales with ADX trend strength.
- `strategy_c_vcp_breakout.pine` — an earlier VCP breakout variant using swing-pivot
  contraction tracking (different detection logic than `pines/vcp.pine`).
- `breakout_projection.pine` — a separate pre-breakout scoring/target-projection indicator
  (squeeze + volume dry-up + resistance clustering), not a strategy.

### Python
- `p.py` — the production backtest harness (`PortfolioSizedEngine`, using the
  [`backtesting`](https://kernc.github.io/backtesting.py/) library) that exactly mirrors
  `pines/strategy_d_volatility_exhaustion.pine`'s entry/exit logic. Single source of truth
  for strategy semantics — the webapp imports directly from this file.
- `experiment.py` — grid-search / parameter-sweep harness for testing strategy variants.
- `scratch_keltner_basket.py` — exploratory scratch work.
- `trade_features.py` — extracts entry-time feature snapshots (RSI, ATR%, distance from
  SMA, etc.) for every closed trade across the screened universe, used to validate which
  entry conditions actually predict winners (see `trades_features.csv`).

### Web dashboard (`webapp/`)
A live scoring dashboard for the production strategy (`strategy_d`/`p.py`), run locally:

```
.venv/Scripts/python.exe -m uvicorn webapp.app:app --port 8123
```

Then open `http://127.0.0.1:8123`.

- `app.py` — FastAPI backend; scores every ticker in `tickers.py` against the strategy's
  6 entry gates, with a 15-minute in-memory cache.
- `scoring.py` — per-ticker evaluation logic: entry gate pass/fail, confidence tier
  (LOW/MEDIUM/HIGH, based on validated distance-from-SMA research), open-trade status with
  a TAKE/SKIP verdict, average trade duration, and last-5-trades history.
- `tickers.py` — the screened ticker universe (generated file, see below).
- `build_universe.py` — rebuilds `tickers.py` from scratch by screening Yahoo Finance for
  cap/volume/price/exchange criteria, then filtering for SMA200/SMA50/weekly-volatility
  technicals. Supports `--min-cap`, `--min-vol`, `--merge`, `--allow-otc` flags:
  ```
  .venv/Scripts/python.exe -m webapp.build_universe --min-cap 100000000 --min-vol 300000
  ```
- `static/index.html` — single-page frontend (no build step, no framework).

## Key validated findings

- **The edge lives in stock selection, not parameter tuning.** A full parameter grid search
  on an unsuitable ticker universe lost money under every permutation; the same fixed
  parameters on a properly screened universe were profitable out-of-sample.
- **Distance from SMA150 (in ATRs) at entry is a real, monotonic confidence signal** — win
  rate rises from ~41% (bottom quintile) to ~73% (top quintile), validated on a chronological
  train/test split. This backs the dashboard's confidence tiers.
- **RSI-at-entry and ATR%-at-entry are not quality signals** — RSI shows only a weak effect,
  and ATR% is a variance dial (bigger winners *and* bigger losers), not an edge.

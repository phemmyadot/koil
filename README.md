# Pine Trend Strategy

Trend-following and mean-reversion trading strategies for optionable stocks, developed in
both Pine Script (for TradingView backtesting) and Python (for research, validation, and a
live scoring dashboard).

## Layout

### Pine Script strategies (TradingView)
`pines/` holds the canonical Pine ports of the dashboard's 3 live strategies (VEXH, VCP,
VCPO) — each is a trade-for-trade mirror of its `webapp/` Python module:
- `pines/strategy_d_volatility_exhaustion.pine` — the shipped/production strategy: buys
  oversold pullbacks (RSI + lower Bollinger Band) within an established SMA150 uptrend, with
  a time stop and ATR volatility ceiling/floor. This is what `p.py` and the dashboard's VEXH
  strategy mirror. (Filename predates the webapp's later expansion — despite the `_d_`, this
  is VEXH, not "Strategy D".)
- `pines/vcp.pine` — "VCP Master", ATR-compression + volume-confirmed breakout with a
  multi-tier stop/breakeven/trail and partial take-profit. Mirrors `webapp/strategy_vcp.py`.
- `pines/vcpo.pine` — "VCPO", same ATR-compression breakout as VCP Master but without the
  volume-confirmation gate. Mirrors `webapp/strategy_vcpo.py`.

Root-level Pine files are earlier exploratory variants, not wired to the dashboard:
- `strategy.pine` — original v1 pullback-in-trend strategy (EMA20/50/200, RSI, ATR stops).
- `strategy_a_adaptive_pullback.pine` / `strategy_b_adx_scaled_trail.pine` — adaptive
  KAMA-based pullback variants, deprecated (no `webapp/` module mirrors them anymore).
- `strategy_c_vcp_breakout.pine` / `strategy_d_vcp_fixed_bracket.pine` — earlier VCP breakout
  variants, deprecated (no `webapp/` module mirrors them anymore).
- `breakout_projection.pine` — a separate pre-breakout scoring/target-projection indicator
  (squeeze + volume dry-up + resistance clustering), not a strategy. Mirrors
  `webapp/prebreak.py`.

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
A live scoring dashboard for all 3 strategies (VEXH/VCP/VCPO), run locally:

```
.venv/Scripts/python.exe -m uvicorn webapp.app:app --port 8123
```

Then open `http://127.0.0.1:8123`.

- `app.py` — FastAPI backend; computes each ticker's per-strategy stats in the background on
  a fixed cadence (see `webapp/refresh_architecture.md`), never on the request path.
- `strategy_vexh.py` / `strategy_vcp.py` / `strategy_vcpo.py` — each strategy's `evaluate()`,
  returning the same shape: trade stats, open-position status, and a TAKE/SKIP/NO SIGNAL/
  IN TRADE verdict. `strategy_common.py` holds logic shared across all three (verdict
  classification, Wilder's ATR).
- `score.py` — the 0-10 setup-quality score (see `webapp/scoring.md`), independent of any
  single strategy's own PF/WR verdict.
- `build_universe.py` — `fetch_candidates()` screens Yahoo Finance for cap/volume/price/
  exchange criteria (symbols only); `passes_technical_filters()` checks a candidate's stored
  bars against each strategy's actual entry condition. Both are called by `app.py`'s refresh
  cycle -- there's no separate CLI step and no generated `tickers.py` file; the screened
  universe lives in the DB (`webapp/db.py`'s `candidate_tickers` table) and is rebuilt fresh
  on every fetch/compute cycle.
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

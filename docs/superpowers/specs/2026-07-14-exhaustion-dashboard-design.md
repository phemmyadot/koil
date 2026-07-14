# Exhaustion Strategy Web Dashboard — Design

Date: 2026-07-14
Status: Approved (user), ready for implementation

## Purpose

A local web UI that shows, for a hard-coded ticker list, how close each name is to an
entry signal of the Hardened Exhaustion Engine (strategy_d_volatility_exhaustion.pine /
p.py), and whether the strategy currently holds an open position in it. Answers
"what should I watch today?" at a glance.

## Architecture

```
webapp/
  app.py           FastAPI backend + static file serving
  scoring.py       Indicator + score computation (imports nothing from p.py; same math)
  static/index.html  Single self-contained page (vanilla JS, no build step)
```

- Python backend runs with the project venv: `.\.venv\Scripts\python.exe -m uvicorn webapp.app:app`
- `TICKERS` list hard-coded at the top of `app.py` (currently the 20-name universe).
- No database, no auth, no order placement, no universe editing from the UI.

## Backend

### GET /api/tickers

Returns JSON:

```json
{
  "asof": "2026-07-14T15:05:00Z",
  "cached": true,
  "tickers": [
    {
      "ticker": "ALM",
      "price": 18.34,
      "date": "2026-07-14",
      "score": 4,
      "conditions": {
        "trend":    {"pass": true,  "value": "close 18.34 vs SMA150 14.02"},
        "band":     {"pass": false, "value": "close 2.1% above lower band"},
        "rsi":      {"pass": false, "value": 47.2},
        "sma_dist": {"pass": true,  "value": "3.0 ATR above SMA"},
        "vol_ceil": {"pass": true,  "value": "ATR 9.9% <= 12%"},
        "vol_floor":{"pass": true,  "value": "ATR 9.9% >= 4%"}
      },
      "to_tp_pct": 6.3,
      "open_trade": {
        "entry_date": "2026-07-02", "entry_price": 17.10,
        "target": 18.90, "bars_held": 8, "unrealized_pct": 7.3
      }
    }
  ],
  "errors": {"SLDE": "no data"}
}
```

- `score` = count of the six entry gates passing on the latest daily close
  (trend, band exhaustion, RSI<=40, SMA-distance, ATR ceiling, ATR floor).
  6/6 means the strategy would enter on this close.
- `to_tp_pct` = distance from current close to the 20-day basis (the payoff metric that
  separated top trades in the 2026-07 trade analysis).
- `open_trade`: run the backtesting.py engine (identical config to p.py) twice, with
  `finalize_trades` False and True; if True yields one extra trade, that trade is the
  open position (gives entry date/price); target = basis frozen at its entry bar;
  unrealized = last close vs entry. Null when flat.
- Indicator math identical to p.py: SMA-RSI(14) with 1e-9 epsilon, Wilder ATR via
  `ewm(alpha=1/14, adjust=False)`, population stdev (`ddof=0`), yfinance
  `auto_adjust=False`.
- Config constants mirror production defaults: SMA 150, BB 20/1.5, RSI 40, dist 0.5,
  ATR 4-12%, time stop 20, entries from 2022-01-01.

### Caching

In-memory dict `{ticker: (fetched_at, payload)}`, TTL 15 minutes. `GET /api/tickers?refresh=1`
bypasses and repopulates. Per-ticker failures land in `errors` and never fail the request.

## Frontend

One page, one table. No framework, no build step; CSS respects
`prefers-color-scheme` for dark/light.

- Columns: Ticker | Price | Score (0-6 bar) | six condition chips (pass/fail with value
  tooltip) | toTP% | Open trade badge (entry date, unrealized ±% colored) 
- Sort: open trades pinned first, then score descending, then toTP% descending.
- Header shows as-of time, cached/live indicator, and a Refresh button (calls ?refresh=1).
- Error tickers render as greyed rows with the error message.

## Testing

1. Smoke: `GET /api/tickers` returns 200 with all 20 tickers or per-ticker errors.
2. Cross-check one ticker's score by hand against indicator values computed in a REPL.
3. Open-trade detection: verify against the p.py backtest (any position open through the
   last bar must appear; flat tickers must show null).
4. Launch server, load page, confirm table renders and refresh works.

## Out of scope (YAGNI)

Auth, persistence, intraday data, charts, alerting, editing tickers in the UI,
multi-strategy support.

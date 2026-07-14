"""Per-ticker scoring for the exhaustion dashboard.

Uses the exact production logic from p.py (single source of truth) so the
score/open-trade status always matches the validated backtest.
"""
import os
import sys
import warnings

import pandas as pd
import yfinance as yf
from backtesting import Backtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p import PortfolioSizedEngine, RSI, ATR  # noqa: E402

warnings.filterwarnings("ignore", message="Some trades remain open")

E = PortfolioSizedEngine  # production config constants live on the engine class


def fetch(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start="2020-01-01", interval="1d",
                     progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.drop(columns=["Adj Close"], errors="ignore")


def _open_trade(df: pd.DataFrame) -> dict | None:
    """Run the engine with and without finalize_trades; the extra row is the open position."""
    runs = {}
    for fin in (False, True):
        bt = Backtest(df, PortfolioSizedEngine, cash=10_000, commission=0.001,
                      trade_on_close=True, finalize_trades=fin)
        runs[fin] = bt.run()["_trades"]
    closed, forced = runs[False], runs[True]
    if len(forced) <= len(closed):
        return None
    tr = forced.iloc[-1]
    basis = df.Close.rolling(E.bb_len).mean()
    target = basis.loc[tr.EntryTime]
    last_close = float(df.Close.iloc[-1])
    bars_held = int(df.index.get_loc(df.index[-1]) - df.index.get_loc(tr.EntryTime))
    return {
        "entry_date": str(tr.EntryTime.date()),
        "entry_price": round(float(tr.EntryPrice), 4),
        "target": round(float(target), 4),
        "bars_held": bars_held,
        "unrealized_pct": round(100 * (last_close / float(tr.EntryPrice) - 1), 2),
    }


def evaluate(ticker: str) -> dict:
    df = fetch(ticker)
    if df.empty or len(df) < E.sma_slow_len + 20:
        raise ValueError("no data" if df.empty else "insufficient history")

    c = df.Close
    price = float(c.iloc[-1])
    sma150 = float(c.rolling(E.sma_slow_len).mean().iloc[-1])
    basis = float(c.rolling(E.bb_len).mean().iloc[-1])
    std = float(c.rolling(E.bb_len).std(ddof=0).iloc[-1])
    lower = basis - E.bb_mult * std
    rsi = RSI(c, E.rsi_len)
    rsi_val = float(rsi.iloc[-1])
    atr = ATR(df.High, df.Low, df.Close, 14)
    atr_val = float(atr.iloc[-1])
    atr_pct = 100 * atr_val / price

    conditions = {
        "trend": {
            "pass": price > sma150,
            "value": f"close {price:.2f} vs SMA150 {sma150:.2f}",
        },
        "band": {
            "pass": price < lower,
            "value": f"close {100 * (price / lower - 1):+.1f}% vs lower band {lower:.2f}",
        },
        "rsi": {
            "pass": rsi_val <= E.rsi_lower,
            "value": round(rsi_val, 1),
        },
        "sma_dist": {
            "pass": price >= sma150 + E.min_sma_dist_atr * atr_val,
            "value": f"{(price - sma150) / atr_val:.1f} ATR above SMA" if atr_val > 0 else "n/a",
        },
        "vol_ceil": {
            "pass": atr_pct <= 100 * E.max_atr_pct,
            "value": f"ATR {atr_pct:.1f}% <= {100 * E.max_atr_pct:.0f}%",
        },
        "vol_floor": {
            "pass": atr_pct >= 100 * E.min_atr_pct,
            "value": f"ATR {atr_pct:.1f}% >= {100 * E.min_atr_pct:.0f}%",
        },
    }

    return {
        "ticker": ticker,
        "price": round(price, 4),
        "date": str(df.index[-1].date()),
        "score": sum(1 for v in conditions.values() if v["pass"]),
        "conditions": conditions,
        "to_tp_pct": round(100 * (basis / price - 1), 2),
        "open_trade": _open_trade(df),
    }

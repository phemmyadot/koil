"""
Pull every closed trade the strategy has taken across the full screened universe,
snapshot the entry-bar feature values (RSI, ATR%, distance from SMA in ATRs,
weekday), and see what separates >=10% winners from the rest.
"""
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import yfinance as yf
from backtesting import Backtest

sys.path.insert(0, "c:/Users/phemm/Desktop/pine-trend-strategy")
from p import PortfolioSizedEngine, RSI, ATR  # noqa: E402
from webapp.tickers import TICKERS  # noqa: E402

warnings.filterwarnings("ignore")
E = PortfolioSizedEngine

rows = []
CHUNK = 80

for i in range(0, len(TICKERS), CHUNK):
    chunk = TICKERS[i:i+CHUNK]
    t0 = time.time()
    try:
        df_all = yf.download(chunk, start="2020-01-01", interval="1d",
                              group_by="ticker", progress=False, auto_adjust=False, threads=True)
    except Exception as e:
        print(f"chunk {i}: download failed: {e}")
        continue

    for sym in chunk:
        try:
            df = df_all[sym] if len(chunk) > 1 else df_all
            df = df.drop(columns=["Adj Close"], errors="ignore").dropna()
            if len(df) < E.sma_slow_len + 20:
                continue

            macro_ma = df.Close.rolling(E.sma_slow_len).mean()
            rsi = RSI(df.Close, E.rsi_len)
            atr = ATR(df.High, df.Low, df.Close, 14)
            atr_pct = atr / df.Close

            bt = Backtest(df, PortfolioSizedEngine, cash=10_000, commission=0.001,
                          trade_on_close=True, finalize_trades=False)
            trades = bt.run()["_trades"]
            if trades.empty:
                continue

            for tr in trades.itertuples():
                et = tr.EntryTime
                if et not in df.index:
                    continue
                r = {
                    "ticker": sym,
                    "entry_date": et,
                    "return_pct": tr.ReturnPct * 100,
                    "duration_days": tr.Duration.days,
                    "rsi_at_entry": float(rsi.loc[et]) if not pd.isna(rsi.loc[et]) else np.nan,
                    "atr_pct_at_entry": float(atr_pct.loc[et]) * 100 if not pd.isna(atr_pct.loc[et]) else np.nan,
                    "dist_atr_at_entry": float((df.Close.loc[et] - macro_ma.loc[et]) / atr.loc[et])
                                          if not pd.isna(atr.loc[et]) and atr.loc[et] != 0 else np.nan,
                    "weekday": et.day_name(),
                    "month": et.month,
                }
                rows.append(r)
        except Exception as e:
            continue

    print(f"chunk {i//CHUNK+1}/{(len(TICKERS)+CHUNK-1)//CHUNK}: "
          f"{len(chunk)} tickers, {time.time()-t0:.1f}s, {len(rows)} trades so far")

out = pd.DataFrame(rows)
out.to_csv("trades_features.csv", index=False)
print(f"\nDONE. {len(out)} total closed trades across {out['ticker'].nunique()} tickers.")

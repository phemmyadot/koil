import datetime
import pandas as pd
import yfinance as yf
from backtesting import Backtest
from p import VolatilityExhaustionEngine

END = datetime.date.today()
START = "2015-01-01"

TICKERS = ["F", "T", "VZ", "PFE", "CSX", "SIRI", "ET", "KMI", "NOK", "SOFI",
           "RIVN", "CCL", "NCLH", "AAL", "DVN", "WBD", "VTRS", "CLF", "SNAP",
           "LYFT", "RIG", "HAL", "KGC", "NIO", "PBR", "ITUB", "VALE", "BBD",
           "GOLD", "IAG", "HL", "CDE", "AA", "MOS", "APA", "AR", "RRC", "BTG", "EQX"]

all_trade_pnls = []
per_ticker = []

for ticker in TICKERS:
    try:
        df = yf.download(ticker, start=str(START), end=str(END), interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 250:
            print(f"{ticker}: insufficient data ({len(df)} bars), skipping")
            continue
        bt = Backtest(df, VolatilityExhaustionEngine, cash=10000, commission=.001, finalize_trades=True)
        stats = bt.run()
        trades = stats["_trades"]
        pnls = trades["PnL"].tolist()
        all_trade_pnls.extend(pnls)
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = -sum(p for p in pnls if p <= 0)
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        wr = wins / n * 100 if n else 0
        per_ticker.append((ticker, n, wr, pf, stats["Return [%]"], stats["Buy & Hold Return [%]"]))
        print(f"{ticker:6} n={n:4} WR={wr:5.1f}% PF={pf:6.3f}  strat_ret={stats['Return [%]']:7.2f}%  buyhold={stats['Buy & Hold Return [%]']:7.2f}%")
    except Exception as e:
        print(f"{ticker}: ERROR {e}")

print("\n=== POOLED ===")
n = len(all_trade_pnls)
wins = sum(1 for p in all_trade_pnls if p > 0)
gross_profit = sum(p for p in all_trade_pnls if p > 0)
gross_loss = -sum(p for p in all_trade_pnls if p <= 0)
pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
wr = wins / n * 100 if n else 0
print(f"trades={n} wins={wins} WR={wr:.1f}% PF={pf:.3f}")

profitable = sum(1 for t, n_, wr_, pf_, r, bh in per_ticker if n_ > 0 and pf_ > 1.0)
losing = sum(1 for t, n_, wr_, pf_, r, bh in per_ticker if n_ > 0 and pf_ <= 1.0)
beat_buyhold = sum(1 for t, n_, wr_, pf_, r, bh in per_ticker if r > bh)
print(f"tickers profitable(PF>1)={profitable} losing={losing} | strategy beat buy&hold on {beat_buyhold}/{len(per_ticker)} tickers")

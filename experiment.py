"""Experiment harness for the Hardened Exhaustion Engine.

Implements the levers and train/test protocol from
docs/superpowers/specs/2026-07-14-exhaustion-improvement-design.md.
p.py remains the untouched TradingView mirror; fill semantics here are identical
(trade_on_close, resting tp/sl orders, finalize_trades=False).
"""
import warnings

import pandas as pd
import yfinance as yf
from backtesting import Backtest, Strategy

warnings.filterwarnings("ignore", message="Some trades remain open")

TICKERS = ['VTRS', 'SGHC', 'SLDE', 'BOC', 'JBGS', 'ABX', 'TAL',
           'WULF', 'DFTX', 'EWTX', 'ADPT', 'AUR', 'TRVI', 'SNDX',
           'ACHV', 'DSGN', 'ALM', 'SKE', 'SG', 'AGIO']
DATA_START = "2019-01-01"  # lead-in so 2020 entries have full indicator warm-up
DATA_END = "2026-06-01"
TRAIN = (pd.Timestamp("2020-01-01"), pd.Timestamp("2023-12-31"))
TEST = (pd.Timestamp("2024-01-01"), pd.Timestamp("2026-06-01"))


def SMA(values, n):
    return pd.Series(values).rolling(n).mean()


def RSI(values, n=14):
    delta = pd.Series(values).diff()
    gain = (delta.where(delta > 0, 0)).rolling(n).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(n).mean()
    return 100 - (100 / (1 + gain / (loss + 1e-9)))


def ATR(high, low, close, n=14):
    # Wilder's ATR (RMA), matching Pine ta.atr
    high, low, close = pd.Series(high), pd.Series(low), pd.Series(close)
    prev_close = close.shift()
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


class ExhaustionEngine(Strategy):
    # Core (fixed) parameters — identical to p.py / Pine
    sma_slow_len = 150
    bb_len = 20
    bb_mult = 1.75
    rsi_len = 14
    rsi_lower = 40.0
    alloc = 0.05

    # Levers under test (None = off = current production behavior)
    atr_stop_mult = None      # hard stop at entry - k*ATR14
    time_stop_bars = None     # exit if TP not reached within N bars
    min_sma_dist_atr = None   # require close >= sma + d*ATR14 at entry
    max_atr_pct = None        # skip entries when ATR14/close > this (volatility ceiling)

    # Entry window gating for train/test slicing
    entry_start = None
    entry_end = None

    def init(self):
        self.macro_ma = self.I(SMA, self.data.Close, self.sma_slow_len)
        self.bb_basis = self.I(SMA, self.data.Close, self.bb_len)
        self.std = self.I(lambda x: pd.Series(x).rolling(self.bb_len).std(ddof=0), self.data.Close)
        self.bb_lower = self.bb_basis - (self.std * self.bb_mult)
        self.rsi = self.I(RSI, self.data.Close, self.rsi_len)
        self.atr = self.I(ATR, self.data.High, self.data.Low, self.data.Close, 14)

    def next(self):
        bar_index = len(self.data) - 1
        price = self.data.Close[-1]

        if bar_index < self.sma_slow_len:
            return

        if self.trades:
            trade = self.trades[0]
            # Trend-break stop: close below macro SMA exits at bar close
            if price < self.macro_ma[-1]:
                trade.close()
                return
            # Time stop: reversion thesis dead if TP unhit after N bars
            if self.time_stop_bars is not None and (bar_index - trade.entry_bar) >= self.time_stop_bars:
                trade.close()
                return
            return

        dt = self.data.index[-1]
        if self.entry_start is not None and dt < self.entry_start:
            return
        if self.entry_end is not None and dt > self.entry_end:
            return

        macro_bullish = price > self.macro_ma[-1]
        bb_exhaustion = price < self.bb_lower[-1]
        rsi_washed_out = self.rsi[-1] <= self.rsi_lower
        if not (macro_bullish and bb_exhaustion and rsi_washed_out):
            return

        # Entry filter: skip exhaustion sitting right on the macro floor
        if self.min_sma_dist_atr is not None and price < self.macro_ma[-1] + self.min_sma_dist_atr * self.atr[-1]:
            return

        # Volatility ceiling: exhaustion in hyper-volatile regimes is a falling knife
        if self.max_atr_pct is not None and self.atr[-1] / price > self.max_atr_pct:
            return

        sl = None
        if self.atr_stop_mult is not None:
            sl = price - self.atr_stop_mult * self.atr[-1]
            if sl <= 0:
                sl = None  # degenerate for sub-$1 names; trend stop still applies

        self.buy(size=self.alloc, tp=self.bb_basis[-1], sl=sl)


def load(ticker):
    df = yf.download(ticker, start=DATA_START, end=DATA_END, interval="1d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.drop(columns=["Adj Close"], errors="ignore")


def run_config(datasets, window, **params):
    """Run one config over all tickers for one entry window; aggregate trades."""
    frames = []
    for ticker, df in datasets.items():
        d = df.loc[:window[1]]
        if len(d) < 200:
            continue
        bt = Backtest(d, ExhaustionEngine, cash=10000, commission=0.001,
                      trade_on_close=True, finalize_trades=False)
        stats = bt.run(entry_start=window[0], entry_end=window[1], **params)
        trades = stats["_trades"]
        if len(trades):
            frames.append(trades)
    tr = pd.concat(frames) if frames else pd.DataFrame(columns=["PnL", "ReturnPct"])
    n = len(tr)
    wins, losses = tr[tr.PnL > 0], tr[tr.PnL < 0]
    gross_loss = abs(losses.PnL.sum())
    return {
        "trades": n,
        "winrate": 100 * len(wins) / n if n else float("nan"),
        "pf": wins.PnL.sum() / gross_loss if gross_loss > 0 else float("inf"),
        "pnl": tr.PnL.sum(),
        "worst": 100 * tr.ReturnPct.min() if n else float("nan"),
    }


def row(label, r):
    return (f"{label:<28} trades={r['trades']:>3}  win={r['winrate']:6.2f}%  "
            f"PF={r['pf']:5.2f}  PnL=${r['pnl']:8.2f}  worst={r['worst']:7.2f}%")


if __name__ == "__main__":
    print("Loading data...")
    datasets = {t: load(t) for t in TICKERS}
    datasets = {t: d for t, d in datasets.items() if not d.empty}

    print("\n=== BASELINE ===")
    print(row("baseline TRAIN", run_config(datasets, TRAIN)))
    print(row("baseline TEST", run_config(datasets, TEST)))

    print("\n=== LEVER SWEEPS (TRAIN 2020-2023 only) ===")
    for k in (2.0, 3.0, 4.0):
        print(row(f"atr_stop k={k}", run_config(datasets, TRAIN, atr_stop_mult=k)))
    for n in (10, 15, 20):
        print(row(f"time_stop N={n}", run_config(datasets, TRAIN, time_stop_bars=n)))
    for d in (0.5, 1.0):
        print(row(f"sma_dist d={d}", run_config(datasets, TRAIN, min_sma_dist_atr=d)))

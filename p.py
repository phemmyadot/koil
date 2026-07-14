import yfinance as yf
import pandas as pd
import numpy as np
from backtesting import Strategy, Backtest

def SMA(values, n):
    return pd.Series(values).rolling(n).mean()

def RSI(values, n=14):
    delta = pd.Series(values).diff()
    gain = (delta.where(delta > 0, 0)).rolling(n).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(n).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def ATR(high, low, close, n=14):
    # Wilder's ATR (RMA), matching Pine ta.atr
    high, low, close = pd.Series(high), pd.Series(low), pd.Series(close)
    prev_close = close.shift()
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

class PortfolioSizedEngine(Strategy):
    sma_slow_len = 150  
    bb_len = 20
    bb_mult = 1.75      
    rsi_len = 14
    rsi_lower = 40.0
    time_stop_bars = 20      # Pine: timeStopBars (0 = off)
    min_sma_dist_atr = 0.5   # Pine: smaDistAtr (0 = off)
    alloc = 0.15             # Pine: default_qty_value=15

    def init(self):
        self.macro_ma = self.I(SMA, self.data.Close, self.sma_slow_len)
        self.bb_basis = self.I(SMA, self.data.Close, self.bb_len)
        self.std = self.I(lambda x: pd.Series(x).rolling(self.bb_len).std(ddof=0), self.data.Close)
        self.bb_lower = self.bb_basis - (self.std * self.bb_mult)
        self.rsi = self.I(RSI, self.data.Close, self.rsi_len)
        self.atr = self.I(ATR, self.data.High, self.data.Low, self.data.Close, 14)

    def next(self):
        bar_index = len(self.data) - 1  # Pine bar_index is 0-based
        current_price = self.data.Close[-1]

        # Pine: hasEnoughData = bar_index >= smaSlowLen
        if bar_index < self.sma_slow_len:
            return

        # Pine block 1 — Invalidation Stop: close below macro SMA exits at bar close
        if self.trades and current_price < self.macro_ma[-1]:
            self.trades[0].close()
            return

        # Pine block 2 — Time Stop: reversion thesis dead if TP not reached within N bars
        if self.trades and self.time_stop_bars > 0 and (bar_index - self.trades[0].entry_bar) >= self.time_stop_bars:
            self.trades[0].close()
            return

        macro_bullish = current_price > self.macro_ma[-1]
        bb_exhaustion = current_price < self.bb_lower[-1]
        rsi_washed_out = self.rsi[-1] <= self.rsi_lower
        sma_dist_ok = self.min_sma_dist_atr == 0 or current_price >= self.macro_ma[-1] + self.min_sma_dist_atr * self.atr[-1]

        # Pine block 3 — Entry fills at signal bar close (trade_on_close=True);
        # tp= is the resting limit at the frozen mid-band (strategy.exit limit=targetMid)
        if not self.trades and macro_bullish and bb_exhaustion and rsi_washed_out and sma_dist_ok:
            self.buy(size=self.alloc, tp=self.bb_basis[-1])

if __name__ == "__main__":
    target_universe = ['NVTS', 'MX', 'SKYT', 'MRAM', 'ATOM', 'LAR', 'ALOY', 'AXGN', 'UFO', 'NEBX']
    
    print("Executing Low-Allocation Unconstrained Portfolio Run:\n")
    
    for ticker in target_universe:
        # auto_adjust=False: TradingView uses split-adjusted, NOT dividend-adjusted prices
        df = yf.download(ticker, start="2020-01-01", end="2026-06-01", interval="1d", progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.drop(columns=["Adj Close"], errors="ignore")
        if df.empty:
            continue
            
        # finalize_trades=False: TradingView's closed-trade stats exclude a still-open position
        bt = Backtest(df, PortfolioSizedEngine, cash=10000, commission=0.001, trade_on_close=True, finalize_trades=False)
        stats = bt.run()
        
        print(f"{ticker}: Total Trades={stats['# Trades']} | Win Rate={stats['Win Rate [%]']:.2f}% | Return={stats['Return [%]']:.2f}% | Stock % Change={stats['Buy & Hold Return [%]']:.2f}%")
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

class PortfolioSizedEngine(Strategy):
    sma_slow_len = 150  
    bb_len = 20
    bb_mult = 1.75      
    rsi_len = 14
    rsi_lower = 40.0    

    def init(self):
        self.macro_ma = self.I(SMA, self.data.Close, self.sma_slow_len)
        self.bb_basis = self.I(SMA, self.data.Close, self.bb_len)
        self.std = self.I(lambda x: pd.Series(x).rolling(self.bb_len).std(ddof=0), self.data.Close)
        self.bb_lower = self.bb_basis - (self.std * self.bb_mult)
        self.rsi = self.I(RSI, self.data.Close, self.rsi_len)
        
        self.target_mid = None
        self.last_exit_idx = -1

    def next(self):
        current_idx = len(self.data)
        current_price = self.data.Close[-1]
        
        if current_idx < self.sma_slow_len:
            return

        if self.trades:
            if self.data.High[-1] >= self.target_mid:
                self.trades[0].close()
                self.last_exit_idx = current_idx
                return

        macro_bullish = current_price > self.macro_ma[-1]
        bb_exhaustion = current_price < self.bb_lower[-1]
        rsi_washed_out = self.rsi[-1] <= self.rsi_lower

        if not self.trades and macro_bullish and bb_exhaustion and rsi_washed_out and current_idx > self.last_exit_idx:
            self.target_mid = self.bb_basis[-1]
            # Lowering allocation size to 5% to absorb tail risk comfortably
            self.buy(size=0.05, limit=current_price) 

if __name__ == "__main__":
    target_universe = ['NVTS', 'MX', 'SKYT', 'MRAM', 'ATOM', 'LAR', 'ALOY', 'AXGN', 'UFO', 'NEBX']
    
    print("Executing Low-Allocation Unconstrained Portfolio Run:\n")
    
    for ticker in target_universe:
        df = yf.download(ticker, start="2020-01-01", end="2026-06-01", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            continue
            
        bt = Backtest(df, PortfolioSizedEngine, cash=10000, commission=0.001, finalize_trades=True)
        stats = bt.run()
        
        print(f"{ticker}: Total Trades={stats['# Trades']} | Win Rate={stats['Win Rate [%]']:.2f}% | Return={stats['Return [%]']:.2f}%")
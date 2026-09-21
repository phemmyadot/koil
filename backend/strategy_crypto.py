"""Crypto compute path -- runs strategy_vcp.run() per ticker with the shared, validated crypto
config (crypto_universe.CONFIG_BY_TICKER, one config for every ticker -- see that module's own
comment for why the large_cap/meme buckets no longer carry separate params), not the shared
module's own defaults. strategy_vcp.py itself
is untouched (shared/validated against equity); this just binds crypto's config via
functools.partial and calls strategy_common.evaluate_strategy directly, the same scaffold
strategy_vcp.evaluate() uses internally.

entry_start is overridden separately from CONFIG (not one of the pine-input values a user types
by hand) because pines/vcp_crypto.pine's own start_date default is 1 Jan 2021, a year earlier
than strategy_vcp.py's ENTRY_START default of 1 Jan 2022 (which matches the EQUITY vcp.pine
instead). Left at the module default, crypto silently dropped every 2021 breakout -- confirmed
against a TradingView read of CRV-USD, whose trade count only matched TradingView's 34 (was 29)
once this override was added.

Known gap: strategy_vcp.run()'s qty is risk-based (risk_pct/initial_capital), not the crypto
strategy's validated $500-fixed-notional sizing (pines/vcp_crypto.pine's trade_notional/close) --
dollar_pnl in the resulting trades therefore does NOT match the $500-notional backtest exactly.
Trade signal/verdict timing (entry/exit, win rate, PF) is unaffected, only the $ figures.
CONFIG's risk_pct (crypto_universe.py) is tuned so median position size lands near $500 given a
$1500 account, rather than the ~$20-100 positions strategy_vcp.py's 1.0 default gives on
crypto's wider ATR-based stops -- see crypto_universe.py's comment for the empirical basis.
"""
import functools

import pandas as pd

import backend.crypto_universe as crypto_universe
import backend.strategy_common as common
import backend.strategy_vcp as strategy_vcp

# Matches pines/vcp_crypto.pine's own start_date default -- see module docstring.
ENTRY_START = pd.Timestamp("2021-01-01")


def evaluate(ticker: str, bars) -> dict:
    config = crypto_universe.CONFIG_BY_TICKER[ticker]
    run_fn = functools.partial(strategy_vcp.run, entry_start=ENTRY_START, **config)
    result = common.evaluate_strategy(ticker, bars, run_fn, strategy_vcp.compute_indicators,
                                       min_bars=250)
    # Manual-validation aid (cross-checking against a TradingView chart): the shared summarize()
    # strips last5_trades down to {days, tp_pct}, dropping the actual entry/exit dates+prices
    # needed to line trades up against what a chart shows. Recomputed here (crypto-only, doesn't
    # touch strategy_common.py/equity) rather than widening the shared shape.
    df = common.with_earnings_flags(bars, ticker)
    ind = strategy_vcp.compute_indicators(df)
    trades, *_ = run_fn(df, ind)
    result["last5_trades_detailed"] = [
        {
            "entry_date": str(t["entry_date"].date()),
            "entry_price": round(float(t["entry_price"]), 6),
            "exit_date": str(t["exit_date"].date()),
            "exit_price": round(float(t["exit_price"]), 6),
            "pnl_pct": t["pnl_pct"],
        }
        for t in trades[-5:]
    ]
    result["data_range"] = {"start": str(df.index[0].date()), "end": str(df.index[-1].date()), "n_bars": len(df)}
    return result

"""Crypto compute path -- runs strategy_vcp.run() per ticker with ITS bucket's validated config
(crypto_universe.CONFIG_BY_TICKER), not the shared module's own defaults. strategy_vcp.py itself
is untouched (shared/validated against equity); this just binds bucket-specific kwargs via
functools.partial and calls strategy_common.evaluate_strategy directly, the same scaffold
strategy_vcp.evaluate() uses internally.

Known gap: strategy_vcp.run()'s qty is risk-based (risk_pct/initial_capital), not the crypto
strategy's validated $500-fixed-notional sizing (pines/vcp_crypto.pine's trade_notional/close) --
dollar_pnl in the resulting trades therefore does NOT match the $500-notional backtest exactly.
Trade signal/verdict timing (entry/exit, win rate, PF) is unaffected, only the $ figures.
Each bucket's risk_pct (crypto_universe.py) is tuned so median position size lands near $500
given a $1500 account, rather than the ~$20-100 positions strategy_vcp.py's 1.0 default gives on
crypto's wider ATR-based stops -- see crypto_universe.py's comment for the empirical basis.
"""
import functools

import backend.crypto_universe as crypto_universe
import backend.strategy_common as common
import backend.strategy_vcp as strategy_vcp


def evaluate(ticker: str, bars) -> dict:
    config = crypto_universe.CONFIG_BY_TICKER[ticker]
    run_fn = functools.partial(strategy_vcp.run, **config)
    return common.evaluate_strategy(ticker, bars, run_fn, strategy_vcp.compute_indicators,
                                     min_bars=250)

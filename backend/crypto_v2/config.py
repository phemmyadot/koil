"""Versioned strategy config for the crypto v2 signal engine -- a plain dict (matching
crypto_universe.py's own config-constant style, not a YAML file on disk) so there's no new
parsing dependency. config_hash() lets every stored signal/strategy_version row record exactly
which config produced it (crypto_v2_strategy_versions) -- the kind of accountability that would
have caught pines/vcp_crypto.pine's silent drift from crypto_universe.py's live config.

Values below are untuned starting points (the doc's own §68: "do not optimize the first
version" -- establish correctness first, then run the same kind of pooled-backtest/walk-forward
validation already done for crypto_universe.py's LARGE_CAP_CONFIG/MEME_CONFIG retune). Daily-bar
only -- no 1h/4h variants like the source document's examples, since this reuses the existing
Coinbase daily-bar pipeline (data_crypto.py), not a new intraday one.
"""
import hashlib
import json

STRATEGY_NAME = "crypto_v2_multi_setup"
STRATEGY_VERSION = "2.0.0"

CONFIG = {
    "trend": {"ema_fast": 20, "ema_medium": 50, "ema_slow": 200},
    "volatility": {"atr_period": 14, "percentile_lookback": 100, "expansion_percentile": 75,
                   "compression_percentile": 25},
    "volume": {"rvol_period": 20, "minimum_rvol": 2.0},
    "momentum": {"rsi_period": 14, "roc_period": 10},
    # Daily-bar relative strength: N-trading-day return vs BTC/ETH, ranked cross-sectionally
    # against every other ticker in the same compute pass (see engine.py).
    "relative_strength": {"lookback_days": 10, "minimum_percentile": 70},
    "structure": {"swing_left": 3, "swing_right": 3, "breakout_lookback": 20},
    "liquidity": {"equal_level_tolerance_pct": 0.5, "prior_week_days": 5},
    # Stop = structural invalidation (swing low, or the sweep's low for a sweep-reversal entry)
    # minus an ATR buffer -- see doc §28. atr_mult is that buffer's multiple, not the whole stop
    # distance like v1's simpler entry_price - ATR*mult.
    "risk": {"stop_atr_buffer_mult": 0.25, "tp1_r": 1.0, "tp2_r": 2.0, "max_bars": 20,
             "risk_pct": 1.0, "initial_capital": 1500.0},
    "filters": {"minimum_setup_score": 5, "minimum_execution_score": 3},
    # Component -> point value, doc §23's model. Keys must match the component names each
    # setups.py detector actually emits (scoring.setup_score does a plain dict lookup, so a
    # name that doesn't match here silently scores 0 even when the component fired -- this bit
    # us once already while building this module, see engine.py's evaluate() smoke test).
    "scoring": {
        "range_break": 2, "breaks_range": 2, "structure_bos": 2, "liquidity_sweep": 2,
        "volume_expansion": 2, "relative_strength": 2, "volatility_expansion": 1,
        "impulse": 1, "range_compression": 1,
    },
}


def config_hash() -> str:
    return hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:12]

"""Versioned strategy config for the crypto v2 signal engine -- a plain dict (matching
crypto_universe.py's own config-constant style, not a YAML file on disk) so there's no new
parsing dependency. config_hash() lets every stored signal/strategy_version row record exactly
which config produced it (crypto_v2_strategy_versions) -- the kind of accountability that would
have caught pines/vcp_crypto.pine's silent drift from crypto_universe.py's live config.

Most values below are still untuned starting points (the doc's own §68: "do not optimize the
first version"). risk.stop_atr_ceiling_mult and setups.ALL_SETUPS (which setups are actually
entry-triggering) got a first real pass on 2026-09-21, via a pooled backtest across the full
discovered universe (556 tickers, ~13.5k trades) plus a per-setup isolation test -- see their own
comments/docstrings for the numbers. Net result: pooled PF 1.33 / WR 56.5% -- a real, positive
edge, but still thin, not yet "solid" in the sense of a comfortable margin (no fees/slippage
modeled here either). The two fixes found were about correctness (an unbounded stop, and a setup
that was accidentally duplicating another one) rather than classic parameter optimization, and
neither moved the pooled headline number much on its own -- the aggregate is currently limited by
the three remaining setups sharing a common, only moderately-selective "close > last confirmed
swing high" trigger (confirmed: reordering entry priority among them changes nothing in the
pooled result, down to the exact trade count), not by risk/exit parameters. Meaningfully raising
the edge further would need differentiating that shared trigger itself, not another tweak pass.

Daily-bar only -- no 1h/4h variants like the source document's examples, since this reuses the
existing Coinbase daily-bar pipeline (data_crypto.py), not a new intraday one.
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
    # distance like v1's simpler entry_price - ATR*mult. stop_atr_ceiling_mult caps how far that
    # structural stop is allowed to sit from entry -- added 2026-09-21 after a pooled backtest
    # across the full discovered universe (556 tickers, ~13k trades) showed a real, quantified
    # problem: an UNBOUNDED "last confirmed swing low" stop is frequently very wide on the more
    # volatile tickers that dominate this universe (70% meme/low-cap) -- median stop was already
    # 22% of price (3.1x ATR), p90 43% (5.0x ATR), tail up to 107% (20x ATR, wider than the
    # entry price itself). That makes R (the risk unit) huge, so tp1_r's breakeven/protection
    # logic -- gated on reaching a full 1R gain -- rarely activates before a trade round-trips
    # from a decent unrealized gain back into that same wide stop. Capped at 4.0x ATR, close to
    # v1's own validated large-cap atr_mult=3.0/meme atr_mult=6.0 range (crypto_universe.py).
    # Be honest about what this did and didn't fix: a ceiling grid search (1.5x-999x/uncapped)
    # found the pooled PF plateaus at ~1.32-1.33 for any ceiling >=3.5x, and pooled max drawdown
    # actually got slightly WORSE at 4.0x (584.67) than fully uncapped (476.98) -- tighter caps
    # cut off more trades via whipsaw stop-outs than they save from the rare huge-stop blowup, in
    # THIS pooled-across-520-independent-tickers metric. The real justification is per-trade tail
    # risk (one 100%+-of-price stop is a real risk to an actual trader concentrated in a handful
    # of positions, even if it barely registers pooled across hundreds of $1500 backtest
    # sub-accounts) and consistent, predictable position sizing -- not a pooled-PF improvement.
    "risk": {"stop_atr_buffer_mult": 0.25, "stop_atr_ceiling_mult": 4.0, "tp1_r": 1.0, "tp2_r": 2.0,
             "max_bars": 20, "risk_pct": 1.0, "initial_capital": 1500.0},
    "filters": {"minimum_setup_score": 5, "minimum_execution_score": 3},
    # Component -> point value, doc §23's model. Keys must match the component names each
    # setups.py detector actually emits (scoring.setup_score does a plain dict lookup, so a
    # name that doesn't match here silently scores 0 even when the component fired -- this bit
    # us once already while building this module, see engine.py's evaluate() smoke test).
    "scoring": {
        "range_break": 2, "breaks_range": 2, "structure_bos": 2, "liquidity_sweep": 2,
        "volume_expansion": 2, "relative_strength": 2, "volatility_expansion": 1,
        "impulse": 1, "range_compression": 1, "established_trend": 2,
    },
}


def config_hash() -> str:
    return hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:12]

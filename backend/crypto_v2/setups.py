"""Setup detectors (doc §19-22) -- each returns (detected, components): a boolean Series for
whether the setup triggered on each bar, plus the named boolean sub-conditions behind it (for
explainability -- doc §49's "why the signal triggered"). All lookahead-safe by construction:
every input (features/structure/liquidity) already only carries confirmed-as-of-bar-k values.

Long setups only for this pass -- short logic is the mirrored implementation per doc §67, left
for a later pass. Simplified relative to the document in a couple of places, flagged inline:
momentum_continuation skips the "pullback then higher low" sub-pattern (doc §20); the stop model
in engine.py also doesn't yet differentiate the sweep-reversal setup's own sweep-low as its stop
basis, using the same swing-low basis as the other three instead.

momentum_continuation's structural condition deliberately uses price_vs_ema_medium (an
established uptrend), not structure.bullish_bos -- a pooled backtest across the full discovered
universe (2026-09-21) found breakout and momentum_continuation producing near-identical trade
sets (9269 vs 9263 trades, same PF/WR to 2 decimals) when each was tested in isolation, because
both were gated on the exact same "close > last_swing_high[1]" fresh-break condition (bos and
range_break are the same formula) -- momentum_continuation was accidentally breakout's near-
duplicate, not a genuinely different setup ("continuing an existing trend" vs "just broke a
fresh high" are different theses, and should fire on different bars).
"""
import pandas as pd


def breakout(df: pd.DataFrame, features: pd.DataFrame, swing_high: pd.Series, config: dict) -> tuple[pd.Series, dict]:
    close = df["Close"]
    range_break = close > swing_high.shift(1)
    components = {
        "range_break": range_break.fillna(False),
        "volume_expansion": features["volume_expansion"].fillna(False),
        "relative_strength": (features["rs_btc"] > 0).fillna(False),
        "volatility_expansion": features["atr_expansion"].fillna(False),
    }
    detected = components["range_break"] & components["volume_expansion"] & components["relative_strength"]
    return detected, components


def momentum_continuation(df: pd.DataFrame, features: pd.DataFrame, structure: pd.DataFrame, config: dict) -> tuple[pd.Series, dict]:
    components = {
        "impulse": (features["roc"] > 0).fillna(False),
        "volume_expansion": features["volume_expansion"].fillna(False),
        # Sustained trend (price above its own medium EMA), not a fresh break above swing_high --
        # see module docstring for why bullish_bos here made this a near-duplicate of breakout().
        "established_trend": (features["price_vs_ema_medium"] > 0).fillna(False),
        "relative_strength": (features["rs_btc"] > 0).fillna(False),
    }
    detected = components["impulse"] & components["volume_expansion"] & components["established_trend"] & components["relative_strength"]
    return detected, components


def liquidity_sweep_reversal(df: pd.DataFrame, features: pd.DataFrame, structure: pd.DataFrame,
                              liquidity: pd.DataFrame, config: dict) -> tuple[pd.Series, dict]:
    components = {
        "liquidity_sweep": liquidity["bullish_sweep"].fillna(False),
        "structure_bos": structure["bullish_bos"].fillna(False),
    }
    detected = components["liquidity_sweep"] & components["structure_bos"]
    return detected, components


def range_breakout(df: pd.DataFrame, features: pd.DataFrame, swing_high: pd.Series, config: dict) -> tuple[pd.Series, dict]:
    close = df["Close"]
    lookback = config["structure"]["breakout_lookback"]
    was_compressed = features["atr_compression"].astype(int).shift(1).rolling(lookback).max().fillna(0) > 0
    components = {
        "range_compression": was_compressed,
        "breaks_range": (close > swing_high.shift(1)).fillna(False),
        "volume_expansion": features["volume_expansion"].fillna(False),
    }
    detected = components["range_compression"] & components["breaks_range"] & components["volume_expansion"]
    return detected, components


# Entry priority AND the enumeration engine.py iterates for both backtesting and live
# signals_today -- momentum_continuation is deliberately excluded (kept as a function, still
# computed/scored, just not entry-triggering): the same 2026-09-21 pooled backtest that found
# it near-duplicating breakout also found its TRUE standalone quality, once genuinely
# differentiated (see momentum_continuation's own docstring), is net-negative (PF 0.96, WR
# 46.9% across ~1950 isolated trades) -- worse than a coin flip after costs. range_breakout is
# listed first since it's the strongest setup by a wide margin (PF 4.5+, WR ~78%) and should win
# any same-bar priority tie against the two more mediocre-but-still-positive setups behind it.
ALL_SETUPS = ["range_breakout", "liquidity_sweep_reversal", "breakout"]

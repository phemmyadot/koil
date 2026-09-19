"""Setup detectors (doc §19-22) -- each returns (detected, components): a boolean Series for
whether the setup triggered on each bar, plus the named boolean sub-conditions behind it (for
explainability -- doc §49's "why the signal triggered"). All lookahead-safe by construction:
every input (features/structure/liquidity) already only carries confirmed-as-of-bar-k values.

Long setups only for this pass -- short logic is the mirrored implementation per doc §67, left
for a later pass. Simplified relative to the document in a couple of places, flagged inline:
momentum_continuation skips the "pullback then higher low" sub-pattern (doc §20) and just
requires impulse + volume + structure + relative strength; the stop model in engine.py also
doesn't yet differentiate the sweep-reversal setup's own sweep-low as its stop basis, using the
same swing-low basis as the other three instead.
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
        "structure_bos": structure["bullish_bos"].fillna(False),
        "relative_strength": (features["rs_btc"] > 0).fillna(False),
    }
    detected = components["impulse"] & components["volume_expansion"] & components["structure_bos"] & components["relative_strength"]
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


ALL_SETUPS = ["breakout", "momentum_continuation", "liquidity_sweep_reversal", "range_breakout"]

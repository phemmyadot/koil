"""Structure engine -- swing highs/lows and break-of-structure (doc §17), price-action only.

A swing point needs `right` additional bars to confirm (its High/Low must be the extreme across
[i-left, i+right]) -- doc §17 requires recording each pivot's actual availability time, not the
bar it occurred on. swing_points() returns the most-recently-CONFIRMED level as of each bar,
forward-filled, so a value read at bar k was genuinely knowable at k, never leaked from the
future. HH/HL/LH/LL sequencing (doc §17's list) is left for a later pass -- break_of_structure
already captures the one piece of structure the setup detectors actually need.
"""
import pandas as pd


def swing_points(df: pd.DataFrame, left: int, right: int) -> tuple[pd.Series, pd.Series]:
    h, l = df["High"], df["Low"]
    window = left + right + 1
    is_swing_high = h == h.rolling(window).max().shift(-right)
    is_swing_low = l == l.rolling(window).min().shift(-right)
    swing_high_at_occurrence = h.where(is_swing_high)
    swing_low_at_occurrence = l.where(is_swing_low)
    # .shift(right) moves each confirmed value from its occurrence bar (i) to its actual
    # availability bar (i + right); ffill then carries the latest known level forward.
    swing_high_known = swing_high_at_occurrence.shift(right).ffill()
    swing_low_known = swing_low_at_occurrence.shift(right).ffill()
    return swing_high_known, swing_low_known


def break_of_structure(df: pd.DataFrame, swing_high: pd.Series, swing_low: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Bullish BOS: close breaks above the swing high known as of the PRIOR bar (matching
    strategy_vcp.py's own resistance = h.rolling(...).max().shift(1) convention -- a breakout
    clears a level already established before today, not one that happens to confirm today)."""
    c = df["Close"]
    bullish_bos = c > swing_high.shift(1)
    bearish_bos = c < swing_low.shift(1)
    return bullish_bos, bearish_bos

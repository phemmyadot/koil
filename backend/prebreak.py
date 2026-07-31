"""
Pre-breakout scorer, ported from pines/pre-break.pine (indicator "UPB").
Not a trading strategy -- a per-ticker structural/regime classifier (state +
score) that applies across all three dashboard strategies (VEXH, VCP, VCPO),
the same way the Pine indicator overlays on a chart regardless of which
strategy you're trading. Computed once per ticker from the shared raw-data
cache (backend/data.py).
"""
import pandas as pd

BB_LENGTH = 20
BB_MULT = 2.0
SQZ_LOOKBACK = 50
VOL_LENGTH = 20
VOL_THRESHOLD = 0.85
RES_LENGTH = 20
PROX_THRESHOLD = 2.5
TREND_LENGTH = 50
BREAKOUT_VOL_MULT = 1.25

STATE_SCORES = {
    "BEARISH": -2, "NEUTRAL": 0, "BULLISH": 1, "COILING (BULL)": 2,
    "PRE-BREAKOUT": 4, "BREAKOUT": 5,
}


def evaluate(ticker: str, df: pd.DataFrame) -> dict:
    """Returns the latest bar's pre-breakout state/score and supporting
    metrics. Raises on insufficient history (mirrors the other evaluators)."""
    if len(df) < TREND_LENGTH + SQZ_LOOKBACK:
        raise ValueError("insufficient history")

    c, h, l, v = df.Close, df.High, df.Low, df.Volume

    basis = c.rolling(BB_LENGTH).mean()
    dev = BB_MULT * c.rolling(BB_LENGTH).std(ddof=0)
    upper, lower = basis + dev, basis - dev
    bb_width = (upper - lower) / basis
    bb_squeeze = bb_width < bb_width.rolling(SQZ_LOOKBACK).mean()

    vol_ma = v.rolling(VOL_LENGTH).mean()
    vol_dry_up = v < vol_ma * VOL_THRESHOLD

    # Excludes the current bar (shift(1)) to monitor a true breakout, same as
    # pre-break.pine's ta.highest(high, resLength)[1].
    res_high = h.rolling(RES_LENGTH).max().shift(1)
    res_low = l.rolling(RES_LENGTH).min().shift(1)
    pct_from_resistance = (res_high - c) / c * 100
    near_resistance = (pct_from_resistance <= PROX_THRESHOLD) & (pct_from_resistance >= 0)

    ema_trend = c.ewm(span=TREND_LENGTH, adjust=False).mean()
    is_bullish_trend = c > ema_trend
    is_bearish_trend = c < ema_trend

    crossover = (c.shift(1) <= res_high.shift(1)) & (c > res_high)
    is_breakout = crossover & (v > vol_ma * BREAKOUT_VOL_MULT)

    # squeezeCounter/projectedTarget/breakoutLevel are stateful (Pine `var`),
    # so replayed bar-by-bar rather than vectorized -- comparisons against
    # NaN during each series' own warm-up resolve to False, same as Pine
    # simply not firing on bars where an indicator isn't meaningful yet.
    squeeze_counter = 0
    projected_target = None
    projected_duration = None
    breakout_level = None

    for i in range(1, len(df)):
        if bool(bb_squeeze.iloc[i]) or bool(near_resistance.iloc[i]):
            squeeze_counter += 1
        else:
            squeeze_counter = 0

        if bool(is_breakout.iloc[i]):
            base_depth = res_high.iloc[i] - res_low.iloc[i]
            projected_target = res_high.iloc[i] + base_depth
            projected_duration = int(squeeze_counter * 0.75)
            breakout_level = res_high.iloc[i]

        if projected_target is not None:
            close_i = c.iloc[i]
            if close_i < ema_trend.iloc[i] or close_i < breakout_level or close_i >= projected_target:
                projected_target = None
                projected_duration = None
                breakout_level = None

    if bool(is_bearish_trend.iloc[-1]):
        state = "BEARISH"
    elif bool(is_breakout.iloc[-1]):
        state = "BREAKOUT"
    elif (bool(bb_squeeze.iloc[-1]) and bool(vol_dry_up.iloc[-1])
          and bool(near_resistance.iloc[-1]) and bool(is_bullish_trend.iloc[-1])):
        state = "PRE-BREAKOUT"
    elif bool(bb_squeeze.iloc[-1]) and bool(is_bullish_trend.iloc[-1]):
        state = "COILING (BULL)"
    elif bool(is_bullish_trend.iloc[-1]):
        state = "BULLISH"
    else:
        state = "NEUTRAL"

    return {
        "state": state,
        "score": STATE_SCORES[state],
        "bb_squeeze": bool(bb_squeeze.iloc[-1]),
        "vol_dry_up": bool(vol_dry_up.iloc[-1]),
        "near_resistance": bool(near_resistance.iloc[-1]),
        "is_bullish_trend": bool(is_bullish_trend.iloc[-1]),
        "squeeze_counter": squeeze_counter,
        "projected_target": round(float(projected_target), 4) if projected_target is not None else None,
        "projected_duration": projected_duration,
    }

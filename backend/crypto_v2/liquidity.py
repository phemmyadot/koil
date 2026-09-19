"""Liquidity engine -- price levels where resting orders likely cluster (doc §18), and sweep
detection against them. Pure price-action; no order-book data exists in this scope (see
scoring.execution_score for how the *execution* liquidity gate, doc §25, is proxied instead --
not to be confused with this module, which is about liquidity-pool price LEVELS, not depth).
"""
import pandas as pd


def liquidity_levels(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Prior-day and prior-week high/low -- doc §18's suggested minimum level set. Equal
    highs/lows need a live order-book or tick-level touch count to detect reliably and are
    skipped here; prior-day/week levels alone already give sweep detection real, lookahead-safe
    levels (both use .shift(1), so today's bar is always compared against an already-known
    level, never one derived from today itself)."""
    h, l = df["High"], df["Low"]
    week = cfg["prior_week_days"]
    return pd.DataFrame({
        "prior_day_high": h.shift(1), "prior_day_low": l.shift(1),
        "prior_week_high": h.rolling(week).max().shift(1), "prior_week_low": l.rolling(week).min().shift(1),
    })


def sweep_signals(df: pd.DataFrame, levels: pd.DataFrame) -> pd.DataFrame:
    """Bullish sweep: this bar's low dips below a known liquidity level, then closes back above
    it -- stop-hunt-then-reclaim, doc §18's exact definition. Bearish is the mirror. Checked
    against both the prior-day and prior-week level; either counts."""
    low, high, close = df["Low"], df["High"], df["Close"]
    bullish = ((low < levels["prior_day_low"]) & (close > levels["prior_day_low"])) | \
              ((low < levels["prior_week_low"]) & (close > levels["prior_week_low"]))
    bearish = ((high > levels["prior_day_high"]) & (close < levels["prior_day_high"])) | \
              ((high > levels["prior_week_high"]) & (close < levels["prior_week_high"]))
    return pd.DataFrame({"bullish_sweep": bullish, "bearish_sweep": bearish})

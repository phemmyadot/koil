"""Feature engine -- normalized, reusable per-bar observations (doc §11-16), daily-bar only.
Every feature here is confirmed-at-close: a value at row i is derived only from bars <= i (or
btc_df/eth_df reindexed to the same dates), so it never uses information unavailable at that
bar's own close -- doc §3.2's no-lookahead rule. Reuses strategy_common.wilder_atr rather than
reimplementing ATR a third time in this codebase.
"""
import numpy as np
import pandas as pd

import backend.strategy_common as common


def _rolling_percentile(s: pd.Series, window: int) -> pd.Series:
    """Percentile rank (0-100) of the latest value within its own trailing window -- doc
    §12-13's "prefer percentile over absolute threshold" framing for ATR/volume."""
    def pct_rank(x):
        return (x < x.iloc[-1]).sum() / (len(x) - 1) * 100 if len(x) > 1 else np.nan
    return s.rolling(window).apply(pct_rank, raw=False)


def compute_trend(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = df["Close"]
    ema_fast = c.ewm(span=cfg["ema_fast"], adjust=False).mean()
    ema_medium = c.ewm(span=cfg["ema_medium"], adjust=False).mean()
    ema_slow = c.ewm(span=cfg["ema_slow"], adjust=False).mean()
    return pd.DataFrame({
        "ema_fast": ema_fast, "ema_medium": ema_medium, "ema_slow": ema_slow,
        "ema_fast_slope": ema_fast.diff(), "ema_medium_slope": ema_medium.diff(),
        "price_vs_ema_fast": (c - ema_fast) / ema_fast,
        "price_vs_ema_medium": (c - ema_medium) / ema_medium,
        "price_vs_ema_slow": (c - ema_slow) / ema_slow,
        "ema_medium_vs_slow": (ema_medium - ema_slow) / ema_slow,
    })


def compute_volatility(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    h, l, c = df["High"], df["Low"], df["Close"]
    atr = common.wilder_atr(h, l, c, cfg["atr_period"])
    atr_pct = atr / c
    atr_avg = atr_pct.rolling(cfg["percentile_lookback"]).mean()
    atr_percentile = _rolling_percentile(atr_pct, cfg["percentile_lookback"])
    return pd.DataFrame({
        "atr": atr, "atr_pct": atr_pct, "atr_avg": atr_avg, "atr_percentile": atr_percentile,
        "atr_expansion": atr_percentile >= cfg["expansion_percentile"],
        "atr_compression": atr_percentile <= cfg["compression_percentile"],
    })


def compute_volume(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    v = df["Volume"]
    vol_avg = v.rolling(cfg["rvol_period"]).mean()
    # A zero-volume window (a thinly-traded ticker with literally no reported volume on some
    # early days) would otherwise divide to +inf, not just NaN -- treat "no average volume to
    # compare against" as unknown (NaN), same as any other missing feature, not "infinite spike".
    rvol = v / vol_avg.replace(0, float("nan"))
    return pd.DataFrame({"vol_avg": vol_avg, "rvol": rvol, "volume_expansion": rvol >= cfg["minimum_rvol"]})


def compute_momentum(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = df["Close"]
    roc = c / c.shift(cfg["roc_period"]) - 1
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(cfg["rsi_period"]).mean()
    loss = (-delta.clip(upper=0)).rolling(cfg["rsi_period"]).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return pd.DataFrame({"roc": roc, "rsi": rsi})


def own_return(df: pd.DataFrame, lookback_days: int) -> pd.Series:
    """The N-day return this ticker's relative-strength feature is measured against -- exposed
    separately so a caller can gather every tracked ticker's own_return for the same date
    before ranking (relative strength is inherently cross-sectional, see
    engine.universe_returns_by_ticker)."""
    c = df["Close"]
    return c / c.shift(lookback_days) - 1


def compute_relative_strength(df: pd.DataFrame, btc_df: pd.DataFrame | None,
                               eth_df: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    """rs_btc/rs_eth only (doc §15) -- the cross-sectional percentile rank across the tracked
    universe needs every ticker's own_return for the same date, not just this one, so it's an
    engine-level step (engine.universe_returns_by_ticker + evaluate()), not part of this
    per-ticker computation."""
    asset_return = own_return(df, cfg["lookback_days"])
    out = pd.DataFrame({"asset_return": asset_return}, index=df.index)
    for label, other in (("btc", btc_df), ("eth", eth_df)):
        if other is None:
            out[f"rs_{label}"] = np.nan
            continue
        other_return = own_return(other, cfg["lookback_days"]).reindex(df.index).ffill()
        out[f"rs_{label}"] = asset_return - other_return
    return out


def market_regime(btc_df: pd.DataFrame, cfg: dict) -> pd.Series:
    """BTC trend classification, shared market context for every ticker (doc §16) --
    'bullish'/'bearish'/'neutral' per bar, computed once from BTC and broadcast by the caller."""
    trend = compute_trend(btc_df, cfg)
    bullish = trend["price_vs_ema_medium"] > 0
    bearish = trend["price_vs_ema_medium"] < -0.05  # >5% below EMA50 -- meaningfully bearish, not noise
    regime = pd.Series("neutral", index=btc_df.index)
    regime[bullish] = "bullish"
    regime[bearish] = "bearish"
    return regime


def compute_features(df: pd.DataFrame, btc_df: pd.DataFrame | None, eth_df: pd.DataFrame | None,
                      config: dict) -> pd.DataFrame:
    """All per-bar features for one ticker, concatenated into a single frame aligned to df's
    index."""
    parts = [
        compute_trend(df, config["trend"]),
        compute_volatility(df, config["volatility"]),
        compute_volume(df, config["volume"]),
        compute_momentum(df, config["momentum"]),
        compute_relative_strength(df, btc_df, eth_df, config["relative_strength"]),
    ]
    return pd.concat(parts, axis=1)

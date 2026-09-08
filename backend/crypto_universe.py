"""Static crypto watchlist, curated from backtesting (2021-2026 Yahoo daily bars) -- see
docs' grid search maximizing count of assets passing a >=20 trades, PF>=2.5, WR>=75% gate on
strategy_vcp.py's run(). No dynamic screener (unlike build_universe.py's Yahoo EquityQuery) --
these two config buckets are the validated result, not a filter to be re-derived at runtime.
"""

# risk_pct is scaled up from strategy_vcp.py's 1.0 default so that a $1500 account's ATR-based
# stop distance produces a ~$500 median position, instead of the $20-100 positions 1.0 gives on
# crypto's wider stops -- measured empirically per bucket (large-cap's tighter ATR needs ~8x,
# meme's wider ATR needs ~20x to reach the same $500 target).
LARGE_CAP_CONFIG = dict(atr_mult=3.0, be_trigger_pct=10.0, trail_tier_pct=10.0,
                         tp_target_pct=8.0, vol_mult=1.6, max_bars=20, risk_pct=7.8)
LARGE_CAP_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "DOGE-USD"]

MEME_CONFIG = dict(atr_mult=4.0, be_trigger_pct=10.0, trail_tier_pct=10.0,
                    tp_target_pct=10.0, vol_mult=1.2, max_bars=15, risk_pct=20.0)
MEME_TICKERS = ["FLOKI-USD", "MEME-USD", "TURBO-USD", "WOJAK-USD"]

BUCKETS = [
    (LARGE_CAP_TICKERS, LARGE_CAP_CONFIG),
    (MEME_TICKERS, MEME_CONFIG),
]

ALL_TICKERS = LARGE_CAP_TICKERS + MEME_TICKERS

# ticker -> its bucket's run() kwargs, for a per-ticker lookup at compute time.
CONFIG_BY_TICKER = {tk: cfg for tickers, cfg in BUCKETS for tk in tickers}

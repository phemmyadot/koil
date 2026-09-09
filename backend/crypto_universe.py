"""Dynamic crypto watchlist, mirroring build_universe.py's two-stage pattern:
fetch_candidates() screens via yfinance's own crypto screener (no CoinGecko, no cross-platform
symbol mapping -- yf.screen() already returns yfinance-native tickers), then
passes_technical_filters() checks each candidate's bars against a loose VCP regime match (see
strategy_vcp.py, the only strategy the crypto side runs) using that bucket's own tuned config
(LARGE_CAP_CONFIG/MEME_CONFIG below).

The two static lists (FALLBACK_LARGE_CAP_TICKERS/FALLBACK_MEME_TICKERS) were validated via an
offline grid search (2021-2026 Yahoo daily bars) against a >=20 trades, PF>=2.5, WR>=75% gate on
strategy_vcp.py's run() -- they're kept as a fallback only, used when the yfinance screener call
fails, not as the primary path.
"""
import os

import pandas as pd
import yfinance as yf

import backend.build_universe as build_universe

# risk_pct is scaled up from strategy_vcp.py's 1.0 default so that a $1500 account's ATR-based
# stop distance produces a ~$500 median position, instead of the $20-100 positions 1.0 gives on
# crypto's wider stops -- measured empirically per bucket (large-cap's tighter ATR needs ~8x,
# meme's wider ATR needs ~20x to reach the same $500 target).
LARGE_CAP_CONFIG = dict(atr_mult=3.0, be_trigger_pct=10.0, trail_tier_pct=10.0,
                         tp_target_pct=8.0, vol_mult=1.6, max_bars=20, risk_pct=7.8)
MEME_CONFIG = dict(atr_mult=4.0, be_trigger_pct=10.0, trail_tier_pct=10.0,
                    tp_target_pct=10.0, vol_mult=1.2, max_bars=15, risk_pct=20.0)

FALLBACK_LARGE_CAP_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "DOGE-USD"]
FALLBACK_MEME_TICKERS = ["FLOKI-USD", "MEME-USD", "TURBO-USD", "WOJAK-USD"]

DEFAULT_LARGE_CAP_COUNT = int(os.environ.get("CRYPTO_UNIVERSE_LARGE_CAP_COUNT", 20))

# Safety-net floor only, not the primary meme filter -- the 250-ticker pool's tail already clears
# this comfortably (rank 250 sits around $148M), it just guards against future pool composition
# drift rather than doing real filtering work today.
MEME_MARKET_CAP_FLOOR = 100_000_000

# Re-discovering the universe every CRYPTO_CHECK_INTERVAL (30 min, data_crypto.py) is unnecessary
# churn even without CoinGecko's old rate limit in the picture -- the top of the market-cap
# rankings doesn't meaningfully change that often, and the only rate limit left (Yahoo's) is the
# same one data.py/data_crypto.py already tolerate. Left at 12h rather than shortened.
DISCOVERY_INTERVAL_SECONDS = int(os.environ.get("CRYPTO_DISCOVERY_INTERVAL_HOURS", 12)) * 3600

# Stablecoins, gold-backed tokens, and wrapped/staked/liquid-staking tokens rank high by market
# cap but have no independent price action a breakout strategy could trade (stablecoins don't
# move; wrapped/staked tokens just track their underlying 1:1 or via a slow-changing rate) -- so
# they're excluded at discovery time. No clean "isStablecoin"/"isWrapped" flag exists in the
# screener response, so this is a static list matched against the base symbol (before "-USD");
# expect to update it occasionally as new wrapped/stablecoin products appear.
_EXCLUDED_SYMBOLS = {
    # stablecoins (incl. gold-backed PAXG, XAUT -- no independent price action either)
    "USDT", "USDC", "DAI", "USDS", "USDE", "PYUSD", "XAUT", "RLUSD", "USDY", "BFUSD",
    "USDD", "SUSDE", "USDF", "USDCE", "USDG", "USDGO", "PAXG", "FDUSD", "TUSD", "GUSD",
    "USDP", "USD1",
    # wrapped / staked / liquid-staking tokens (track an underlying 1:1 or via a slow rate)
    "WBTC", "WETH", "WBNB", "STETH", "WSTETH", "WEETH", "RETH", "CBBTC", "WBETH",
    "BNSOL", "JITOSOL", "LBTC", "BTCB", "RSETH", "AETHWETH", "AETHUSDT", "KHYPE",
}


def _is_excluded(symbol: str) -> bool:
    # Yahoo disambiguates ticker collisions by appending digits to the base symbol (e.g.
    # "USDT038517-USD", "HYPE32196-USD") -- strip a trailing digit run before matching so those
    # still hit the exclusion set instead of slipping through as an unrecognized "new" coin.
    base = symbol.upper().removesuffix("-USD").rstrip("0123456789")
    return base in _EXCLUDED_SYMBOLS or base.startswith("W") and base[1:] in _EXCLUDED_SYMBOLS


def fetch_candidates(large_cap_count: int = DEFAULT_LARGE_CAP_COUNT) -> dict[str, list[str]]:
    """Screens yfinance's 'all_cryptocurrencies_us' predefined screener (up to Yahoo's 250-result
    cap per call -- there's no working pagination for this screener name in this yfinance
    version, and passing offset= raises KeyError since it's not in yfinance's local
    PREDEFINED_SCREENER_QUERIES dict, only proxied straight through to Yahoo). sortField is
    required -- without it results aren't reliably market-cap-ordered. Returns yfinance-native
    tickers per bucket (no cross-platform mapping needed): top `large_cap_count` by market cap
    (after exclusions) is the large-cap bucket. The meme bucket ranks its remainder (after a
    market-cap floor) by 24h volume-spike ratio instead of market cap -- attention/momentum is
    the relevant signal for meme relevance, not slow-moving cap rank."""
    res = yf.screen("all_cryptocurrencies_us", count=250, sortField="intradaymarketcap", sortAsc=False)
    quotes = [q for q in res.get("quotes", []) if not _is_excluded(q["symbol"])]
    quotes.sort(key=lambda q: q.get("marketCap") or 0, reverse=True)
    symbols = [q["symbol"] for q in quotes]
    large_cap = symbols[:large_cap_count]

    large_cap_set = set(large_cap)
    meme_pool = [q for q in quotes if q["symbol"] not in large_cap_set
                 and (q.get("marketCap") or 0) >= MEME_MARKET_CAP_FLOOR]

    spikes = []
    for q in meme_pool:
        current_vol = q.get("regularMarketVolume") or q.get("volume24Hr")
        avg_vol = q.get("averageDailyVolume3Month")
        if not current_vol or not avg_vol:  # can't rank without both -- skip rather than guess
            continue
        spikes.append((q["symbol"], current_vol / avg_vol))
    spikes.sort(key=lambda pair: pair[1], reverse=True)
    meme = [symbol for symbol, _ in spikes]

    return {"large_cap": large_cap, "meme": meme}


def matches_vcp_setup(df: pd.DataFrame, atr_mult: float, mode: str = "or") -> bool:
    """Loose VCP regime match for a crypto candidate, adapted from build_universe.py's
    _matches_vcp_family_setup (generic pandas, not equity-specific): ATR(22) compression vs its
    100-bar average OR price above EMA(50), not the exact same-day breakout trigger (fresh cross
    above the prior 20-bar high with volume confirmation is rare on any given day). Uses this
    bucket's own tuned atr_mult (LARGE_CAP_CONFIG/MEME_CONFIG's atr_mult) as the compression
    threshold, since the two buckets' volatility regimes were tuned independently."""
    c, h, l = df["Close"].dropna(), df["High"], df["Low"]
    if len(c) < 130:
        return False
    atr = build_universe._wilder_atr(h, l, c, 22)
    atr_avg = atr.rolling(100).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    if pd.isna(atr_avg.iloc[-1]):
        return False
    compressed = atr.iloc[-1] <= atr_avg.iloc[-1] * atr_mult
    macro_bullish = c.iloc[-1] > ema50.iloc[-1]
    return (compressed and macro_bullish) if mode == "and" else (compressed or macro_bullish)


def passes_technical_filters(df: pd.DataFrame, config: dict) -> bool:
    return matches_vcp_setup(df, atr_mult=config["atr_mult"])


BUCKET_CONFIGS = {"large_cap": LARGE_CAP_CONFIG, "meme": MEME_CONFIG}
FALLBACK_TICKERS = {"large_cap": FALLBACK_LARGE_CAP_TICKERS, "meme": FALLBACK_MEME_TICKERS}

# Populated by app.py's crypto refresh cycle (discovery -> DB), read here for ALL_TICKERS/
# CONFIG_BY_TICKER -- starts from the fallback lists so the app has a usable universe before the
# first discovery cycle completes (e.g. right after startup).
_active_tickers: dict[str, list[str]] = dict(FALLBACK_TICKERS)


def set_active_tickers(bucket: str, tickers: list[str]) -> None:
    global _active_tickers
    _active_tickers[bucket] = tickers if tickers else FALLBACK_TICKERS[bucket]


def get_all_tickers() -> list[str]:
    return _active_tickers["large_cap"] + _active_tickers["meme"]


def get_config_by_ticker() -> dict[str, dict]:
    return {tk: LARGE_CAP_CONFIG for tk in _active_tickers["large_cap"]} | \
           {tk: MEME_CONFIG for tk in _active_tickers["meme"]}


# Backwards-compatible module-level views (read by strategy_crypto.py, app.py) -- functions
# above, kept as properties via a tiny module __getattr__ so existing `crypto_universe.ALL_TICKERS`/
# `crypto_universe.CONFIG_BY_TICKER` call sites don't need to change to function calls.
def __getattr__(name):
    if name == "ALL_TICKERS":
        return get_all_tickers()
    if name == "CONFIG_BY_TICKER":
        return get_config_by_ticker()
    raise AttributeError(name)

"""Dynamic crypto watchlist, mirroring build_universe.py's two-stage pattern:
fetch_candidates() screens via yfinance's own crypto screener (no CoinGecko, no cross-platform
symbol mapping -- yf.screen() already returns yfinance-native tickers), then
passes_technical_filters() checks each candidate's bars against a loose VCP regime match (see
strategy_vcp.py, the only strategy the crypto side runs) using the single shared CONFIG below.

The two static lists (FALLBACK_LARGE_CAP_TICKERS/FALLBACK_MEME_TICKERS) were validated via an
offline grid search (2021-2026 Yahoo daily bars) against a >=20 trades, PF>=2.5, WR>=75% gate on
strategy_vcp.py's run() -- they're kept as a fallback only, used when the yfinance screener call
fails, not as the primary path.
"""
import os
import time

import pandas as pd
import yfinance as yf

import backend.build_universe as build_universe

# Historical note: through 2026-09-21 this was split into LARGE_CAP_CONFIG/MEME_CONFIG (two
# tuned presets, applied per ticker by top-20-market-cap rank). That split diverged from how
# pines/vcp_crypto.pine is actually used on TradingView: the chart's inputs are a single set of
# values typed in once, applied to whichever ticker is on screen -- not swapped per bucket. That
# mismatch surfaced concretely 2026-09-21 validating SEI-USD/FET-USD: both are meme-bucket by
# market-cap rank (not top 20), so the old split ran them through MEME_CONFIG (SEI-USD: 17
# trades/70.6% WR/PF 1.17; FET-USD: 34 trades/76.5% WR/PF 2.34) while the TradingView chart -- left
# on its default inputs, i.e. the old LARGE_CAP_CONFIG values -- gave completely different numbers
# for both (SEI-USD 11/13, PF 5.478; FET-USD 18/24, PF 3.085). Re-running both tickers through the
# old LARGE_CAP_CONFIG values in this same Python harness reproduced TradingView's trade count and
# win rate exactly (SEI-USD 13 trades/84.6% WR/PF 4.89; FET-USD 24 trades/75.0% WR/PF 3.19 -- the
# remaining few-percent PF gap is Yahoo- vs Coinbase-sourced bars, not a logic difference), so the
# single-config model below is what the chart is actually validated against, not the bucket split.
# risk_pct is scaled up from strategy_vcp.py's 1.0 default so that a $1500 account's ATR-based
# stop distance produces a ~$500 median position instead of the $20-100 positions 1.0 gives on
# crypto's wider stops -- measured empirically (~8x); this only affects the backtest engine's own
# $ figures (win rate/PF/entry-exit timing are unaffected either way, see strategy_crypto.py's
# docstring), real trade sizing uses the separate $500-fixed-notional model, not this risk_pct.
#
# atr_mult/tp_target_pct re-tuned to 6.9/15.0 2026-09-21 (pines/vcp_crypto.pine commit edfa88f
# "updated pine") per the SEI-USD/FET-USD validation above. NOTE: atr_mult is dual-purpose --
# passes_technical_filters() below also uses this same value as the compression threshold for
# discovery screening (atr <= atr_avg*atr_mult), so widening it to 6.9 loosens that filter too
# (nearly anything qualifies as "compressed" at 6.9x its own 100-bar average), not just the stop
# distance/position size it was tuned for.
#
# The large_cap/meme ticker-list split (below, FALLBACK_*/fetch_candidates) still exists for
# discovery/UI grouping, but both buckets now run this same CONFIG -- there is no longer a
# per-bucket parameter difference.
CONFIG = dict(atr_mult=6.9, be_trigger_pct=10.0, trail_tier_pct=18.0,
              tp_target_pct=15.0, vol_mult=1.6, max_bars=15, risk_pct=7.8)

FALLBACK_LARGE_CAP_TICKERS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "DOGE-USD"]
FALLBACK_MEME_TICKERS = ["FLOKI-USD", "MEME-USD", "TURBO-USD", "WOJAK-USD"]

DEFAULT_LARGE_CAP_COUNT = int(os.environ.get("CRYPTO_UNIVERSE_LARGE_CAP_COUNT", 20))

# Lowered from $100M 2026-09-19 to widen the meme pool (see _SORT_FIELDS below for the discovery-
# breadth half of that same change): at $100M this was the dominant filter, cutting ~1000 raw
# discovered candidates down to ~215. $5M keeps it a real filter (not just a safety net) rather
# than removing it outright -- still screens out true dust/dead tokens, while admitting several
# hundred more real (if more illiquid) candidates. Chosen over $0/no-floor: those thinnest tokens
# are the ones where yfinance/Coinbase candle data quality and actual tradability (spread,
# slippage) get shakiest, and the backtest can't see either. Overridable via env var (same
# pattern as DEFAULT_LARGE_CAP_COUNT above) so this risk/breadth tradeoff can be tuned without a
# code change -- also read directly by crypto_v2/universe.py's discover(), which applies the same
# floor to its own (larger, 4-tier) candidate pool.
MEME_MARKET_CAP_FLOOR = int(os.environ.get("CRYPTO_MEME_MARKET_CAP_FLOOR", 5_000_000))

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


# The 250-per-call cap has no working offset-pagination for this screener (not in yfinance's
# PREDEFINED_SCREENER_QUERIES, so offset= raises KeyError; custom EquityQuery is hardcoded to
# quoteType=EQUITY internally, so it can't target crypto either; and a direct offset= probe
# against Yahoo's raw endpoint confirmed the server itself ignores offset here too -- it's not
# just this yfinance version's limitation). Varying sortField/sortAsc instead gives 16 different
# top-250 slices to dedupe across -- these 8 fields (lastmarket/currency added 2026-09-19) are the
# only ones confirmed valid for this screener via direct probing (others return HTTP 400).
_SORT_FIELDS = ["intradaymarketcap", "dayvolume", "percentchange", "circulatingsupply", "ticker",
                "fromcurrency", "lastmarket", "currency"]


def _fetch_all_pages() -> list[dict]:
    seen: dict[str, dict] = {}
    for sort_field in _SORT_FIELDS:
        for sort_asc in (False, True):
            try:
                res = yf.screen("all_cryptocurrencies_us", count=250, sortField=sort_field, sortAsc=sort_asc)
            except Exception as exc:
                print(f"crypto_universe: screen call failed (sortField={sort_field}, sortAsc={sort_asc}): {exc}")
                continue
            for q in res.get("quotes", []):
                seen.setdefault(q["symbol"], q)
            time.sleep(0.3)  # mirrors build_universe.py's pagination pacing
    return list(seen.values())


# Populated as a side effect of the discovery pass in fetch_candidates() -- lastMarket (which
# venue yfinance attributed the ticker's most recent trade to) is already present on the same
# quote objects _fetch_all_pages() pulls, so this avoids a second per-ticker API call just to
# capture it. app.py's discovery orchestration reads this after calling fetch_candidates().
_last_market_by_symbol: dict[str, str] = {}


def last_market_by_symbol() -> dict[str, str]:
    return dict(_last_market_by_symbol)


def _fetch_candidate_quotes() -> list[dict]:
    """Shared first stage for both fetch_candidates() below and crypto_v2/universe.py's
    discover() -- one _fetch_all_pages() call, the exclusion filter, and the
    _last_market_by_symbol side effect, so both discovery paths start from literally the same
    raw candidate set. Split out 2026-09-22: v1 previously ranked its meme bucket by a 24h
    volume-spike ratio that silently DROPPED any candidate missing averageDailyVolume3Month
    (common on thinner/newer names) -- v2's discover() never applied that filter, which is why
    its pool ended up meaningfully larger from the exact same screener call, and (per live
    comparison) produced better setups. fetch_candidates() below now pools the same way v2
    does: floor + market-cap sort, no volume-spike re-ranking."""
    global _last_market_by_symbol
    quotes = [q for q in _fetch_all_pages() if not _is_excluded(q["symbol"])]
    _last_market_by_symbol = {q["symbol"]: q["lastMarket"] for q in quotes if q.get("lastMarket")}
    return quotes


def fetch_candidates(large_cap_count: int = DEFAULT_LARGE_CAP_COUNT) -> dict[str, list[str]]:
    """Screens yfinance's 'all_cryptocurrencies_us' predefined screener across 12 sort-order
    combinations (see _fetch_all_pages) and dedupes by symbol, since a single call is capped at
    Yahoo's 250-result limit for this screener. Returns yfinance-native tickers per bucket (no
    cross-platform mapping needed): top `large_cap_count` by market cap (after exclusions) is the
    large-cap bucket; meme is everyone else above MEME_MARKET_CAP_FLOOR, same market-cap-sorted
    pool crypto_v2/universe.py's discover() classifies into its own 4 tiers (see
    _fetch_candidate_quotes)."""
    quotes = _fetch_candidate_quotes()
    floored = [q for q in quotes if (q.get("marketCap") or 0) >= MEME_MARKET_CAP_FLOOR]
    floored.sort(key=lambda q: q.get("marketCap") or 0, reverse=True)
    symbols = [q["symbol"] for q in floored]
    large_cap = symbols[:large_cap_count]
    meme = symbols[large_cap_count:]

    return {"large_cap": large_cap, "meme": meme}


def matches_vcp_setup(df: pd.DataFrame, atr_mult: float, mode: str = "or") -> bool:
    """Loose VCP regime match for a crypto candidate, adapted from build_universe.py's
    _matches_vcp_family_setup (generic pandas, not equity-specific): ATR(22) compression vs its
    100-bar average OR price above EMA(50), not the exact same-day breakout trigger (fresh cross
    above the prior 20-bar high with volume confirmation is rare on any given day). Uses the
    shared CONFIG's atr_mult as the compression threshold, same value strategy_vcp.run() uses
    for stop distance/position sizing."""
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


BUCKET_CONFIGS = {"large_cap": CONFIG, "meme": CONFIG}
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
    return {tk: CONFIG for tk in get_all_tickers()}


# Backwards-compatible module-level views (read by strategy_crypto.py, app.py) -- functions
# above, kept as properties via a tiny module __getattr__ so existing `crypto_universe.ALL_TICKERS`/
# `crypto_universe.CONFIG_BY_TICKER` call sites don't need to change to function calls.
def __getattr__(name):
    if name == "ALL_TICKERS":
        return get_all_tickers()
    if name == "CONFIG_BY_TICKER":
        return get_config_by_ticker()
    raise AttributeError(name)

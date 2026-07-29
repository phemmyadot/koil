"""
Universe candidate screening.

Server-side filter (fetch_candidates) narrows to cap/volume/price/exchange
candidates via Yahoo's equity screener API -- symbols only, no price
history. passes_technical_filters() then checks whether a candidate's
latest close matches one of the three dashboard strategies' actual entry
conditions (VEXH, VCP, or VCPO -- see matches_vexh_setup/matches_vcp_setup/
matches_vcpo_setup), instead of a generic trend/volatility stand-in
unrelated to what the strategies look for. app.py calls this against bars
already fetched into the DB by the normal per-ticker fetch path -- there's
no separate download here.

Screening criteria default from the constants below but can be overridden via
env vars (BUILD_UNIVERSE_MIN_CAP, BUILD_UNIVERSE_MIN_VOL, BUILD_UNIVERSE_PRICE_MIN,
BUILD_UNIVERSE_PRICE_MAX, BUILD_UNIVERSE_EXCHANGES, BUILD_UNIVERSE_ALLOW_OTC)
so criteria changes don't require a code push -- just set the env var.
"""
import os
import time

import pandas as pd
import yfinance as yf


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a .env file at the project root, if present.

    No third-party dependency -- keeps existing os.environ values as-is.
    """
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Major US listing venues only -- excludes OTC/Pink Sheets (exchange code "PNK"),
# which are essentially never optionable and carry outsized pump-and-dump risk,
# a bigger concern now that the cap floor can go as low as $100M.
# Override with BUILD_UNIVERSE_EXCHANGES (comma-separated) if needed.
_DEFAULT_EXCHANGES = ["NMS", "NYQ", "NCM", "NGM", "ASE", "PCX"]
MAJOR_EXCHANGES = (
    [e.strip() for e in os.environ["BUILD_UNIVERSE_EXCHANGES"].split(",") if e.strip()]
    if os.environ.get("BUILD_UNIVERSE_EXCHANGES")
    else _DEFAULT_EXCHANGES
)

DEFAULT_MIN_CAP = int(os.environ.get("BUILD_UNIVERSE_MIN_CAP", 300_000_000))
DEFAULT_MIN_VOL = int(os.environ.get("BUILD_UNIVERSE_MIN_VOL", 500_000))
DEFAULT_PRICE_MIN = int(os.environ.get("BUILD_UNIVERSE_PRICE_MIN", 5))
DEFAULT_PRICE_MAX = int(os.environ.get("BUILD_UNIVERSE_PRICE_MAX", 50))
DEFAULT_ALLOW_OTC = _env_bool("BUILD_UNIVERSE_ALLOW_OTC")

# Combine each strategy's two regime conditions with "or" (looser, lets more
# candidates through) or "and" (stricter, both must hold). Override via
# BUILD_UNIVERSE_MATCH_MODE.
DEFAULT_MATCH_MODE = os.environ.get("BUILD_UNIVERSE_MATCH_MODE", "or").strip().lower()
if DEFAULT_MATCH_MODE not in ("and", "or"):
    raise ValueError(f"BUILD_UNIVERSE_MATCH_MODE must be 'and' or 'or', got {DEFAULT_MATCH_MODE!r}")


def _combine(a: bool, b: bool, mode: str) -> bool:
    return (a and b) if mode == "and" else (a or b)


def fetch_candidates(min_cap: int = DEFAULT_MIN_CAP, min_vol: int = DEFAULT_MIN_VOL,
                      price_range: tuple[int, int] = (DEFAULT_PRICE_MIN, DEFAULT_PRICE_MAX),
                      exchanges: list[str] | None = MAJOR_EXCHANGES) -> list[str]:
    """Server-side filter: cap/volume/price/exchange. Returns matching symbols."""
    filters = [
        yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
        yf.EquityQuery("gt", ["avgdailyvol3m", min_vol]),
        yf.EquityQuery("btwn", ["intradayprice", price_range[0], price_range[1]]),
        yf.EquityQuery("eq", ["region", "us"]),
    ]
    if exchanges:
        filters.append(yf.EquityQuery("is-in", ["exchange", *exchanges]))
    query = yf.EquityQuery("and", filters)
    symbols, offset, size, total = [], 0, 250, None
    while True:
        res = yf.screen(query, size=size, offset=offset, sortField="ticker", sortAsc=True)
        quotes = res.get("quotes", [])
        if not quotes:
            break
        symbols.extend(q["symbol"] for q in quotes)
        total = res.get("total")
        offset += size
        if offset >= total:
            break
        time.sleep(0.3)
    return symbols


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    return 100 - (100 / (1 + gain / (loss + 1e-9)))


def matches_vexh_setup(df: pd.DataFrame, mode: str = DEFAULT_MATCH_MODE) -> bool:
    """Regime match for strategy_d_volatility_exhaustion.pine's setup, not
    the exact same-day entry trigger (requiring all 6 gates at once is rare
    on any given day across any universe). Combines a bullish macro trend
    (close > SMA150) and a washed-out RSI(14) <= 40 via `mode` ("or" --
    either one alone is enough, looser; "and" -- both required, stricter).
    Drops the narrower same-bar BB-cross, SMA-distance, and ATR-band gates
    entirely either way."""
    c = df["Close"].dropna()
    if len(c) < 160:
        return False
    sma150 = c.rolling(150).mean()
    rsi = _rsi(c, 14)
    if pd.isna(sma150.iloc[-1]) or pd.isna(rsi.iloc[-1]):
        return False
    macro_bullish = c.iloc[-1] > sma150.iloc[-1]
    rsi_washed_out = rsi.iloc[-1] <= 40
    return _combine(macro_bullish, rsi_washed_out, mode)


def _matches_vcp_family_setup(df: pd.DataFrame, mode: str) -> bool:
    """Regime match for strategy_vcp.py/strategy_vcpo.py's setup, not the
    exact same-day breakout trigger (requiring a fresh cross above the prior
    20-bar high, same-bar, is rare on any given day). Combines ATR(22)
    compression vs its 100-bar average and price above EMA(50) via `mode`
    ("or" -- either one alone is enough, looser; "and" -- both required,
    stricter). Drops the narrower same-bar resistance-cross and
    volume-confirmation gates entirely either way."""
    c, h, l = df["Close"].dropna(), df["High"], df["Low"]
    if len(c) < 130:
        return False
    atr = _wilder_atr(h, l, c, 22)
    atr_avg = atr.rolling(100).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    if pd.isna(atr_avg.iloc[-1]):
        return False
    compressed = atr.iloc[-1] <= atr_avg.iloc[-1] * 1.15
    macro_bullish = c.iloc[-1] > ema50.iloc[-1]
    return _combine(compressed, macro_bullish, mode)


def matches_vcp_setup(df: pd.DataFrame, mode: str = DEFAULT_MATCH_MODE) -> bool:
    return _matches_vcp_family_setup(df, mode)


def matches_vcpo_setup(df: pd.DataFrame, mode: str = DEFAULT_MATCH_MODE) -> bool:
    return _matches_vcp_family_setup(df, mode)


def passes_technical_filters(df: pd.DataFrame) -> bool:
    """A ticker passes if its latest close matches ANY of the three dashboard strategies'
    actual entry conditions (VEXH, VCP, VCPO), rather than a generic SMA200/trend/
    volatility stand-in unrelated to what the strategies themselves look for."""
    return (matches_vexh_setup(df) or matches_vcp_setup(df) or matches_vcpo_setup(df))

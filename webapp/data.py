"""
Shared raw OHLCV cache, decoupled from computation. One background job fetches
all tickers' price history in bulk; every strategy evaluator reads from this
cache instead of hitting yfinance itself, so a page request never triggers a
network call.

Staleness rule: refetch every 2 hours while the US market is open, and at
most once after each close (to pick up the final EOD candle) -- otherwise the
cache is left alone, so a closed market means zero fetch traffic.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from webapp.tickers import TICKERS

ET = ZoneInfo("America/New_York")
REFRESH_INTERVAL_OPEN = 2 * 60 * 60  # 2 hours while market open
CHECK_INTERVAL = 15 * 60             # how often the background loop wakes to check staleness
FETCH_WORKERS = 30
# Matches the start_date/startDate input default (1 Jan 2022) shared by all
# three pine strategies (vcp.pine, vcpo.pine, strategy_d_volatility_
# exhaustion.pine) -- keeps the app's backtest window aligned with the Pine
# reference point instead of an arbitrary rolling window.
HISTORY_START = "2022-01-01"

_raw_cache: dict[str, pd.DataFrame] = {}
_raw_errors: dict[str, str] = {}
_last_fetch_time: float | None = None
_lock = threading.Lock()


def market_is_open(now: datetime | None = None) -> bool:
    """US equity market hours, Mon-Fri 9:30-16:00 ET.
    Known simplification: does not account for market holidays."""
    now = (now or datetime.now(ET)).astimezone(ET)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now <= close_t


def _most_recent_close(now: datetime | None = None) -> datetime:
    """The most recent weekday 16:00 ET that is <= now."""
    now = (now or datetime.now(ET)).astimezone(ET)
    d = now
    for _ in range(10):  # a week+ of lookback is always enough
        candidate = d.replace(hour=16, minute=0, second=0, microsecond=0)
        if d.weekday() < 5 and candidate <= now:
            return candidate
        d = d - timedelta(days=1)
    raise RuntimeError("could not find a recent market close")


def is_stale() -> bool:
    if _last_fetch_time is None:
        return True
    now = time.time()
    if market_is_open():
        return now - _last_fetch_time > REFRESH_INTERVAL_OPEN
    last_fetch_dt = datetime.fromtimestamp(_last_fetch_time, ET)
    return last_fetch_dt < _most_recent_close()


FETCH_TIMEOUT = 20  # seconds -- one hung ticker must not block the whole bulk fetch


def _fetch_one(ticker: str) -> tuple[str, pd.DataFrame | None, str | None]:
    try:
        df = yf.download(ticker, start=HISTORY_START, interval="1d", progress=False,
                          auto_adjust=False, timeout=FETCH_TIMEOUT)
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        df = df.drop(columns=["Adj Close"], errors="ignore").dropna()
        if len(df) < 250:
            return ticker, None, "insufficient history"
        return ticker, df, None
    except Exception as e:  # noqa: BLE001 - one bad ticker shouldn't kill the bulk fetch
        return ticker, None, str(e) or type(e).__name__


def warm_cache(tickers: list[str] | None = None) -> None:
    """Bulk-fetch raw OHLCV for every ticker. Blocking; run this off the
    request path (startup + background refresher only)."""
    global _last_fetch_time
    tickers = tickers or TICKERS
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(_fetch_one, tickers))
    with _lock:
        for tk, df, err in results:
            if df is not None:
                _raw_cache[tk] = df
                _raw_errors.pop(tk, None)
            else:
                _raw_errors[tk] = err
        _last_fetch_time = time.time()


def get_bars(ticker: str) -> pd.DataFrame | None:
    return _raw_cache.get(ticker)


def get_error(ticker: str) -> str | None:
    return _raw_errors.get(ticker)


def last_fetch_time() -> float | None:
    return _last_fetch_time


def start_background_refresher() -> None:
    """Daemon thread: sleeps, checks the staleness rule, refetches everything
    if stale. No-ops (no network calls) whenever the market is closed and
    we've already captured that day's close."""
    def loop():
        while True:
            time.sleep(CHECK_INTERVAL)
            if is_stale():
                warm_cache()
    threading.Thread(target=loop, daemon=True).start()

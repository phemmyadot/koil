"""Crypto's own raw OHLCV cache, parallel to data.py but always-on -- crypto trades 24/7, so
this has no market-hours gating (see market_hours.py's docstring: equity-only, not reusable
here). Persists via db.py's crypto_bars/crypto_fetch_meta tables, never the equity bars table.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

import backend.db as db

# Always-on cadence -- no market-hours check, crypto trades every day. Separate from equity's
# CHECK_INTERVAL so tuning one never affects the other.
CRYPTO_CHECK_INTERVAL = 30 * 60
FETCH_WORKERS = 9  # only 9 tickers total, no need for equity's 30-worker pool

_fetch_executor = ThreadPoolExecutor(max_workers=FETCH_WORKERS)

HISTORY_START = "2021-01-01"  # matches data.py's own warm-up window for ATR/EMA lookbacks

_raw_cache: dict[str, pd.DataFrame] = {}
_raw_errors: dict[str, str] = {}
_fetched_at: dict[str, float] = {}
_last_fetch_time: float | None = None
_lock = threading.Lock()

_fetch_progress: dict[str, int] | None = None


def fetch_progress() -> dict[str, int] | None:
    with _lock:
        return dict(_fetch_progress) if _fetch_progress is not None else None


def _load_from_db() -> None:
    global _raw_cache, _fetched_at, _raw_errors
    try:
        _raw_cache = db.crypto_load_all_bars()
        _fetched_at, _raw_errors = db.crypto_load_all_fetch_meta()
    except Exception as e:  # noqa: BLE001 - corrupted DB, not a crash
        print(f"data_crypto: loading price data from db failed ({e}); starting cold.")
        _raw_cache, _fetched_at, _raw_errors = {}, {}, {}


_load_from_db()

FETCH_TIMEOUT = 20

_RATE_LIMITED = "__rate_limited__"


def _fetch_one(ticker: str, force: bool) -> tuple[str, pd.DataFrame | None, str | None]:
    last_bar_date = None if force else db.crypto_get_last_bar_date(ticker)
    if last_bar_date:
        try:
            last_bar_ts = pd.Timestamp(last_bar_date)
        except (ValueError, TypeError):
            last_bar_date = None
        else:
            if last_bar_ts.date() >= pd.Timestamp.now().date():
                start = last_bar_ts.strftime("%Y-%m-%d")
            else:
                start = (last_bar_ts + timedelta(days=1)).strftime("%Y-%m-%d")
    if not last_bar_date:
        start = HISTORY_START
    try:
        df = yf.download(ticker, start=start, interval="1d", progress=False,
                          auto_adjust=False, timeout=FETCH_TIMEOUT)
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        df = df.drop(columns=["Adj Close"], errors="ignore").dropna()
        if df.empty:
            if last_bar_date:
                return ticker, df, None
            return ticker, None, "insufficient history"
        return ticker, df, None
    except YFRateLimitError:
        return ticker, None, _RATE_LIMITED
    except Exception as e:  # noqa: BLE001 - one bad ticker shouldn't kill the bulk fetch
        return ticker, None, str(e) or type(e).__name__


def warm_cache(tickers: list[str], force: bool = False) -> None:
    """No whole-batch early-exit check like data.py's -- only 9 tickers, always-on cadence, the
    background loop itself already controls how often this gets called (CRYPTO_CHECK_INTERVAL
    sleep between wakes), so a second staleness gate here would be redundant."""
    global _last_fetch_time, _fetch_progress
    now = time.time()

    with _lock:
        _fetch_progress = {"done": 0, "total": len(tickers)}
    rate_limited = False
    try:
        futures = {_fetch_executor.submit(_fetch_one, tk, force): tk for tk in tickers}
        for future in as_completed(futures):
            tk, df, err = future.result()
            if err == _RATE_LIMITED:
                rate_limited = True
                for f in futures:
                    f.cancel()
                break
            with _lock:
                if df is not None:
                    db.crypto_upsert_bars(tk, df, now)
                    if force or tk not in _raw_cache or df.empty:
                        if not df.empty:
                            _raw_cache[tk] = df
                    else:
                        _raw_cache[tk] = pd.concat([_raw_cache[tk], df])
                        _raw_cache[tk] = _raw_cache[tk][~_raw_cache[tk].index.duplicated(keep="last")]
                    _fetched_at[tk] = now
                    _raw_errors.pop(tk, None)
                else:
                    db.crypto_mark_fetch_error(tk, now, err)
                    _raw_errors[tk] = err
                _fetch_progress["done"] += 1
    finally:
        with _lock:
            _fetch_progress = None
    if rate_limited:
        print("data_crypto: warm_cache stopped early -- Yahoo rate-limited this batch; "
              "remaining tickers will retry on the next scheduled refresh.")

    with _lock:
        _last_fetch_time = now


def get_bars(ticker: str) -> pd.DataFrame | None:
    return _raw_cache.get(ticker)


def get_error(ticker: str) -> str | None:
    return _raw_errors.get(ticker)


def last_fetch_time() -> float | None:
    return _last_fetch_time

"""Crypto's own raw OHLCV cache, parallel to data.py but always-on -- crypto trades 24/7, so
this has no market-hours gating (see market_hours.py's docstring: equity-only, not reusable
here). Persists via db.py's crypto_bars/crypto_fetch_meta tables, never the equity bars table.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

import backend.db as db

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product_id}/candles"
PAGE_DAYS = 250  # under Coinbase's 300-candle-per-request cap
MAX_EMPTY_PAGES = 3  # consecutive empty windows means we've passed the real listing date
# Coinbase's public (unauthenticated) endpoints are documented at ~3 req/s -- a cold multi-year
# fetch is several paginated requests per ticker, so this delay applies between EVERY page (not
# just per-ticker) to keep the aggregate rate under that regardless of how many workers are
# fetching concurrently.
PAGE_FETCH_DELAY = 0.4

# Always-on cadence -- no market-hours check, crypto trades every day. Separate from equity's
# CHECK_INTERVAL so tuning one never affects the other.
CRYPTO_CHECK_INTERVAL = 30 * 60
# Discovery now finds ~200+ tickers (crypto_universe.py), each needing several paginated Coinbase
# requests on a cold fetch -- kept low (not equity's 30) since Coinbase's public rate limit is far
# stricter than Yahoo's, and PAGE_FETCH_DELAY already paces requests within each worker.
FETCH_WORKERS = 4

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


def _coinbase_product(ticker: str) -> str:
    return ticker if ticker.endswith("-USD") else f"{ticker}-USD"


class _RateLimitedError(Exception):
    pass


class _ProductNotFoundError(Exception):
    pass


def _fetch_candles_page(product_id: str, start: datetime, end: datetime) -> list[list[float]]:
    resp = requests.get(
        COINBASE_CANDLES_URL.format(product_id=product_id),
        params={
            "granularity": 86400,
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        },
        timeout=FETCH_TIMEOUT,
    )
    if resp.status_code == 429:
        # A 429 here is momentary burst throttling on one request, not a Yahoo-style whole-session
        # block (unlike YFRateLimitError, which _RATE_LIMITED/warm_cache's batch-abort was
        # originally modeled on) -- one retry after a short backoff clears it in practice instead
        # of needing to cancel every other ticker's in-flight fetch.
        time.sleep(1.0)
        resp = requests.get(
            COINBASE_CANDLES_URL.format(product_id=product_id),
            params={
                "granularity": 86400,
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
            },
            timeout=FETCH_TIMEOUT,
        )
        if resp.status_code == 429:
            raise _RateLimitedError()
    if resp.status_code == 404:
        # Coinbase doesn't list this product at all (confirmed for e.g. TRX-USD, XMR-USD, LEO-USD,
        # and yfinance's digit-disambiguated symbols like "HYPE32196-USD") -- distinct from an
        # empty-array response (listed, just no data in this window/before the listing date).
        raise _ProductNotFoundError()
    resp.raise_for_status()
    return resp.json()


def _fetch_coinbase_history(ticker: str, start_date: str) -> pd.DataFrame:
    """Paginate backward in bounded windows from today to start_date, stopping early once
    MAX_EMPTY_PAGES consecutive windows come back empty (past the ticker's real listing date)."""
    product_id = _coinbase_product(ticker)
    start_bound = pd.Timestamp(start_date).to_pydatetime().replace(tzinfo=timezone.utc)
    window_end = datetime.now(timezone.utc)
    rows: list[list[float]] = []
    empty_streak = 0
    while window_end > start_bound:
        window_start = max(window_end - timedelta(days=PAGE_DAYS), start_bound)
        page = _fetch_candles_page(product_id, window_start, window_end)
        if page:
            rows.extend(page)
            empty_streak = 0
        else:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_PAGES:
                break
        window_end = window_start
        if window_end > start_bound:
            time.sleep(PAGE_FETCH_DELAY)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "Low", "High", "Open", "Close", "Volume"])
    df["date"] = pd.to_datetime(df["ts"], unit="s").dt.normalize()
    df = df.drop_duplicates(subset="date").set_index("date").sort_index()
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df


def _fetch_yfinance_history(ticker: str, start: str) -> pd.DataFrame:
    """Same fetch/post-processing yfinance path this codebase used before the Coinbase migration
    (see git history) -- reused as-is here so the fallback isn't a slightly different reimplementation."""
    df = yf.download(ticker, start=start, interval="1d", progress=False,
                      auto_adjust=False, timeout=FETCH_TIMEOUT)
    if hasattr(df.columns, "get_level_values"):
        df.columns = df.columns.get_level_values(0)
    return df.drop(columns=["Adj Close"], errors="ignore").dropna()


def _fetch_one(ticker: str, force: bool) -> tuple[str, pd.DataFrame | None, str | None, str | None]:
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
        df = _fetch_coinbase_history(ticker, start)
        if df.empty:
            if last_bar_date:
                return ticker, df, None, "coinbase"
            return ticker, None, "insufficient history", None
        return ticker, df, None, "coinbase"
    except _RateLimitedError:
        return ticker, None, _RATE_LIMITED, None
    except _ProductNotFoundError:
        # Confirmed 404 (not merely empty-in-window) -- Coinbase doesn't list this product at all,
        # so fall back to yfinance instead of dropping the ticker; this is the only failure mode
        # that falls back, since empty-in-window and rate-limits both still mean "ask Coinbase again".
        try:
            df = _fetch_yfinance_history(ticker, start)
            if df.empty:
                if last_bar_date:
                    return ticker, df, None, "yfinance"
                return ticker, None, "insufficient history", None
            return ticker, df, None, "yfinance"
        except Exception as e:  # noqa: BLE001 - one bad ticker shouldn't kill the bulk fetch
            return ticker, None, str(e) or type(e).__name__, None
    except Exception as e:  # noqa: BLE001 - one bad ticker shouldn't kill the bulk fetch
        return ticker, None, str(e) or type(e).__name__, None


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
            tk, df, err, price_source = future.result()
            if err == _RATE_LIMITED:
                rate_limited = True
                for f in futures:
                    f.cancel()
                break
            with _lock:
                if df is not None:
                    db.crypto_upsert_bars(tk, df, now, price_source)
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
        print("data_crypto: warm_cache stopped early -- Coinbase rate-limited this batch; "
              "remaining tickers will retry on the next scheduled refresh.")

    with _lock:
        _last_fetch_time = now


def get_bars(ticker: str) -> pd.DataFrame | None:
    return _raw_cache.get(ticker)


def get_error(ticker: str) -> str | None:
    return _raw_errors.get(ticker)


def last_fetch_time() -> float | None:
    return _last_fetch_time

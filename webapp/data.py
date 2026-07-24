"""
Shared raw OHLCV cache, decoupled from computation. One background job fetches
all tickers' price history in bulk; every strategy evaluator reads from this
cache instead of hitting yfinance itself, so a page request never triggers a
network call.

Staleness rule: refetch every 2 hours while the US market is open, and at
most once after each close (to pick up the final EOD candle) -- otherwise the
cache is left alone, so a closed market means zero fetch traffic.
"""
import os
import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from webapp.tickers import TICKERS

ET = ZoneInfo("America/New_York")
REFRESH_INTERVAL_OPEN = 2 * 60 * 60  # 2 hours while market open
# How often the background loop wakes to check staleness. is_stale() only
# returns True around market open, every REFRESH_INTERVAL_OPEN while open,
# and once right after close -- 30 min is frequent enough to catch each of
# those boundaries promptly without polling every few minutes all day for
# a market that's closed 17+ hours out of 24.
CHECK_INTERVAL = 30 * 60
FETCH_WORKERS = 30

# Persists _raw_cache to disk (gitignored, bind-mount this like tickers.py)
# so a container rebuild doesn't force a full ~2000-ticker re-fetch from
# Yahoo every deploy -- only tickers missing,
# corrupted, or past today's TTL get re-fetched on startup. The manual
# Refresh button (?refresh=1) bypasses this entirely via force=True.
PRICE_CACHE_PATH = os.path.join(os.path.dirname(__file__), "price_cache.pkl")
# Matches the start_date/startDate input default (1 Jan 2022) shared by all
# three pine strategies. Fetches a year earlier than that so ATR (needs
# ATR_LEN=22 + a 100-bar rolling average -- ~122 bars) and VEXH's SMA150 are
# already warmed up by 2022-01-01, instead of computing on cold/incomplete
# indicators for the first several months of the window.
HISTORY_START = "2021-01-01"

_raw_cache: dict[str, pd.DataFrame] = {}
_raw_errors: dict[str, str] = {}
_fetched_at: dict[str, float] = {}  # per-ticker fetch epoch, for the TTL check
_last_fetch_time: float | None = None
_lock = threading.Lock()

# Live progress for the current warm_cache() call, if one is in flight --
# lets the frontend show a real "N of M loaded" bar instead of just a spinner
# while the first post-deploy fetch (which can take several minutes on a
# cold cache) is running. None when no fetch is currently in progress.
_fetch_progress: dict[str, int] | None = None


def fetch_progress() -> dict[str, int] | None:
    with _lock:
        return dict(_fetch_progress) if _fetch_progress is not None else None


def _load_price_cache() -> None:
    """Best-effort load on import -- a missing, empty, or corrupted cache
    file just means every ticker gets treated as needing a fresh fetch,
    same as a truly cold start. Must never raise and block the app."""
    global _raw_cache, _fetched_at
    if not os.path.isfile(PRICE_CACHE_PATH):
        return
    try:
        with open(PRICE_CACHE_PATH, "rb") as f:
            payload = pickle.load(f)
        _raw_cache = payload.get("bars", {})
        _fetched_at = payload.get("fetched_at", {})
    except Exception as e:  # noqa: BLE001 - corrupted cache file, not a crash
        print(f"data: price cache load failed ({e}); starting cold.")
        _raw_cache = {}
        _fetched_at = {}


def _save_price_cache() -> None:
    # Not the usual write-to-.tmp-then-os.replace() atomic swap: PRICE_CACHE_PATH
    # is a Docker bind mount (docker-compose.yml), so it's a mount point inside
    # the container, not a plain renameable file -- os.replace() onto it fails
    # with EBUSY ("Device or resource busy"), which silently discarded every
    # save under the old atomic-write version. Writing in place loses crash-
    # atomicity (a mid-write crash could leave a truncated/corrupt pickle),
    # but _load_price_cache() already treats a corrupt file as a cold start
    # rather than crashing, so that tradeoff is acceptable here.
    try:
        with open(PRICE_CACHE_PATH, "wb") as f:
            pickle.dump({"bars": _raw_cache, "fetched_at": _fetched_at}, f)
    except Exception as e:  # noqa: BLE001 - failing to persist shouldn't crash a refresh
        print(f"data: price cache save failed ({e})")


_load_price_cache()


def _cache_is_fresh(ticker: str, now: float) -> bool:
    """Same-day (ET) TTL -- a bar fetched anytime today is good enough; a
    stale/missing one needs a real fetch. Matches is_stale()'s own
    once-per-close-plus-2h-while-open cadence, just applied per ticker."""
    fetched_at = _fetched_at.get(ticker)
    if fetched_at is None or ticker not in _raw_cache:
        return False
    fetched_dt = datetime.fromtimestamp(fetched_at, ET)
    now_dt = datetime.fromtimestamp(now, ET)
    if fetched_dt.date() != now_dt.date():
        return False
    if market_is_open(now_dt) and now - fetched_at > REFRESH_INTERVAL_OPEN:
        return False
    return True


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


_RATE_LIMITED = "__rate_limited__"  # sentinel error string _fetch_one uses to flag YFRateLimitError


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
    except YFRateLimitError:
        # Distinct from an ordinary per-ticker failure -- Yahoo is throttling
        # the whole run, not rejecting this one symbol. warm_cache() checks
        # for this sentinel and stops submitting the rest of the batch rather
        # than burning through it against a wall; the untried tickers just
        # stay stale and get picked up on the next refresh (background loop
        # or manual Refresh), same as if this run had never touched them.
        return ticker, None, _RATE_LIMITED
    except Exception as e:  # noqa: BLE001 - one bad ticker shouldn't kill the bulk fetch
        return ticker, None, str(e) or type(e).__name__


def warm_cache(tickers: list[str] | None = None, force: bool = False) -> None:
    """Bulk-fetch raw OHLCV. Blocking; run this off the request path (startup
    + background refresher) except for the manual Refresh button, which
    passes force=True and accepts the wait.

    force=False (startup/background): only fetches tickers whose cached bars
    are missing, corrupted (fails to load), or past today's TTL -- everything
    else is served straight from the on-disk cache, so a container rebuild
    doesn't force a full re-fetch of ~2000 tickers just because the process
    restarted. force=True (Refresh button): re-fetches everything regardless
    of TTL, same as before this cache existed."""
    global _last_fetch_time, _fetch_progress
    tickers = tickers or TICKERS
    now = time.time()
    to_fetch = tickers if force else [tk for tk in tickers if not _cache_is_fresh(tk, now)]

    if to_fetch:
        with _lock:
            _fetch_progress = {"done": 0, "total": len(to_fetch)}
        rate_limited = False
        try:
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
                futures = {pool.submit(_fetch_one, tk): tk for tk in to_fetch}
                # as_completed (real completion order, unlike pool.map()) so
                # progress updates as each ticker actually lands, and so a
                # rate-limit hit can stop the run instead of only being
                # noticed after every ticker in the batch has been tried.
                for future in as_completed(futures):
                    tk, df, err = future.result()
                    if err == _RATE_LIMITED:
                        rate_limited = True
                        # Cancel whatever hasn't started yet -- futures already
                        # in flight still finish (can't interrupt a running
                        # yf.download call), but nothing new gets kicked off.
                        for f in futures:
                            f.cancel()
                        break
                    with _lock:
                        if df is not None:
                            _raw_cache[tk] = df
                            _fetched_at[tk] = now
                            _raw_errors.pop(tk, None)
                        else:
                            _raw_errors[tk] = err
                        _fetch_progress["done"] += 1
        finally:
            with _lock:
                _fetch_progress = None
        if rate_limited:
            # Untried tickers are simply left as-is (still stale, or missing
            # from the cache) -- they'll be retried on the next refresh
            # (background loop's next wakeup, or a manual Refresh), same as
            # if this run had never gotten to them.
            print("data: warm_cache stopped early -- Yahoo rate-limited this batch; "
                  "remaining tickers will retry on the next scheduled refresh.")
        _save_price_cache()

    with _lock:
        _last_fetch_time = now


def get_bars(ticker: str) -> pd.DataFrame | None:
    return _raw_cache.get(ticker)


def get_error(ticker: str) -> str | None:
    return _raw_errors.get(ticker)


def get_fetched_at(ticker: str) -> float | None:
    """When this ticker's currently-cached bars were actually fetched --
    lets a caller (app.py's computed-results cache) tell whether a stored
    computed result is still valid (bars unchanged since it was computed)
    or needs recomputing (bars refetched since), without needing to
    recompute just to find out."""
    return _fetched_at.get(ticker)


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

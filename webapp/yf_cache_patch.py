"""Disables yfinance's on-disk tz/cookie/isin caches (peewee-backed SQLite files under
~/.cache/py-yfinance/) before any yfinance call happens anywhere in this app.

Root cause this works around: yfinance's cache.py opens each cache as a module-level
peewee.SqliteDatabase singleton. peewee's connection state is thread-local, and in
production this app observed FD counts climbing far faster than the bounded, persistent
ThreadPoolExecutors (data.py's _fetch_executor, app.py's _compute_executor,
strategy_common.py's _earnings_executor) should allow -- e.g. 950 open FDs to
tkr-tz.db/tkr-tz.db-wal after 17h serving ~1450 tickers, eventually hitting
OSError: [Errno 24] Too many open files and taking the whole server down. The exact
mechanism (per-thread vs per-call) was never pinned down against the live container, so
rather than patch around a specific guess, this removes the on-disk cache layer entirely --
yfinance already ships no-op dummy cache classes for exactly this "cache unavailable"
case (normally triggered when its cache directory isn't writable); this activates that
same fallback unconditionally.

Tradeoff: every ticker's timezone/cookie/ISIN lookup that would have hit the on-disk
cache now goes over the network every time instead of being cached across process
restarts. This app only needs the resulting DataFrame, fetches daily bars (not
intraday, where tz handling matters most), and already treats yfinance calls as
network-bound/rate-limit-prone -- the extra per-call cost is small and worth trading
for "never runs the server out of file descriptors again."

Call apply() once, before importing/using yfinance anywhere -- p.py, webapp/data.py, and
webapp/build_universe.py each call this first thing.
"""


def apply() -> None:
    from yfinance import cache

    cache._TzCacheManager._tz_cache = cache._TzCacheDummy()
    cache._CookieCacheManager._Cookie_cache = cache._CookieCacheDummy()
    cache._ISINCacheManager._isin_cache = cache._ISINCacheDummy()

"""Disables yfinance's on-disk tz/cookie/isin caches (peewee-backed SQLite files under
~/.cache/py-yfinance/) before any yfinance call happens anywhere in this app.

Those caches leaked file descriptors in production (peewee's SqliteDatabase connections
are thread-local and never closed) until the server ran out of FDs. Rather than chase the
exact leak mechanism, this activates yfinance's own no-op dummy cache classes (normally
used when the cache directory isn't writable) unconditionally -- tz/cookie/ISIN lookups
just go over the network each time instead of being cached across restarts, which is fine
for this app's daily-bar, already-rate-limit-tolerant fetches.

Call apply() once, before importing/using yfinance anywhere -- p.py, backend/data.py, and
backend/build_universe.py each call this first thing.
"""


def apply() -> None:
    from yfinance import cache

    cache._TzCacheManager._tz_cache = cache._TzCacheDummy()
    cache._CookieCacheManager._Cookie_cache = cache._CookieCacheDummy()
    cache._ISINCacheManager._isin_cache = cache._ISINCacheDummy()

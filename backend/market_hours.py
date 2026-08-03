"""US regular-session market hours (9:30 AM-4:00 PM America/New_York, Mon-Fri) -- pure
functions over an explicit `now`, no reliance on datetime.now() internally, so callers control
time and tests don't need to mock the clock. See
docs/superpowers/specs/2026-08-03-market-hours-background-fetch-design.md.

Holidays and early closes are NOT accounted for (v1 scope) -- a market holiday reads as an
ordinary weekday, so the loop will attempt its once-after-close fetch and get a same-as-before
gap response. Harmless (same cost as always-on fetching), just not fully optimized.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def is_market_open(now: datetime) -> bool:
    local = now.astimezone(MARKET_TZ)
    if local.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= local.time() < MARKET_CLOSE


def most_recent_close_boundary(now: datetime) -> datetime:
    """The timestamp of the most recent 4:00 PM ET close at or before `now`. If the market is
    currently open, this is the PRIOR session's close (today's close hasn't happened yet), so a
    close-period fetch made during today's open session is correctly seen as stale once today's
    own close arrives."""
    local = now.astimezone(MARKET_TZ)
    candidate = local.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0)
    while candidate.weekday() >= 5 or candidate > local:
        candidate -= timedelta(days=1)
        candidate = candidate.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0)
    return candidate

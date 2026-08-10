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


def market_status_text(now: datetime) -> str:
    """Human-readable current market status for the review chatbot -- computed fresh per call
    (not baked into the frozen daily snapshot, which would go stale mid-session), so it always
    reflects the real time the message is being sent, not whenever the snapshot was generated."""
    local = now.astimezone(MARKET_TZ)
    weekday_closed = local.weekday() >= 5
    if is_market_open(now):
        open_dt = local.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0)
        close_dt = local.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0)
        minutes_open = int((local - open_dt).total_seconds() // 60)
        minutes_to_close = int((close_dt - local).total_seconds() // 60)
        return (
            f"Market is OPEN (regular session, {minutes_open} minutes since 9:30 AM ET open, "
            f"{minutes_to_close} minutes until 4:00 PM ET close). Current time: "
            f"{local.strftime('%Y-%m-%d %I:%M %p %Z')}."
        )
    if weekday_closed:
        status = f"Market is CLOSED (weekend). Current time: {local.strftime('%Y-%m-%d %I:%M %p %Z')}."
    elif local.time() < MARKET_OPEN:
        open_dt = local.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0)
        minutes_to_open = int((open_dt - local).total_seconds() // 60)
        status = (
            f"Market is CLOSED (pre-market, opens in {minutes_to_open} minutes at 9:30 AM ET). "
            f"Current time: {local.strftime('%Y-%m-%d %I:%M %p %Z')}."
        )
    else:
        close_dt = local.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0)
        minutes_since_close = int((local - close_dt).total_seconds() // 60)
        status = (
            f"Market is CLOSED (after-hours, {minutes_since_close} minutes since 4:00 PM ET close). "
            f"Current time: {local.strftime('%Y-%m-%d %I:%M %p %Z')}."
        )
    return status


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

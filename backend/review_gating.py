"""Review-cycle time-window gating for the daily review chatbot -- see
docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md, Part 7.

Pure functions over an explicit `now`, same discipline as market_hours.py -- no reliance on
datetime.now() internally, so callers control time and this is directly unit-testable without
waiting for real clock time.

Chat itself never locks by time (the user decides when a review's context is done, not a
clock) -- this module only gates when a NEW review is allowed to start, i.e. whether today's
close data has actually landed yet."""
from datetime import datetime, time

import backend.market_hours as market_hours

# The window during which a NEW review is allowed to start. Distinct from "is the market
# closed," which is also true at 3am -- a new review only makes sense once, right after that
# day's close, not at any arbitrary later hour.
_NEW_REVIEW_WINDOW_START = time(16, 0)   # 4:00pm ET
_NEW_REVIEW_WINDOW_END = time(23, 59, 59)  # 11:59:59pm ET


def review_available_to_start(now: datetime) -> bool:
    """True only in the window from when the post-close fetch has landed through 11:59pm --
    NOT simply "market is closed" (which is also true at 3am, where a new review shouldn't
    start), and NOT the instant the clock hits 4pm (the background loop's own once-per-close-
    period fetch takes real time -- ~35 minutes measured in production, see
    2026-08-03-market-hours-background-fetch-design.md -- so triggering right at 4:00pm would
    use stale pre-close data)."""
    import backend.db as db  # deferred -- avoids a hard import-time dependency for a module this small

    if market_hours.is_market_open(now):
        return False
    local = now.astimezone(market_hours.MARKET_TZ)
    if not (_NEW_REVIEW_WINDOW_START <= local.time() <= _NEW_REVIEW_WINDOW_END):
        return False
    boundary = market_hours.most_recent_close_boundary(now)
    last_close_fetch = db.get_last_close_fetch_at()
    if last_close_fetch is None:
        return False
    return datetime.fromisoformat(last_close_fetch) >= boundary

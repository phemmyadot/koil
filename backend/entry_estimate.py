"""
Estimate Entry: a realistic limit-order price for a strategy's open position, combining
the strategy's own historical max-adverse-excursion (MAE) on winning trades with a
chart-anchored support level the user supplies. See
docs/superpowers/specs/2026-07-29-estimate-entry-design.md for the full design.

Pure computation, no I/O -- backend/app.py's /api/estimate_entry endpoint supplies every
input from already-computed in-memory state plus the user's support_levels input.
"""


def estimate_entry(current_price: float, avg_mae_wins_pct: float,
                    support_levels: list[float]) -> dict:
    """current_price is the MAE-floor basis -- works for both an open position and a fresh
    TAKE/Pending signal with no position yet. Returns mae_floor, support_used,
    recommended_limit, pct_below_current."""
    mae_floor = round(current_price * (1 - avg_mae_wins_pct / 100), 2)

    supports_below = [s for s in support_levels if s < current_price]
    support_used = max(supports_below) if supports_below else mae_floor

    recommended_limit = max(mae_floor, support_used)
    # Never at or above market -- a "limit" that isn't below current price isn't a dip entry.
    recommended_limit = min(recommended_limit, round(current_price * 0.99, 2))

    pct_below_current = round((recommended_limit - current_price) / current_price * 100, 2)

    return {
        "mae_floor": mae_floor,
        "support_used": support_used,
        "recommended_limit": recommended_limit,
        "pct_below_current": pct_below_current,
    }


def order_method(instrument: str) -> str:
    """Deterministic order-staging rule for a TAKE/Pending signal that already passed the
    quality bar. instrument: 'spot' or 'option'."""
    return "GTC tonight" if instrument == "option" else "set at open, cancel by 10:30am"

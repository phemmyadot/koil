"""
Estimate Entry: a realistic limit-order price for a strategy's open position, combining
the strategy's own historical max-adverse-excursion (MAE) on winning trades with a
chart-anchored support level the user supplies. See
docs/superpowers/specs/2026-07-29-estimate-entry-design.md for the full design.

Pure computation, no I/O -- backend/app.py's /api/estimate_entry endpoint supplies every
input from already-computed in-memory state plus the user's support_levels input.
"""


def estimate_entry(current_price: float, entry_price: float, avg_mae_wins_pct: float,
                    support_levels: list[float]) -> dict:
    """
    current_price: latest close (payload["price"]).
    entry_price: the strategy's open_position.entry_price -- a real simulated entry, not a
                 stand-in. Callers must not call this without a live open_position.
    avg_mae_wins_pct: strategy_common.summarize()'s avg_mae_wins_pct for this strategy
                       (positive float, e.g. 3.91 for a 3.91% average dip on winners).
    support_levels: user-entered support prices, any order. Only levels below
                     current_price count as a floor candidate.

    Returns mae_floor, support_used, recommended_limit, pct_below_current.
    """
    mae_floor = round(entry_price * (1 - avg_mae_wins_pct / 100), 2)

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

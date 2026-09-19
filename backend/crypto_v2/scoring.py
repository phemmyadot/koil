"""Component-based setup scoring (doc §23) and a market-cap/volume proxy execution score (doc
§24-25 -- no real order-book depth/spread data exists in this scope). Never collapses into a
single opaque number without keeping the components: engine.py stores both the score and the
breakdown of what fired, so a signal can always answer "why."
"""


def setup_score(components: dict[str, bool], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Sums weighted components that fired; returns the total plus a breakdown (each
    component's own contribution, 0 for ones that didn't fire)."""
    breakdown = {name: (weights.get(name, 0.0) if fired else 0.0) for name, fired in components.items()}
    return sum(breakdown.values()), breakdown


def execution_score(market_cap: float | None, quote_volume_24h: float | None) -> float:
    """0-10 tradability proxy, standing in for doc §25's real liquidity/spread/depth/slippage
    inputs -- quote_volume/market_cap turnover is the same proxy crypto_universe.py's
    MEME_MARKET_CAP_FLOOR + volume-spike ranking already leans on, formalized into a score.
    Banded rather than linear: an illiquid micro-cap's one-off volume spike shouldn't out-score
    a steadily-liquid large-cap just because the ratio is numerically bigger that day."""
    if not market_cap or not quote_volume_24h or market_cap <= 0:
        return 0.0
    turnover = quote_volume_24h / market_cap
    for threshold, score in ((0.5, 10.0), (0.2, 8.0), (0.1, 6.0), (0.05, 4.0), (0.01, 2.0)):
        if turnover >= threshold:
            return score
    return 0.0

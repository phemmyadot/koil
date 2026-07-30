"""
Adaptive SR Levels -- Python port of pines/sr.pine, feeding Estimate Entry's support-level
input (see docs/superpowers/specs/2026-07-29-adaptive-sr-port-design.md). Pivot highs/lows
are detected over the stored daily bars, merged into ATR/percent-tolerance-banded zones
(nearby pivots strengthen the same zone instead of creating a new one), and the nearest
zones on each side of the latest close are returned.

Hardcodes pivotStrength=10 -- this dashboard is daily-bars-only (webapp/data.py), and
sr.pine's auto-adapt-to-timeframe logic always resolves to 10 on the daily branch, so the
intraday branching in the original indicator doesn't apply and isn't ported.
"""
import pandas as pd

PIVOT_STRENGTH = 10
DEFAULT_ATR_LENGTH = 22 
SR_TOL_ATR = 0.75
SR_TOL_PCT = 0.005
SR_MAX_ZONES = 60
LEVELS_EACH_SIDE = 3


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    """Same formula as build_universe.py's _wilder_atr() -- duplicated rather than imported
    across modules, since that one is underscore-prefixed (module-private by convention)."""
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def _find_pivots(bars: pd.DataFrame, strength: int) -> list[tuple[int, float, bool]]:
    """Returns (bar_index, price, is_high) for every confirmed pivot -- a bar is a pivot low
    if its Low is (one of, on a tie) the minimum over the strength-bar window on both sides
    (mirror for pivot high on High), matching ta.pivotlow/ta.pivothigh(src, strength, strength).
    A tie is resolved to the center bar itself, same as Pine: the center is checked directly
    against the window extreme, not required to be the unique occurrence of it."""
    pivots = []
    low, high = bars["Low"].values, bars["High"].values
    n = len(bars)
    for i in range(strength, n - strength):
        window_low = low[i - strength:i + strength + 1]
        if low[i] == window_low.min():
            pivots.append((i, float(low[i]), False))
        window_high = high[i - strength:i + strength + 1]
        if high[i] == window_high.max():
            pivots.append((i, float(high[i]), True))
    pivots.sort(key=lambda p: p[0])
    return pivots


def compute_sr_levels(bars: pd.DataFrame, atr_length: int = DEFAULT_ATR_LENGTH) -> dict:
    """Returns {"support": [float, ...], "resistance": [float, ...]}, each up to
    LEVELS_EACH_SIDE levels, nearest-to-current-price first. bars: standard OHLCV
    DataFrame (webapp/data.py's shape). atr_length: the zone-merge tolerance band uses
    whichever ATR window the calling strategy itself reasons about volatility with --
    VCP/VCPO are both ATR(22) (the default here, see strategy_vcp.py/strategy_vcpo.py's
    ATR_LEN), VEXH is ATR(14) -- callers pass the strategy-appropriate value rather than
    all three sharing one fixed window unrelated to any of them. Too few bars to detect
    any pivot (fewer than 2*PIVOT_STRENGTH+1 rows) returns empty lists for both, not an
    error -- matches sr.pine's own "if array.size(zonePrice) > 0" no-op on an empty zone set."""
    if len(bars) < 2 * PIVOT_STRENGTH + 1:
        return {"support": [], "resistance": []}

    atr = _wilder_atr(bars["High"], bars["Low"], bars["Close"], atr_length)
    close = bars["Close"].values
    pivots = _find_pivots(bars, PIVOT_STRENGTH)

    # zones: list of [price, touches] -- weighted-average merge, FIFO eviction past SR_MAX_ZONES.
    zones: list[list[float]] = []
    for bar_i, price, _is_high in pivots:
        atr_val = atr.iloc[bar_i]
        close_val = close[bar_i]
        if pd.isna(atr_val):
            continue
        tol = max(SR_TOL_ATR * atr_val, SR_TOL_PCT * close_val)

        merged = False
        for zone in zones:
            if abs(price - zone[0]) <= tol:
                zone[0] = (zone[0] * zone[1] + price) / (zone[1] + 1)
                zone[1] += 1
                merged = True
                break
        if not merged:
            zones.append([price, 1])
            if len(zones) > SR_MAX_ZONES:
                zones.pop(0)

    if not zones:
        return {"support": [], "resistance": []}

    # Round before comparing, not a tolerance band -- an earlier version excluded a
    # 0.75xATR/0.5% band around current price to avoid a one-cent-rounding coincidence
    # (ELF: a zone at $84.09 vs an $84.10 close), but that band was wide enough to also
    # swallow real nearby levels (GENI: a genuine support 3.3% below price, inside a 4.3%
    # band, got dropped entirely). Rounding both sides to the cent before the strict "<"/">"
    # comparison fixes the one-cent-coincidence case without discarding real nearby levels.
    current_price = round(float(close[-1]), 2)
    support = sorted((z[0] for z in zones if round(z[0], 2) < current_price), reverse=True)[:LEVELS_EACH_SIDE]
    resistance = sorted((z[0] for z in zones if round(z[0], 2) > current_price))[:LEVELS_EACH_SIDE]
    return {"support": [round(s, 2) for s in support], "resistance": [round(r, 2) for r in resistance]}

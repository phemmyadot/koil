"""Bar-by-bar port of pines/sr.pine (Adaptive SR Levels)."""
import pandas as pd

PIVOT_STRENGTH = 10
DEFAULT_ATR_LENGTH = 22
SR_TOL_ATR = 0.75
SR_TOL_PCT = 0.005
SR_MAX_ZONES = 60
LEVELS_EACH_SIDE = 3


def _rma(values: list[float], length: int) -> list[float | None]:
    """Pine's ta.rma: seed with a simple mean of the first `length` values, then recurse."""
    n = len(values)
    out: list[float | None] = [None] * n
    if n <= length:
        return out
    out[length] = sum(values[1:length + 1]) / length
    for i in range(length + 1, n):
        out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def _true_range(high: list[float], low: list[float], close: list[float]) -> list[float]:
    tr = [high[0] - low[0]]
    for i in range(1, len(high)):
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return tr


def compute_sr_levels(bars: pd.DataFrame, atr_length: int = DEFAULT_ATR_LENGTH,
                       pivot_strength: int = PIVOT_STRENGTH) -> dict:
    """Returns {"support": [(price, touches), ...], "resistance": [(price, touches), ...]},
    up to LEVELS_EACH_SIDE each, nearest-to-current-price first."""
    high, low, close = bars["High"].tolist(), bars["Low"].tolist(), bars["Close"].tolist()
    tr = _true_range(high, low, close)
    atr_series = _rma(tr, atr_length)
    n = len(high)
    if n < 2 * pivot_strength + 1 or all(a is None for a in atr_series):
        return {"support": [], "resistance": []}

    zone_price: list[float] = []
    zone_touch: list[int] = []
    zone_tol: list[float] = []  # tolerance the zone was founded with

    def add_pivot_zone(p: float, atr: float, close_val: float) -> None:
        tol = max(SR_TOL_ATR * atr, SR_TOL_PCT * close_val)
        merged = False
        for i in range(len(zone_price)):
            if not merged and abs(p - zone_price[i]) <= min(tol, zone_tol[i]):
                t = zone_touch[i]
                zone_price[i] = (zone_price[i] * t + p) / (t + 1)
                zone_touch[i] = t + 1
                merged = True
        if not merged:
            zone_price.append(p)
            zone_touch.append(1)
            zone_tol.append(tol)
            if len(zone_price) > SR_MAX_ZONES:
                zone_price.pop(0)
                zone_touch.pop(0)
                zone_tol.pop(0)

    for bar_index in range(n):
        atr_val = atr_series[bar_index]
        if atr_val is None:
            continue

        confirm_target = bar_index - pivot_strength
        if confirm_target < pivot_strength:
            pivot_high_price = pivot_low_price = None
        else:
            lo, hi = confirm_target - pivot_strength, confirm_target + pivot_strength
            center_high = high[confirm_target]
            is_pivot_high = (all(center_high >= high[j] for j in range(lo, confirm_target))
                              and all(center_high > high[j] for j in range(confirm_target + 1, hi + 1)))
            pivot_high_price = center_high if is_pivot_high else None
            center_low = low[confirm_target]
            is_pivot_low = (all(center_low <= low[j] for j in range(lo, confirm_target))
                             and all(center_low < low[j] for j in range(confirm_target + 1, hi + 1)))
            pivot_low_price = center_low if is_pivot_low else None

        if pivot_high_price is not None:
            add_pivot_zone(pivot_high_price, atr_val, close[bar_index])
        if pivot_low_price is not None:
            add_pivot_zone(pivot_low_price, atr_val, close[bar_index])

    if not zone_price:
        return {"support": [], "resistance": []}

    current_price = close[-1]

    def select(side_is_resistance: bool) -> list[tuple[float, int]]:
        last_picked = None
        picked = []
        for _ in range(LEVELS_EACH_SIDE):
            best = None
            best_i = -1
            for i, zp in enumerate(zone_price):
                is_side = (zp > current_price) if side_is_resistance else (zp < current_price)
                beyond = (last_picked is None) or (
                    (zp > last_picked) if side_is_resistance else (zp < last_picked))
                closer = (best is None) or ((zp < best) if side_is_resistance else (zp > best))
                if is_side and beyond and closer:
                    best, best_i = zp, i
            if best_i < 0:
                break
            last_picked = best
            picked.append((best, zone_touch[best_i]))
        return picked

    resistance = select(side_is_resistance=True)
    support = select(side_is_resistance=False)
    return {
        "support": [(round(p, 2), t) for p, t in support],
        "resistance": [(round(p, 2), t) for p, t in resistance],
    }

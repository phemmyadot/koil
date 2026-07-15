"""
Strategy D: VCP fixed-bracket breakout (validated in the session's model_d.py
work, with the corrected 50-day volume-dry-up window). Reads from the shared
raw-data cache (webapp/data.py) and extends it with a "signal today" /
TAKE-SKIP verdict evaluator.
"""
import itertools
import statistics

import pandas as pd

ADX_THRESHOLD = 25.0
BBW_PCT_LIMIT = 45.0
VOL_DRYUP_MULT = 1.05
VOL_MULTIPLIER = 1.0
STOP_ATR_MULT = 2.5
TRAIL_ATR_MULT = 3.5
TRAIL_ACTIVATE_ATR_MULT = 1.0
EMA_LEN = 50
ATR_LEN = 14
RESISTANCE_LEN = 20
BBW_LEN = 20
BBW_RANK_LEN = 100
VOL_FAST_LEN = 5
VOL_SLOW_LEN = 50
VOL_AVG_LEN = 50


def _bars_from_df(df: pd.DataFrame) -> list[dict]:
    bars = []
    for idx, row in df.iterrows():
        bars.append({"o": float(row.Open), "h": float(row.High), "l": float(row.Low),
                      "c": float(row.Close), "v": float(row.Volume), "d": idx.strftime("%Y-%m")})
    return bars


def _rma(values, length, n):
    out = [None] * n
    for i in range(n):
        if i < length:
            continue
        elif i == length:
            out[i] = sum(values[1:length + 1]) / length
        else:
            out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def compute_indicators(bars):
    n = len(bars)
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    v = [b["v"] for b in bars]

    ema = [None] * n
    k = 2.0 / (EMA_LEN + 1)
    for i in range(n):
        if i < EMA_LEN:
            continue
        elif i == EMA_LEN:
            ema[i] = sum(c[i - EMA_LEN + 1:i + 1]) / EMA_LEN
        else:
            ema[i] = c[i] * k + ema[i - 1] * (1 - k)

    tr = [None] * n
    for i in range(n):
        tr[i] = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = [None] * n
    for i in range(n):
        if i < ATR_LEN:
            continue
        elif i == ATR_LEN:
            atr[i] = sum(tr[1:ATR_LEN + 1]) / ATR_LEN
        else:
            atr[i] = (atr[i - 1] * (ATR_LEN - 1) + tr[i]) / ATR_LEN

    plus_dm, minus_dm = [0.0] * n, [0.0] * n
    for i in range(1, n):
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    tr_rma = _rma(tr, ATR_LEN, n)
    plus_dm_rma = _rma(plus_dm, ATR_LEN, n)
    minus_dm_rma = _rma(minus_dm, ATR_LEN, n)
    plus_di, minus_di, dx = [None] * n, [None] * n, [None] * n
    for i in range(n):
        if tr_rma[i]:
            plus_di[i] = 100 * plus_dm_rma[i] / tr_rma[i]
            minus_di[i] = 100 * minus_dm_rma[i] / tr_rma[i]
            s = plus_di[i] + minus_di[i]
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / s if s != 0 else 0.0
    adx = _rma([x if x is not None else 0.0 for x in dx], ATR_LEN, n)
    for i in range(n):
        if dx[i] is None:
            adx[i] = None

    local_resistance, pattern_low = [None] * n, [None] * n
    for i in range(n):
        if i < RESISTANCE_LEN:
            continue
        local_resistance[i] = max(h[i - RESISTANCE_LEN:i])
        pattern_low[i] = min(l[i - RESISTANCE_LEN:i])

    bbw = [None] * n
    for i in range(n):
        if i < BBW_LEN - 1:
            continue
        window = c[i - BBW_LEN + 1:i + 1]
        mean = sum(window) / BBW_LEN
        sd = statistics.pstdev(window)
        bbw[i] = (4.0 * sd / c[i] * 100.0) if c[i] > 0 else 0.0

    bbw_rank = [None] * n
    for i in range(n):
        if bbw[i] is None or i < BBW_RANK_LEN:
            continue
        window = [x for x in bbw[i - BBW_RANK_LEN + 1:i + 1] if x is not None]
        if len(window) < BBW_RANK_LEN:
            continue
        bbw_rank[i] = sum(1 for x in window if x < bbw[i]) / len(window) * 100.0

    def sma(vals, length):
        out = [None] * n
        for i in range(n):
            if i < length - 1:
                continue
            out[i] = sum(vals[i - length + 1:i + 1]) / length
        return out

    vol_fast = sma(v, VOL_FAST_LEN)
    vol_slow = sma(v, VOL_SLOW_LEN)
    vol_avg = sma(v, VOL_AVG_LEN)

    return dict(ema=ema, atr=atr, adx=adx, plus_di=plus_di, minus_di=minus_di,
                local_resistance=local_resistance, pattern_low=pattern_low,
                bbw_rank=bbw_rank, vol_fast=vol_fast, vol_slow=vol_slow, vol_avg=vol_avg)


def run(bars, ind, adx_threshold=ADX_THRESHOLD, bbw_pct_limit=BBW_PCT_LIMIT,
        vol_dryup_mult=VOL_DRYUP_MULT, vol_multiplier=VOL_MULTIPLIER,
        stop_atr_mult=STOP_ATR_MULT, trail_atr_mult=TRAIL_ATR_MULT,
        trail_activate_atr_mult=TRAIL_ACTIVATE_ATR_MULT):
    """Returns (trades, signal_today, in_position, open_position). Parameters
    default to the validated baseline but can be overridden -- used by
    optimize() to sweep configs without mutating shared module state.
    open_position (None unless a position is open) is {"entry_price",
    "target"} -- target is a projected reward level (entry +
    trail_atr_mult*ATR-at-entry, mirroring the trailing stop's distance), NOT
    an order this strategy actually places -- it trails/pattern-breaks out,
    it doesn't take profit at a fixed price."""
    n = len(bars)
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    o = [b["o"] for b in bars]
    v = [b["v"] for b in bars]
    ema, atr, adx = ind["ema"], ind["atr"], ind["adx"]
    plus_di, minus_di = ind["plus_di"], ind["minus_di"]
    local_resistance, pattern_low = ind["local_resistance"], ind["pattern_low"]
    bbw_rank, vol_fast, vol_slow, vol_avg = ind["bbw_rank"], ind["vol_fast"], ind["vol_slow"], ind["vol_avg"]

    trades = []
    position = None
    pending_entry = False

    for i in range(1, n):
        needed = [ema[i], atr[i], adx[i], local_resistance[i], bbw_rank[i - 1],
                  vol_fast[i - 1], vol_slow[i - 1], vol_avg[i]]
        if any(x is None for x in needed):
            continue

        if pending_entry and position is None:
            entry_price = o[i]
            stop_init = min(entry_price - stop_atr_mult * atr[i - 1],
                             pattern_low[i - 1] if pattern_low[i - 1] is not None else entry_price - stop_atr_mult * atr[i - 1])
            position = {"entry_i": i, "entry_price": entry_price, "stop": stop_init,
                        "high_since": h[i], "pattern_low": pattern_low[i - 1], "entry_atr": atr[i - 1]}
            pending_entry = False

        if position is not None:
            position["high_since"] = max(position["high_since"], h[i])
            in_profit = (c[i] - position["entry_price"]) >= trail_activate_atr_mult * atr[i]
            if in_profit:
                position["stop"] = max(position["stop"], position["high_since"] - trail_atr_mult * atr[i])
            pattern_break = position["pattern_low"] is not None and c[i] < position["pattern_low"]
            stopped = l[i] <= position["stop"]
            if stopped or pattern_break:
                exit_price = position["stop"] if stopped else c[i]
                pnl = exit_price - position["entry_price"]
                trades.append(dict(entry_i=position["entry_i"], exit_i=i, pnl=pnl))
                position = None

        regime_ok = adx[i] > adx_threshold and plus_di[i] > minus_di[i]
        breakout = c[i] > local_resistance[i] and c[i] > ema[i]
        setup_ok = bbw_rank[i - 1] < bbw_pct_limit and vol_fast[i - 1] <= vol_slow[i - 1] * vol_dryup_mult
        volume_confirmed = v[i] >= vol_multiplier * vol_avg[i]
        long_signal = regime_ok and breakout and setup_ok and volume_confirmed

        pending_entry = long_signal and position is None

    open_position = None
    if position is not None:
        open_position = {
            "entry_price": round(position["entry_price"], 4),
            "target": round(position["entry_price"] + trail_atr_mult * position["entry_atr"], 4),
        }
    return trades, pending_entry, position is not None, open_position


def _verdict(signal_today: bool, in_position: bool, n_trades: int, win_rate: float, pf: float) -> tuple[str, str]:
    """A real TAKE/SKIP call, not a vague confidence bucket -- based on whether
    THIS ticker's own backtested history with THIS strategy actually shows an
    edge, not just whether a signal fired."""
    if in_position:
        return "IN TRADE", "a position from a prior signal is still open"
    if not signal_today:
        return "NO SIGNAL", "no entry signal on the latest close"
    if n_trades < 5:
        return "SKIP", f"only {n_trades} historical trades on this ticker -- not enough data to trust the signal"
    if pf >= 1.5 and win_rate >= 40:
        return "TAKE", f"{n_trades} trades historically, {win_rate:.1f}% WR, PF {pf:.2f} -- real edge on this ticker"
    return "SKIP", f"{n_trades} trades historically, {win_rate:.1f}% WR, PF {pf:.2f} -- no real edge on this ticker"


BASELINE_CONFIG = dict(adx_threshold=ADX_THRESHOLD, bbw_pct_limit=BBW_PCT_LIMIT,
                       vol_multiplier=VOL_MULTIPLIER, stop_atr_mult=STOP_ATR_MULT,
                       trail_atr_mult=TRAIL_ATR_MULT)

OPTIMIZE_GRID = dict(
    adx_threshold=[20.0, 25.0, 30.0],
    bbw_pct_limit=[30.0, 45.0, 60.0],
    vol_multiplier=[1.0, 1.4],
    stop_atr_mult=[2.0, 2.5, 3.0],
    trail_atr_mult=[3.0, 3.5, 4.0],
)


def _summarize(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100 if trades else 0.0
    return {"n_trades": len(trades), "win_rate": round(wr, 1), "profit_factor": round(pf, 2)}


def optimize(ticker: str, df: pd.DataFrame, train_frac: float = 0.7, min_trades_per_split: int = 3) -> dict | None:
    """Per-ticker parameter sweep, time-split train/holdout. Returns None if
    no config clears the minimum trade count on both slices."""
    bars = _bars_from_df(df)
    if len(bars) < 300:
        return None
    ind = compute_indicators(bars)
    split_i = int(len(bars) * train_frac)

    keys = list(OPTIMIZE_GRID.keys())
    best = None
    for combo in itertools.product(*OPTIMIZE_GRID.values()):
        cfg = dict(zip(keys, combo))
        trades, _, _, open_position = run(bars, ind, **cfg)
        train_trades = [t for t in trades if t["entry_i"] < split_i]
        holdout_trades = [t for t in trades if t["entry_i"] >= split_i]
        if len(train_trades) < min_trades_per_split or len(holdout_trades) < min_trades_per_split:
            continue
        st, sh = _summarize(train_trades), _summarize(holdout_trades)
        robust_pf = min(st["profit_factor"], sh["profit_factor"])
        robust_wr = min(st["win_rate"], sh["win_rate"])
        score = robust_pf * robust_wr
        if best is None or score > best["_score"]:
            # open_position is a single value shared by both splits (one
            # config, so one live position) -- not per-split, same as config.
            best = {"config": cfg, "train_stats": st, "holdout_stats": sh,
                    "open_position": open_position, "_score": score}

    if best is None:
        return None
    best.pop("_score")
    return best


def evaluate(ticker: str, df: pd.DataFrame) -> dict:
    bars = _bars_from_df(df)
    if len(bars) < 250:
        raise ValueError("insufficient history")
    ind = compute_indicators(bars)
    trades, signal_today, in_position, open_position = run(bars, ind)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100 if trades else 0.0
    verdict, verdict_reason = _verdict(signal_today, in_position, len(trades), wr, pf)
    first_trade_date = bars[trades[0]["entry_i"]]["d"] if trades else None

    return {
        "signal_today": signal_today,
        "in_position": in_position,
        "open_position": open_position,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "n_trades": len(trades),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "first_trade_date": first_trade_date,
    }

"""
Strategy A: KAMA adaptive pullback (validated in the session's model.py work).
Reads from the shared raw-data cache (webapp/data.py) instead of fetching its
own data, and extends it with a "signal today" / TAKE-SKIP verdict evaluator
for the dashboard.
"""
import itertools

import pandas as pd

ADX_THRESHOLD = 20.0
EXTENSION_MULT = 1.5
EXTENSION_LOOKBACK = 10
PULLBACK_ATR_BUF = 0.5
STOP_ATR_MULT = 1.5
TRAIL_ATR_MULT = 3.0
TRAIL_ACTIVATE_ATR_MULT = 1.0


def _bars_from_df(df: pd.DataFrame) -> list[dict]:
    bars = []
    for idx, row in df.iterrows():
        bars.append({"o": float(row.Open), "h": float(row.High), "l": float(row.Low),
                      "c": float(row.Close), "v": float(row.Volume), "d": idx.strftime("%Y-%m")})
    return bars


def compute_indicators(bars, er_period=10, adx_len=14, rsi_len=14, rsi_sig_len=9, atr_len=14,
                        kama_fast=2, kama_slow=30):
    n = len(bars)
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]

    kama = [None] * n
    fast_sc = 2.0 / (kama_fast + 1)
    slow_sc = 2.0 / (kama_slow + 1)
    for i in range(n):
        if i < er_period:
            continue
        change = abs(c[i] - c[i - er_period])
        vol = sum(abs(c[j] - c[j - 1]) for j in range(i - er_period + 1, i + 1))
        er = change / vol if vol != 0 else 0.0
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        prev = kama[i - 1] if kama[i - 1] is not None else c[i - 1]
        kama[i] = prev + sc * (c[i] - prev)

    tr = [None] * n
    for i in range(n):
        tr[i] = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = [None] * n
    for i in range(n):
        if i < atr_len:
            continue
        elif i == atr_len:
            atr[i] = sum(tr[1:atr_len + 1]) / atr_len
        else:
            atr[i] = (atr[i - 1] * (atr_len - 1) + tr[i]) / atr_len

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    def rma(values, length):
        out = [None] * n
        for i in range(n):
            if i < length:
                continue
            elif i == length:
                out[i] = sum(values[1:length + 1]) / length
            else:
                out[i] = (out[i - 1] * (length - 1) + values[i]) / length
        return out

    tr_rma = rma(tr, adx_len)
    plus_dm_rma = rma(plus_dm, adx_len)
    minus_dm_rma = rma(minus_dm, adx_len)
    plus_di = [None] * n
    minus_di = [None] * n
    dx = [None] * n
    for i in range(n):
        if tr_rma[i]:
            plus_di[i] = 100 * plus_dm_rma[i] / tr_rma[i]
            minus_di[i] = 100 * minus_dm_rma[i] / tr_rma[i]
            s = plus_di[i] + minus_di[i]
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / s if s != 0 else 0.0
    adx = rma([x if x is not None else 0.0 for x in dx], adx_len)
    for i in range(n):
        if dx[i] is None:
            adx[i] = None

    gains, losses = [0.0] * n, [0.0] * n
    for i in range(1, n):
        d = c[i] - c[i - 1]
        gains[i] = d if d > 0 else 0.0
        losses[i] = -d if d < 0 else 0.0
    avg_gain, avg_loss = rma(gains, rsi_len), rma(losses, rsi_len)
    rsi = [None] * n
    for i in range(n):
        if avg_gain[i] is None:
            continue
        rsi[i] = 100.0 if avg_loss[i] == 0 else 100 - 100 / (1 + avg_gain[i] / avg_loss[i])
    rsi_sig = [None] * n
    for i in range(n):
        if i >= rsi_sig_len - 1:
            window = [rsi[j] for j in range(i - rsi_sig_len + 1, i + 1)]
            if all(x is not None for x in window):
                rsi_sig[i] = sum(window) / rsi_sig_len

    slow_kama = [None] * n
    slow_er_period = er_period * 3
    for i in range(n):
        if i < slow_er_period:
            continue
        change = abs(c[i] - c[i - slow_er_period])
        vol = sum(abs(c[j] - c[j - 1]) for j in range(i - slow_er_period + 1, i + 1))
        er = change / vol if vol != 0 else 0.0
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        prev = slow_kama[i - 1] if slow_kama[i - 1] is not None else c[i - 1]
        slow_kama[i] = prev + sc * (c[i] - prev)

    return dict(kama=kama, slow_kama=slow_kama, atr=atr, adx=adx, plus_di=plus_di, minus_di=minus_di,
                rsi=rsi, rsi_sig=rsi_sig)


def run(bars, ind, adx_threshold=ADX_THRESHOLD, extension_mult=EXTENSION_MULT,
        extension_lookback=EXTENSION_LOOKBACK, pullback_atr_buf=PULLBACK_ATR_BUF,
        stop_atr_mult=STOP_ATR_MULT, trail_atr_mult=TRAIL_ATR_MULT,
        trail_activate_atr_mult=TRAIL_ACTIVATE_ATR_MULT):
    """Returns (trades, signal_today, in_position, open_position). Parameters
    default to the validated baseline but can be overridden -- used by
    optimize() to sweep configs without mutating shared module state
    (thread/process safe). open_position (None unless a position is open) is
    {"entry_price", "target"} -- target is a projected reward level (entry +
    trail_atr_mult*ATR-at-entry, mirroring the trailing stop's distance), NOT
    an order this strategy actually places -- it trails/trend-breaks out, it
    doesn't take profit at a fixed price."""
    n = len(bars)
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    o = [b["o"] for b in bars]
    kama, slow_kama, atr, adx = ind["kama"], ind["slow_kama"], ind["atr"], ind["adx"]
    plus_di, minus_di = ind["plus_di"], ind["minus_di"]
    rsi, rsi_sig = ind["rsi"], ind["rsi_sig"]

    trades = []
    position = None
    pending_entry = False

    for i in range(1, n):
        if kama[i] is None or atr[i] is None or adx[i] is None or rsi_sig[i] is None:
            continue

        if pending_entry and position is None:
            entry_price = o[i]
            position = {"entry_i": i, "entry_price": entry_price,
                        "stop": entry_price - stop_atr_mult * atr[i - 1], "high_since": h[i],
                        "entry_atr": atr[i - 1]}
            pending_entry = False

        if position is not None:
            position["high_since"] = max(position["high_since"], h[i])
            in_profit = (c[i] - position["entry_price"]) >= trail_activate_atr_mult * atr[i]
            if in_profit:
                position["stop"] = max(position["stop"], position["high_since"] - trail_atr_mult * atr[i])
            exit_line = slow_kama[i] if slow_kama[i] is not None else kama[i]
            trend_break = c[i] < exit_line
            stopped = l[i] <= position["stop"]
            if stopped or trend_break:
                exit_price = position["stop"] if stopped else c[i]
                pnl = exit_price - position["entry_price"]
                trades.append(dict(entry_i=position["entry_i"], exit_i=i, pnl=pnl))
                position = None

        regime_ok = adx[i] > adx_threshold and plus_di[i] > minus_di[i]
        pulled_back = l[i] <= kama[i] and c[i] > (kama[i] - pullback_atr_buf * atr[i])
        rsi_resume = (rsi[i - 1] is not None and rsi_sig[i - 1] is not None
                      and rsi[i - 1] <= rsi_sig[i - 1] and rsi[i] > rsi_sig[i])
        lo = max(0, i - extension_lookback)
        best = 0.0
        for j in range(lo, i):
            if kama[j] is not None and atr[j]:
                best = max(best, (c[j] - kama[j]) / atr[j])
        extended_ok = best >= extension_mult
        long_signal = regime_ok and pulled_back and rsi_resume and extended_ok

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


BASELINE_CONFIG = dict(adx_threshold=ADX_THRESHOLD, extension_mult=EXTENSION_MULT,
                       stop_atr_mult=STOP_ATR_MULT, trail_atr_mult=TRAIL_ATR_MULT)

# Trimmed grid (81 combos) -- kept small deliberately since this runs per
# ticker in a background sweep, not on the request path.
OPTIMIZE_GRID = dict(
    adx_threshold=[15.0, 20.0, 25.0],
    extension_mult=[1.0, 1.5, 2.0],
    stop_atr_mult=[1.0, 1.5, 2.0],
    trail_atr_mult=[2.5, 3.0, 4.0],
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
    """Per-ticker parameter sweep, time-split train/holdout (not a cross-
    ticker split -- there's only one ticker here) so a config that only wins
    on the train slice doesn't get reported as if it were a real edge. Returns
    None if no config clears the minimum trade count on both slices --
    common for a single ticker's history with a low-frequency strategy like
    this one, and a real result, not a bug."""
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

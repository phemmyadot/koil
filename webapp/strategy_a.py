"""
Strategy A: KAMA adaptive pullback (validated in the session's model.py work).
Ported to fetch live data via yfinance instead of a local JSON cache, and
extended with a "signal today" / rating-bucket evaluator for the dashboard.
"""
import yfinance as yf

ADX_THRESHOLD = 20.0
EXTENSION_MULT = 1.5
EXTENSION_LOOKBACK = 10
PULLBACK_ATR_BUF = 0.5
STOP_ATR_MULT = 1.5
TRAIL_ATR_MULT = 3.0
TRAIL_ACTIVATE_ATR_MULT = 1.0


def fetch_bars(ticker: str, period: str = "10y") -> list[dict]:
    df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=False)
    if hasattr(df.columns, "get_level_values"):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    bars = []
    for idx, row in df.iterrows():
        bars.append({"o": float(row.Open), "h": float(row.High), "l": float(row.Low),
                      "c": float(row.Close), "v": float(row.Volume)})
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


def run(bars, ind):
    """Returns (trades, signal_today, in_position) using the validated defaults."""
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
                        "stop": entry_price - STOP_ATR_MULT * atr[i - 1], "high_since": h[i]}
            pending_entry = False

        if position is not None:
            position["high_since"] = max(position["high_since"], h[i])
            in_profit = (c[i] - position["entry_price"]) >= TRAIL_ACTIVATE_ATR_MULT * atr[i]
            if in_profit:
                position["stop"] = max(position["stop"], position["high_since"] - TRAIL_ATR_MULT * atr[i])
            exit_line = slow_kama[i] if slow_kama[i] is not None else kama[i]
            trend_break = c[i] < exit_line
            stopped = l[i] <= position["stop"]
            if stopped or trend_break:
                exit_price = position["stop"] if stopped else c[i]
                pnl = exit_price - position["entry_price"]
                trades.append(dict(entry_i=position["entry_i"], exit_i=i, pnl=pnl))
                position = None

        regime_ok = adx[i] > ADX_THRESHOLD and plus_di[i] > minus_di[i]
        pulled_back = l[i] <= kama[i] and c[i] > (kama[i] - PULLBACK_ATR_BUF * atr[i])
        rsi_resume = (rsi[i - 1] is not None and rsi_sig[i - 1] is not None
                      and rsi[i - 1] <= rsi_sig[i - 1] and rsi[i] > rsi_sig[i])
        extended_ok = False
        lo = max(0, i - EXTENSION_LOOKBACK)
        best = 0.0
        for j in range(lo, i):
            if kama[j] is not None and atr[j]:
                best = max(best, (c[j] - kama[j]) / atr[j])
        extended_ok = best >= EXTENSION_MULT
        long_signal = regime_ok and pulled_back and rsi_resume and extended_ok

        pending_entry = long_signal and position is None

    return trades, pending_entry, position is not None


def _rating(n_trades: int, win_rate: float, pf: float) -> str:
    if n_trades < 5:
        return "LOW"
    if pf >= 2.0 and win_rate >= 45:
        return "HIGH"
    if pf >= 1.0:
        return "MEDIUM"
    return "LOW"


def evaluate(ticker: str) -> dict:
    bars = fetch_bars(ticker)
    if len(bars) < 250:
        raise ValueError("insufficient history")
    ind = compute_indicators(bars)
    trades, signal_today, in_position = run(bars, ind)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100 if trades else 0.0

    return {
        "signal_today": signal_today,
        "in_position": in_position,
        "n_trades": len(trades),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "rating": _rating(len(trades), wr, pf),
    }

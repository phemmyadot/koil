"""
VCP Master (vcp.pine, fixed + volume-confirmed) ported for the dashboard.
Mirrors test_vcp.py's validated logic: ATR compression + 20-bar breakout +
volume confirmation, multi-tier stop/breakeven/trail, partial TP, time stop.
Extended with a "signal today" / rating-bucket evaluator.
"""
import numpy as np
import pandas as pd
import yfinance as yf

ATR_LEN = 22
ATR_MULT = 3.0
COMPRESSION_MULT = 1.15
RESISTANCE_LEN = 20
BE_TRIGGER_PCT = 7.9
TRAIL_TIER_PCT = 13.1
TP_TARGET_PCT = 11.0
MAX_BARS = 20
EMA_LEN = 50
VOL_MULT = 1.4
VOL_AVG_LEN = 50


def fetch(ticker: str, period: str = "10y") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.drop(columns=["Adj Close"], errors="ignore").dropna()


def wilder_atr(h, l, c, length):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.copy()
    atr.iloc[:length] = np.nan
    atr.iloc[length] = tr.iloc[1:length + 1].mean()
    for i in range(length + 1, len(tr)):
        atr.iloc[i] = (atr.iloc[i - 1] * (length - 1) + tr.iloc[i]) / length
    return atr


def _verdict(signal_today: bool, in_position: bool, n_trades: int, win_rate: float, pf: float,
             tp_hit: bool = False) -> tuple[str, str]:
    """A real TAKE/SKIP call, not a vague confidence bucket -- based on whether
    THIS ticker's own backtested history with THIS strategy actually shows an
    edge, not just whether a signal fired."""
    if in_position:
        if tp_hit:
            return "TP HIT", "partial take-profit already triggered on the open position"
        return "IN TRADE", "a position from a prior signal is still open"
    if not signal_today:
        return "NO SIGNAL", "no entry signal on the latest close"
    if n_trades < 5:
        return "SKIP", f"only {n_trades} historical trades on this ticker -- not enough data to trust the signal"
    if pf >= 1.5 and win_rate >= 40:
        return "TAKE", f"{n_trades} trades historically, {win_rate:.1f}% WR, PF {pf:.2f} -- real edge on this ticker"
    return "SKIP", f"{n_trades} trades historically, {win_rate:.1f}% WR, PF {pf:.2f} -- no real edge on this ticker"


def evaluate(ticker: str) -> dict:
    df = fetch(ticker)
    if len(df) < 250:
        raise ValueError("insufficient history")

    c, h, l, o, v = df.Close, df.High, df.Low, df.Open, df.Volume
    atr = wilder_atr(h, l, c, ATR_LEN)
    atr_avg = atr.rolling(100).mean()
    ema50 = c.ewm(span=EMA_LEN, adjust=False).mean()
    resistance = h.rolling(RESISTANCE_LEN).max().shift(1)
    vol_avg = v.rolling(VOL_AVG_LEN).mean()

    compressed = atr <= atr_avg * COMPRESSION_MULT
    macro_bullish = c > ema50
    volume_confirmed = v >= VOL_MULT * vol_avg
    breakout = (c > resistance) & compressed & volume_confirmed

    trades = []
    position = None
    pending_tp_at = None
    pending_exit_at = None

    for i in range(1, len(df)):
        if position is not None and pending_tp_at == i:
            position["tp_half_hit"] = True
            position["half_pnl_pct"] = (o.iloc[i] - position["entry_price"]) / position["entry_price"] * 100
            pending_tp_at = None

        if position is not None and pending_exit_at is not None and pending_exit_at == i:
            entry_price = position["entry_price"]
            exit_price = o.iloc[i]
            final_pnl_pct = (exit_price - entry_price) / entry_price * 100
            blended = (0.5 * position["half_pnl_pct"] + 0.5 * final_pnl_pct
                       if position["tp_half_hit"] else final_pnl_pct)
            trades.append({"pnl_pct": round(float(blended), 2), "entry_i": position["entry_i"]})
            position = None
            pending_exit_at = None
            continue

        if position is None and breakout.iloc[i - 1] and not pd.isna(atr.iloc[i - 1]):
            entry_price = o.iloc[i]
            entry_atr = atr.iloc[i]
            position = {"entry_i": i, "entry_price": entry_price,
                        "stop": entry_price - entry_atr * ATR_MULT,
                        "high_since": h.iloc[i], "be_activated": False, "tp_half_hit": False,
                        "half_pnl_pct": None}
            continue

        if position is None:
            continue

        position["high_since"] = max(position["high_since"], h.iloc[i])
        entry_price = position["entry_price"]
        max_gain_pct = (position["high_since"] - entry_price) / entry_price * 100
        cur_atr = atr.iloc[i]
        close_i = c.iloc[i]
        bars_in_trade = i - position["entry_i"]

        if max_gain_pct >= BE_TRIGGER_PCT and not position["be_activated"]:
            position["stop"] = entry_price
            position["be_activated"] = True
        if position["tp_half_hit"] or max_gain_pct >= TRAIL_TIER_PCT:
            position["stop"] = max(position["stop"], close_i - cur_atr * 2.5)
        elif position["be_activated"]:
            position["stop"] = max(position["stop"], max(entry_price, close_i - cur_atr * 4.5))
        else:
            position["stop"] = max(position["stop"], entry_price - cur_atr * ATR_MULT)

        if max_gain_pct >= TP_TARGET_PCT and not position["tp_half_hit"] and pending_tp_at is None:
            pending_tp_at = i + 1

        stopped = close_i < position["stop"] or l.iloc[i] < position["stop"]
        time_stop = (bars_in_trade >= MAX_BARS and close_i <= entry_price and not macro_bullish.iloc[i])
        if (stopped or time_stop) and pending_exit_at is None:
            pending_exit_at = i + 1

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = -sum(t["pnl_pct"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100 if trades else 0.0

    signal_today = bool(breakout.iloc[-1]) and position is None
    in_position = position is not None
    tp_hit = bool(position["tp_half_hit"]) if position is not None else False
    verdict, verdict_reason = _verdict(signal_today, in_position, len(trades), wr, pf, tp_hit)
    first_trade_date = df.index[trades[0]["entry_i"]].strftime("%Y-%m") if trades else None

    return {
        "signal_today": signal_today,
        "in_position": in_position,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "n_trades": len(trades),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "first_trade_date": first_trade_date,
    }

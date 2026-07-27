"""
VCP Master (pines/vcp.pine, fixed + volume-confirmed) ported for the dashboard.
Mirrors test_vcp.py's validated logic: ATR compression + 20-bar breakout +
volume confirmation, multi-tier stop/breakeven/trail, partial TP, time stop.
Reads from the shared raw-data cache (webapp/data.py) and extends it with a
"signal today" / TAKE-SKIP verdict evaluator.
"""
import numpy as np
import pandas as pd

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
# Risk-based position sizing, matching vcp.pine's strategy() declaration and risk_pct default.
INITIAL_CAPITAL = 1500.0
RISK_PCT = 1.0
# Matches vcp.pine's start_date default; data.py fetches a year earlier purely for indicator warm-up.
ENTRY_START = pd.Timestamp("2022-01-01")


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
    """A real TAKE/SKIP call, based on whether this ticker's own backtested history shows an edge."""
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


def compute_indicators(df: pd.DataFrame) -> dict:
    """Indicator series, independent of run()'s overridable params."""
    c, h, l, v = df.Close, df.High, df.Low, df.Volume
    atr = wilder_atr(h, l, c, ATR_LEN)
    return dict(
        atr=atr,
        atr_avg=atr.rolling(100).mean(),
        ema50=c.ewm(span=EMA_LEN, adjust=False).mean(),
        resistance=h.rolling(RESISTANCE_LEN).max().shift(1),
        vol_avg=v.rolling(VOL_AVG_LEN).mean(),
    )


def run(df: pd.DataFrame, ind: dict, atr_mult=ATR_MULT, be_trigger_pct=BE_TRIGGER_PCT,
        trail_tier_pct=TRAIL_TIER_PCT, tp_target_pct=TP_TARGET_PCT, vol_mult=VOL_MULT,
        max_bars=MAX_BARS, risk_pct=RISK_PCT, initial_capital=INITIAL_CAPITAL):
    """Returns (trades, signal_today, in_position, tp_hit, open_position)."""
    c, h, l, o, v = df.Close, df.High, df.Low, df.Open, df.Volume
    atr, atr_avg, ema50, resistance, vol_avg = (ind["atr"], ind["atr_avg"], ind["ema50"],
                                                  ind["resistance"], ind["vol_avg"])

    compressed = atr <= atr_avg * COMPRESSION_MULT
    macro_bullish = c > ema50
    volume_confirmed = v >= vol_mult * vol_avg
    breakout = (c > resistance) & compressed & volume_confirmed

    trades = []
    position = None
    pending_tp_at = None
    pending_exit_at = None
    equity = initial_capital

    for i in range(1, len(df)):
        if position is not None and pending_tp_at == i:
            position["tp_half_hit"] = True
            fill_price = o.iloc[i]
            half_pnl_pct = (fill_price - position["entry_price"]) / position["entry_price"] * 100
            leg_qty = position["qty"] * 0.5
            dollar_pnl = leg_qty * (fill_price - position["entry_price"])
            equity += dollar_pnl
            # Recorded as its own trade, same as TradingView's List of Trades splits TP-half from final close.
            trades.append({"pnl_pct": round(float(half_pnl_pct), 2), "dollar_pnl": float(dollar_pnl),
                            "entry_i": position["entry_i"], "days": i - position["entry_i"]})
            pending_tp_at = None

        if position is not None and pending_exit_at is not None and pending_exit_at == i:
            entry_price = position["entry_price"]
            exit_price = o.iloc[i]
            final_pnl_pct = (exit_price - entry_price) / entry_price * 100
            leg_qty = position["qty"] * 0.5 if position["tp_half_hit"] else position["qty"]
            dollar_pnl = leg_qty * (exit_price - entry_price)
            equity += dollar_pnl
            # MAE over the position's full lifetime, attached only here (not the TP-half leg) to avoid double counting.
            mae_pct = (entry_price - position["low_since"]) / entry_price * 100
            trades.append({"pnl_pct": round(float(final_pnl_pct), 2), "dollar_pnl": float(dollar_pnl),
                            "entry_i": position["entry_i"], "days": i - position["entry_i"],
                            "mae_pct": round(float(mae_pct), 2)})
            position = None
            pending_exit_at = None
            continue

        if (position is None and breakout.iloc[i - 1] and not pd.isna(atr.iloc[i - 1])
                and df.index[i - 1] >= ENTRY_START):
            entry_price = o.iloc[i]
            entry_atr = atr.iloc[i]
            stop_distance = entry_atr * atr_mult
            # Risk-based sizing, matching vcp.pine's risk_qty: every trade risks the same % of equity.
            qty = (equity * risk_pct / 100) / stop_distance if stop_distance > 0 else 0.0
            position = {"entry_i": i, "entry_price": entry_price, "qty": qty,
                        "stop": entry_price - stop_distance,
                        "high_since": h.iloc[i], "low_since": l.iloc[i],
                        "be_activated": False, "tp_half_hit": False}
            # No `continue` here -- Pine evaluates breakeven/trail/TP/stop on the entry-fill bar itself too.

        if position is None:
            continue

        position["high_since"] = max(position["high_since"], h.iloc[i])
        position["low_since"] = min(position["low_since"], l.iloc[i])
        entry_price = position["entry_price"]
        max_gain_pct = (position["high_since"] - entry_price) / entry_price * 100
        cur_atr = atr.iloc[i]
        close_i = c.iloc[i]
        bars_in_trade = i - position["entry_i"]

        if max_gain_pct >= be_trigger_pct and not position["be_activated"]:
            position["stop"] = entry_price
            position["be_activated"] = True
        if position["tp_half_hit"] or max_gain_pct >= trail_tier_pct:
            position["stop"] = max(position["stop"], close_i - cur_atr * 2.5)
        elif position["be_activated"]:
            position["stop"] = max(position["stop"], max(entry_price, close_i - cur_atr * 4.5))
        else:
            position["stop"] = max(position["stop"], entry_price - cur_atr * atr_mult)

        if max_gain_pct >= tp_target_pct and not position["tp_half_hit"] and pending_tp_at is None:
            pending_tp_at = i + 1

        stopped = close_i < position["stop"] or l.iloc[i] < position["stop"]
        time_stop = (bars_in_trade >= max_bars and close_i <= entry_price and not macro_bullish.iloc[i])
        if (stopped or time_stop) and pending_exit_at is None:
            pending_exit_at = i + 1

    signal_today = bool(breakout.iloc[-1]) and position is None
    in_position = position is not None
    tp_hit = bool(position["tp_half_hit"]) if position is not None else False
    open_position = None
    if position is not None:
        entry_price = position["entry_price"]
        last_close = float(c.iloc[-1])
        target = float(entry_price) * (1 + tp_target_pct / 100)
        open_position = {
            "entry_date": str(df.index[position["entry_i"]].date()),
            "entry_price": round(float(entry_price), 4),
            "target": round(target, 4),
            # Distance from current price to the fixed partial-TP level (vcp.pine's tp_target %).
            "to_tp_pct": round((target / last_close - 1) * 100, 2),
            "days_held": (len(df) - 1) - position["entry_i"],
            "unrealized_pct": round((last_close / entry_price - 1) * 100, 2),
            "mae_pct": round((entry_price - position["low_since"]) / entry_price * 100, 2),
        }
    return trades, signal_today, in_position, tp_hit, open_position


def _summarize(trades: list[dict]) -> dict:
    # Win rate is a plain count, but profit factor is dollar-weighted (trades vary in position size).
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    gross_win = sum(t["dollar_pnl"] for t in wins)
    gross_loss = -sum(t["dollar_pnl"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100 if trades else 0.0
    avg_days = round(sum(t["days"] for t in trades) / len(trades), 1) if trades else None
    # "tp_pct" is a legacy name -- it's the trade's full signed pnl_pct, not just TP exits.
    last5 = [{"days": t["days"], "tp_pct": t["pnl_pct"]} for t in trades[-5:]]

    # mae_pct only exists on the final-close record of each round-trip, so no double counting.
    mae_wins = [t["mae_pct"] for t in wins if t.get("mae_pct") is not None]
    avg_mae_wins_pct = round(sum(mae_wins) / len(mae_wins), 2) if mae_wins else None
    # Share of winners that barely dipped before working (favors entering at market over chasing a limit fill).
    pct_near_zero_mae = (round(sum(1 for m in mae_wins if m < 1.0) / len(mae_wins) * 100, 1)
                          if mae_wins else None)

    # Share of total $ PnL from the single best trade (1.0 sentinel if no closed trade or non-positive total).
    closed_pnls = [t["dollar_pnl"] for t in trades]
    total_pnl = sum(closed_pnls)
    max_trade_pnl_fraction = (max(closed_pnls) / total_pnl
                               if total_pnl > 0 and closed_pnls else 1.0)

    return {"n_trades": len(trades), "win_rate": round(wr, 1), "profit_factor": round(pf, 2),
            "avg_trade_days": avg_days, "last5_trades": last5,
            "avg_mae_wins_pct": avg_mae_wins_pct, "pct_near_zero_mae": pct_near_zero_mae,
            "max_trade_pnl_fraction": round(float(max_trade_pnl_fraction), 4)}


def evaluate(ticker: str, df: pd.DataFrame, ind: dict | None = None) -> dict:
    """ind: optional pre-computed indicators, shared with strategy_vcpo.py's evaluate() to avoid recomputing them."""
    if len(df) < 250:
        raise ValueError("insufficient history")

    if ind is None:
        ind = compute_indicators(df)
    trades, signal_today, in_position, tp_hit, open_position = run(df, ind)

    stats = _summarize(trades)
    verdict, verdict_reason = _verdict(signal_today, in_position, stats["n_trades"],
                                        stats["win_rate"], stats["profit_factor"], tp_hit)
    first_trade_date = df.index[trades[0]["entry_i"]].strftime("%Y-%m") if trades else None

    # in_position not exposed -- derivable from open_position != null (see scoring.py's shared-shape docstring).
    return {
        "signal_today": signal_today,
        "open_position": open_position,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "first_trade_date": first_trade_date,
        **stats,
    }

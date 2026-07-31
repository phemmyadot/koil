"""
VCP Master (pines/vcp.pine, fixed + volume-confirmed) ported for the dashboard.
Mirrors test_vcp.py's validated logic: ATR compression + 20-bar breakout +
volume confirmation, multi-tier stop/breakeven/trail, partial TP, time stop.
Reads from the shared raw-data cache (webapp/data.py) and extends it with a
"signal today" / TAKE-SKIP verdict evaluator.
"""
import pandas as pd

import webapp.strategy_common as common

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


def compute_indicators(df: pd.DataFrame) -> dict:
    """Indicator series, independent of run()'s overridable params."""
    c, h, l, v = df.Close, df.High, df.Low, df.Volume
    atr = common.wilder_atr(h, l, c, ATR_LEN)
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
            leg_qty = position["qty"] * 0.5
            equity += leg_qty * (fill_price - position["entry_price"])
            # Recorded as its own trade, same as TradingView's List of Trades splits TP-half from final close.
            # No mae_pct here -- only the final-close leg gets one, see record_trade()'s docstring.
            common.record_trade(trades, df, position["entry_i"], position["entry_price"], i, fill_price, leg_qty)
            pending_tp_at = None

        if position is not None and pending_exit_at is not None and pending_exit_at == i:
            entry_price = position["entry_price"]
            exit_price = o.iloc[i]
            leg_qty = position["qty"] * 0.5 if position["tp_half_hit"] else position["qty"]
            equity += leg_qty * (exit_price - entry_price)
            mae_pct = (entry_price - position["low_since"]) / entry_price * 100
            mfe_pct = (position["high_since"] - entry_price) / entry_price * 100
            common.record_trade(trades, df, position["entry_i"], entry_price, i, exit_price, leg_qty,
                                 mae_pct, mfe_pct)
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
        target = float(entry_price) * (1 + tp_target_pct / 100)
        mae_pct = (entry_price - position["low_since"]) / entry_price * 100
        open_position = common.build_open_position(df, position["entry_i"], entry_price, target, mae_pct,
                                                     stop=position["stop"])
    return trades, signal_today, in_position, tp_hit, open_position


def evaluate(ticker: str, bars: pd.DataFrame, ind: dict | None = None) -> dict:
    """ind: optional pre-computed indicators, shared with strategy_vcpo.py's evaluate() to avoid recomputing them."""
    return common.evaluate_strategy(ticker, bars, run, compute_indicators, min_bars=250, ind=ind)

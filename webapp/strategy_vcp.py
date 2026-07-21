"""
VCP Master (pines/vcp.pine, fixed + volume-confirmed) ported for the dashboard.
Mirrors test_vcp.py's validated logic: ATR compression + 20-bar breakout +
volume confirmation, multi-tier stop/breakeven/trail, partial TP, time stop.
Reads from the shared raw-data cache (webapp/data.py) and extends it with a
"signal today" / TAKE-SKIP verdict evaluator.
"""
import itertools

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
# Matches vcp.pine's start_date input default and p.py's PortfolioSizedEngine.
# webapp/data.py fetches a year earlier than this purely for indicator
# warm-up (ATR's 100-bar rolling average, etc.) -- without this gate, entries
# would fire on that warm-up year's not-yet-reliable indicators and show up
# as phantom trades TradingView's List of Trades doesn't have.
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


def compute_indicators(df: pd.DataFrame) -> dict:
    """Series independent of the swept params (compression_mult stays fixed,
    not part of the sweep grid, same as this session's earlier sweep_vcp.py)."""
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
        max_bars=MAX_BARS):
    """Returns (trades, signal_today, in_position, tp_hit, open_position).
    Parameters default to the validated baseline but can be overridden --
    used by optimize() to sweep configs without mutating shared module state.
    open_position (None unless a position is open) is {"entry_price",
    "target"} -- unlike A/D this strategy DOES place a real partial take-
    profit at tp_target_pct, so target here is that actual order level."""
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

    for i in range(1, len(df)):
        if position is not None and pending_tp_at == i:
            position["tp_half_hit"] = True
            half_pnl_pct = (o.iloc[i] - position["entry_price"]) / position["entry_price"] * 100
            # Recorded as its own trade, same as TradingView's List of Trades
            # splits the TP-half fill and the final close into two rows for
            # the one entry -- not blended into a single combined trade.
            trades.append({"pnl_pct": round(float(half_pnl_pct), 2), "entry_i": position["entry_i"],
                            "days": i - position["entry_i"]})
            pending_tp_at = None

        if position is not None and pending_exit_at is not None and pending_exit_at == i:
            entry_price = position["entry_price"]
            exit_price = o.iloc[i]
            final_pnl_pct = (exit_price - entry_price) / entry_price * 100
            trades.append({"pnl_pct": round(float(final_pnl_pct), 2), "entry_i": position["entry_i"],
                            "days": i - position["entry_i"]})
            position = None
            pending_exit_at = None
            continue

        if (position is None and breakout.iloc[i - 1] and not pd.isna(atr.iloc[i - 1])
                and df.index[i - 1] >= ENTRY_START):
            entry_price = o.iloc[i]
            entry_atr = atr.iloc[i]
            position = {"entry_i": i, "entry_price": entry_price,
                        "stop": entry_price - entry_atr * atr_mult,
                        "high_since": h.iloc[i], "be_activated": False, "tp_half_hit": False}
            # No `continue` here -- Pine evaluates breakeven/trail/TP/stop on
            # the entry-fill bar itself too (the fill happens at this bar's
            # open, before the rest of the bar's script runs, so
            # position_size is already >0 for the whole bar). Falls through
            # to Stage 4 below using this same bar's own high/low/close.

        if position is None:
            continue

        position["high_since"] = max(position["high_since"], h.iloc[i])
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
        open_position = {
            "entry_price": round(float(entry_price), 4),
            "target": round(float(entry_price) * (1 + tp_target_pct / 100), 4),
            "days_held": (len(df) - 1) - position["entry_i"],
            "unrealized_pct": round((last_close / entry_price - 1) * 100, 2),
        }
    return trades, signal_today, in_position, tp_hit, open_position


BASELINE_CONFIG = dict(atr_mult=ATR_MULT, be_trigger_pct=BE_TRIGGER_PCT,
                       trail_tier_pct=TRAIL_TIER_PCT, vol_mult=VOL_MULT)

OPTIMIZE_GRID = dict(
    atr_mult=[2.0, 2.5, 3.0],
    be_trigger_pct=[5.0, 7.9, 10.0],
    trail_tier_pct=[10.0, 13.1, 16.0],
    vol_mult=[1.0, 1.4, 1.8],
)


def _summarize(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = -sum(t["pnl_pct"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100 if trades else 0.0
    avg_days = round(sum(t["days"] for t in trades) / len(trades), 1) if trades else None
    last5 = [{"days": t["days"], "tp_pct": t["pnl_pct"]} for t in trades[-5:]]
    return {"n_trades": len(trades), "win_rate": round(wr, 1), "profit_factor": round(pf, 2),
            "avg_trade_days": avg_days, "last5_trades": last5}


def optimize(ticker: str, df: pd.DataFrame, train_frac: float = 0.7, min_trades_per_split: int = 3) -> dict | None:
    """Per-ticker parameter sweep, time-split train/holdout. Returns None if
    no config clears the minimum trade count on both slices."""
    if len(df) < 300:
        return None
    ind = compute_indicators(df)
    split_i = int(len(df) * train_frac)

    keys = list(OPTIMIZE_GRID.keys())
    best = None
    for combo in itertools.product(*OPTIMIZE_GRID.values()):
        cfg = dict(zip(keys, combo))
        trades, _, _, _, open_position = run(df, ind, **cfg)
        train_trades = [t for t in trades if t["entry_i"] < split_i]
        holdout_trades = [t for t in trades if t["entry_i"] >= split_i]
        if len(train_trades) < min_trades_per_split or len(holdout_trades) < min_trades_per_split:
            continue
        st, sh = _summarize(train_trades), _summarize(holdout_trades)
        st["first_trade_date"] = (df.index[train_trades[0]["entry_i"]].strftime("%Y-%m")
                                    if train_trades else None)
        sh["first_trade_date"] = (df.index[holdout_trades[0]["entry_i"]].strftime("%Y-%m")
                                    if holdout_trades else None)
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
    if len(df) < 250:
        raise ValueError("insufficient history")

    ind = compute_indicators(df)
    trades, signal_today, in_position, tp_hit, open_position = run(df, ind)

    stats = _summarize(trades)
    verdict, verdict_reason = _verdict(signal_today, in_position, stats["n_trades"],
                                        stats["win_rate"], stats["profit_factor"], tp_hit)
    first_trade_date = df.index[trades[0]["entry_i"]].strftime("%Y-%m") if trades else None

    return {
        "signal_today": signal_today,
        "in_position": in_position,
        "open_position": open_position,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "first_trade_date": first_trade_date,
        **stats,
    }

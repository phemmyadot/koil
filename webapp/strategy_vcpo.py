"""
VCPO (pines/vcpo.pine) ported for the dashboard. Same ATR compression +
20-bar breakout + multi-tier stop/breakeven/trail + partial TP + time stop as
strategy_vcp.py, but the breakout condition drops volume confirmation --
vcpo.pine has no vol_mult/volume-average gate at all.
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
# Matches vcpo.pine's strategy() declaration (initial_capital=1500,
# default_qty_value=33.33, default_qty_type=percent_of_equity, compounding).
# Profit factor is a dollar-weighted metric, so sizing has to be modeled for
# it to match TradingView's number, even though price-return-per-trade
# (validated separately) doesn't need it.
INITIAL_CAPITAL = 1500.0
PCT_EQUITY = 33.33
# Matches vcpo.pine's start_date input default and p.py's PortfolioSizedEngine.
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
    not part of the sweep grid)."""
    c, h, l = df.Close, df.High, df.Low
    atr = wilder_atr(h, l, c, ATR_LEN)
    return dict(
        atr=atr,
        atr_avg=atr.rolling(100).mean(),
        ema50=c.ewm(span=EMA_LEN, adjust=False).mean(),
        resistance=h.rolling(RESISTANCE_LEN).max().shift(1),
    )


def run(df: pd.DataFrame, ind: dict, atr_mult=ATR_MULT, be_trigger_pct=BE_TRIGGER_PCT,
        trail_tier_pct=TRAIL_TIER_PCT, tp_target_pct=TP_TARGET_PCT, max_bars=MAX_BARS,
        pct_equity=PCT_EQUITY, initial_capital=INITIAL_CAPITAL):
    """Returns (trades, signal_today, in_position, tp_hit, open_position).
    Parameters default to the validated baseline but can be overridden --
    used by optimize() to sweep configs without mutating shared module state.
    open_position (None unless a position is open) is {"entry_price",
    "target"} -- unlike A/D this strategy DOES place a real partial take-
    profit at tp_target_pct, so target here is that actual order level."""
    c, h, l, o = df.Close, df.High, df.Low, df.Open
    atr, atr_avg, ema50, resistance = ind["atr"], ind["atr_avg"], ind["ema50"], ind["resistance"]

    compressed = atr <= atr_avg * COMPRESSION_MULT
    macro_bullish = c > ema50
    breakout = (c > resistance) & compressed

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
            # Recorded as its own trade, same as TradingView's List of Trades
            # splits the TP-half fill and the final close into two rows for
            # the one entry -- not blended into a single combined trade.
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
            # MAE over the position's FULL lifetime (entry to this final close),
            # not just this leg -- attached only here, not on the TP-half leg
            # record, so _summarize()'s wins-only average counts one MAE value
            # per logical round-trip rather than double-counting a trade that
            # got split into two rows.
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
            # Fixed percent-of-equity sizing, matching vcpo.pine's default
            # qty_type/qty_value -- position size is a flat % of current
            # equity, compounding, unlike vcp.pine's risk-based sizing.
            qty = (equity * pct_equity / 100) / entry_price
            position = {"entry_i": i, "entry_price": entry_price, "qty": qty,
                        "stop": entry_price - entry_atr * atr_mult,
                        "high_since": h.iloc[i], "low_since": l.iloc[i],
                        "be_activated": False, "tp_half_hit": False}
            # No `continue` here -- Pine evaluates breakeven/trail/TP/stop on
            # the entry-fill bar itself too (the fill happens at this bar's
            # open, before the rest of the bar's script runs, so
            # position_size is already >0 for the whole bar). Falls through
            # to Stage 4 below using this same bar's own high/low/close.

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
        open_position = {
            "entry_price": round(float(entry_price), 4),
            "target": round(float(entry_price) * (1 + tp_target_pct / 100), 4),
            "days_held": (len(df) - 1) - position["entry_i"],
            "unrealized_pct": round((last_close / entry_price - 1) * 100, 2),
            "mae_pct": round((entry_price - position["low_since"]) / entry_price * 100, 2),
        }
    return trades, signal_today, in_position, tp_hit, open_position


BASELINE_CONFIG = dict(atr_mult=ATR_MULT, be_trigger_pct=BE_TRIGGER_PCT,
                       trail_tier_pct=TRAIL_TIER_PCT)

OPTIMIZE_GRID = dict(
    atr_mult=[2.0, 2.5, 3.0],
    be_trigger_pct=[5.0, 7.9, 10.0],
    trail_tier_pct=[10.0, 13.1, 16.0],
)


def _summarize(trades: list[dict]) -> dict:
    # Win rate is a plain count (unweighted, matching TradingView), but
    # profit factor is dollar-weighted (Gross Profit $ / Gross Loss $) --
    # trades compound in size as equity grows/shrinks, so summing raw
    # pnl_pct would implicitly treat every trade as equally sized, which
    # doesn't match TradingView's actual (size-weighted) profit factor.
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    gross_win = sum(t["dollar_pnl"] for t in wins)
    gross_loss = -sum(t["dollar_pnl"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100 if trades else 0.0
    avg_days = round(sum(t["days"] for t in trades) / len(trades), 1) if trades else None
    # "tp_pct" is a legacy name -- it's the trade's full signed pnl_pct
    # (losses included, not just TP exits). Kept as-is since frontend/backend
    # already agree on it and it's read in several places (last5(), scoring).
    last5 = [{"days": t["days"], "tp_pct": t["pnl_pct"]} for t in trades[-5:]]

    # mae_pct only exists on the FINAL-close trade record of each logical
    # round-trip (see run()), so this is one value per real trade even
    # though a TP-half split produces two rows -- no double counting.
    mae_wins = [t["mae_pct"] for t in wins if t.get("mae_pct") is not None]
    avg_mae_wins_pct = round(sum(mae_wins) / len(mae_wins), 2) if mae_wins else None
    # Share of winners that barely dipped before working -- a high value
    # here means waiting for a pullback before entering is often a mistake;
    # take the signal at market instead of chasing a limit fill.
    pct_near_zero_mae = (round(sum(1 for m in mae_wins if m < 1.0) / len(mae_wins) * 100, 1)
                          if mae_wins else None)

    # Share of total $ PnL contributed by the single best trade -- 1.0 (worst
    # case, fails the >35% concentration check) when there's no closed trade
    # or total PnL is non-positive, since "no track record of distributed
    # wins" should score the same as "one outlier carried everything."
    closed_pnls = [t["dollar_pnl"] for t in trades]
    total_pnl = sum(closed_pnls)
    max_trade_pnl_fraction = (max(closed_pnls) / total_pnl
                               if total_pnl > 0 and closed_pnls else 1.0)

    return {"n_trades": len(trades), "win_rate": round(wr, 1), "profit_factor": round(pf, 2),
            "avg_trade_days": avg_days, "last5_trades": last5,
            "avg_mae_wins_pct": avg_mae_wins_pct, "pct_near_zero_mae": pct_near_zero_mae,
            "max_trade_pnl_fraction": round(float(max_trade_pnl_fraction), 4)}


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


def evaluate(ticker: str, df: pd.DataFrame, ind: dict | None = None) -> dict:
    """ind: optional pre-computed indicators (VCP's compute_indicators() is a
    safe superset -- see app.py's _compute_one). Computed here if omitted."""
    if len(df) < 250:
        raise ValueError("insufficient history")

    if ind is None:
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

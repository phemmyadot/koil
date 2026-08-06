"""Code shared by strategy_vexh.py/strategy_vcp.py/strategy_vcpo.py -- each's evaluate() returns the same shape:
n_trades, win_rate, profit_factor, avg_trade_days, last5_trades, avg_mae_wins_pct, pct_near_zero_mae,
avg_mfe_wins_pct, max_trade_pnl_fraction, signal_today, open_position, verdict, verdict_reason,
first_trade_date. open_position is
{entry_date, entry_price, target, to_tp_pct, unrealized_pct, mae_pct, days_held} or None.

Only each strategy's own run() (the actual trading logic: entry gate, stop/trail/TP rules, time
stop) is allowed to differ -- see STRATEGY_ARCHITECTURE.md.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p import fetch_earnings_dates, earnings_flags_from_dates  # noqa: E402
import backend.db as db  # noqa: E402

# Bounds yf.Ticker.get_earnings_dates(), which has no built-in timeout and can hang the whole batch.
_EARNINGS_FETCH_TIMEOUT = 8
_earnings_executor = ThreadPoolExecutor(max_workers=8)


def cached_earnings_dates(ticker: str) -> pd.DatetimeIndex:
    """24h-cached earnings dates, persisted as a per-ticker DB upsert."""
    cached = db.get_earnings_dates(ticker)
    if cached is not None:
        return cached
    future = _earnings_executor.submit(fetch_earnings_dates, ticker)
    try:
        dates = future.result(timeout=_EARNINGS_FETCH_TIMEOUT)
    except (FutureTimeoutError, Exception):  # noqa: BLE001 - never let one ticker hang the batch
        dates = pd.DatetimeIndex([])
    db.upsert_earnings_dates(ticker, dates, time.time())
    return dates


def with_earnings_flags(bars: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Attach earnings-window flag columns to a copy of the shared raw OHLCV cache. Called for
    every strategy, unconditionally -- even strategies whose run() doesn't read these columns
    get the same input shape as strategies that do."""
    df = bars.copy()
    df["EarningsWithinAvoidWindow"], df["EarningsImminent"] = earnings_flags_from_dates(
        df.index, cached_earnings_dates(ticker))
    return df


def days_to_earnings(ticker: str, today: pd.Timestamp) -> int | None:
    """Calendar days to the next known earnings date on/after today, from the same cached
    source as with_earnings_flags's EarningsWithinAvoidWindow (21-day) flag -- this is just a
    countdown view of the same underlying dates, not a new fetch or a new threshold. None if no
    upcoming earnings date is known."""
    dates = cached_earnings_dates(ticker)
    upcoming = dates[dates >= today]
    if upcoming.empty:
        return None
    return int((upcoming.min() - today).days)


def wilder_atr(h, l, c, length):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.copy()
    atr.iloc[:length] = float("nan")
    atr.iloc[length] = tr.iloc[1:length + 1].mean()
    for i in range(length + 1, len(tr)):
        atr.iloc[i] = (atr.iloc[i - 1] * (length - 1) + tr.iloc[i]) / length
    return atr


def record_trade(trades: list[dict], df: pd.DataFrame, entry_bar: int, entry_price: float,
                  exit_bar: int, exit_price: float, qty: float, mae_pct: float | None = None,
                  mfe_pct: float | None = None, commission_rate: float = 0.0) -> None:
    """Appends one closed-trade record. dollar_pnl = qty * (exit_price - entry_price) --
    strategies with real position sizing (VCP/VCPO) pass their actual qty for a real
    dollar_pnl; a strategy with no sizing model (VEXH) passes qty=1/entry_price so
    dollar_pnl numerically equals the trade's percent return, since summarize()'s PF/
    gross-win/gross-loss math is dollar_pnl-based and must still match a percent-return-based
    PF for a strategy that has no real dollar sizing to begin with. commission_rate
    (0.0 default) is subtracted from pnl_pct on both entry and exit -- only strategies that
    model commission (VEXH) pass a nonzero rate. mae_pct/mfe_pct (max adverse/favorable
    excursion) are both omitted (None) for a partial leg of a round-trip (e.g. VCP/VCPO's
    TP-half fill) -- only the final-close leg gets them, so summarize() counts one value per
    real trade, not one per row."""
    commission_pct = commission_rate * (entry_price + exit_price) / entry_price
    pnl_pct = (exit_price / entry_price - 1) * 100 - commission_pct * 100
    # Derived from pnl_pct (not a separate exit_price - entry_price computation) so commission
    # is always reflected in dollar_pnl too -- summarize()'s PF/gross-win/gross-loss math is
    # dollar_pnl-based and must never silently drop commission for a commission-modeling strategy.
    dollar_pnl = qty * entry_price * (pnl_pct / 100)
    trade = {
        "entry_bar": entry_bar,
        "entry_date": df.index[entry_bar],
        "entry_price": entry_price,
        "exit_bar": exit_bar,
        "exit_date": df.index[exit_bar],
        "exit_price": exit_price,
        "pnl_pct": round(float(pnl_pct), 2),
        "dollar_pnl": float(dollar_pnl),
        "days": (df.index[exit_bar] - df.index[entry_bar]).days,
    }
    if mae_pct is not None:
        trade["mae_pct"] = round(float(mae_pct), 2)
    if mfe_pct is not None:
        trade["mfe_pct"] = round(float(mfe_pct), 2)
    trades.append(trade)


def summarize(trades: list[dict]) -> dict:
    """PF/WR/avg-days/last-5/MAE/outlier-fraction, always computed off dollar_pnl -- every
    trade has it (record_trade() guarantees this), so there's no separate percent-only path."""
    if not trades:
        return {"n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_trade_days": None,
                "last5_trades": [], "avg_mae_wins_pct": None, "pct_near_zero_mae": None,
                "avg_mfe_wins_pct": None, "max_trade_pnl_fraction": 1.0}

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    gross_win = sum(t["dollar_pnl"] for t in wins)
    gross_loss = -sum(t["dollar_pnl"] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(trades) * 100
    avg_days = round(sum(t["days"] for t in trades) / len(trades), 1)
    # "tp_pct" is a legacy name -- it's the trade's full signed pnl_pct, not just TP exits.
    last5 = [{"days": t["days"], "tp_pct": t["pnl_pct"]} for t in trades[-5:]]

    # mae_pct/mfe_pct are absent on a partial (non-final) leg -- see record_trade()'s docstring.
    mae_wins = [t["mae_pct"] for t in wins if t.get("mae_pct") is not None]
    avg_mae_wins_pct = round(sum(mae_wins) / len(mae_wins), 2) if mae_wins else None
    # Share of winners that barely dipped before working (favors entering at market over chasing a limit fill).
    pct_near_zero_mae = (round(sum(1 for m in mae_wins if m < 1.0) / len(mae_wins) * 100, 1)
                          if mae_wins else None)
    # Average max favorable excursion (best unrealized gain reached before exit) across winning
    # trades -- how much upside a winner typically gives up by holding to the actual exit rule
    # rather than the best available price, the mirror image of avg_mae_wins_pct.
    mfe_wins = [t["mfe_pct"] for t in wins if t.get("mfe_pct") is not None]
    avg_mfe_wins_pct = round(sum(mfe_wins) / len(mfe_wins), 2) if mfe_wins else None

    # Share of total $ PnL from the single best trade (1.0 sentinel if non-positive total).
    dollar_pnls = [t["dollar_pnl"] for t in trades]
    total_pnl = sum(dollar_pnls)
    max_trade_pnl_fraction = max(dollar_pnls) / total_pnl if total_pnl > 0 else 1.0

    return {"n_trades": len(trades), "win_rate": round(wr, 1), "profit_factor": round(pf, 2),
            "avg_trade_days": avg_days, "last5_trades": last5,
            "avg_mae_wins_pct": avg_mae_wins_pct, "pct_near_zero_mae": pct_near_zero_mae,
            "avg_mfe_wins_pct": avg_mfe_wins_pct,
            "max_trade_pnl_fraction": round(float(max_trade_pnl_fraction), 4)}


def build_open_position(df: pd.DataFrame, entry_bar: int, entry_price: float, target: float,
                         mae_pct: float, stop: float | None = None) -> dict:
    """The open_position shape, shared by all three strategies. target is the one genuinely
    different input per strategy: VEXH's is the live Bollinger midline, VCP/VCPO's is a fixed
    entry_price * (1 + tp_target_pct) level -- each strategy's run() computes it and hands it in.
    stop is VCP/VCPO's live trailing stop as of the last bar (breakeven/ATR-trail adjusted, not
    just the entry-time value) -- None for VEXH, which has no price-based stop at all (time-stop
    only)."""
    last_close = float(df.Close.iloc[-1])
    days_held = (len(df) - 1) - entry_bar
    return {
        "entry_date": str(df.index[entry_bar].date()),
        "entry_price": round(float(entry_price), 4),
        "target": round(float(target), 4),
        "to_tp_pct": round((target / last_close - 1) * 100, 2),
        "days_held": days_held,
        "unrealized_pct": round((last_close / entry_price - 1) * 100, 2),
        "mae_pct": round(float(mae_pct), 2),
        "stop": round(float(stop), 4) if stop is not None else None,
    }


def verdict(signal_today: bool, in_position: bool, n_trades: int, win_rate: float, pf: float,
            tp_hit: bool = False) -> tuple[str, str]:
    """TAKE/SKIP/NO SIGNAL/IN TRADE/TP HIT, based on whether this ticker's own backtested history shows an edge."""
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


def evaluate_strategy(ticker: str, bars: pd.DataFrame, run_fn, compute_indicators_fn,
                       min_bars: int, ind: dict | None = None) -> dict:
    """Shared evaluate() scaffold: flag earnings, run the strategy, summarize, verdict, assemble.
    run_fn(df, ind) must return (trades, signal_today, in_position, tp_hit, open_position) --
    trades from record_trade(), open_position from build_open_position() or None."""
    df = with_earnings_flags(bars, ticker)
    if df.empty or len(df) < min_bars:
        raise ValueError("no data" if df.empty else "insufficient history")

    if ind is None:
        ind = compute_indicators_fn(df)
    trades, signal_today, in_position, tp_hit, open_position = run_fn(df, ind)

    stats = summarize(trades)
    v, v_reason = verdict(signal_today, in_position, stats["n_trades"], stats["win_rate"],
                           stats["profit_factor"], tp_hit)
    first_trade_date = df.index[trades[0]["entry_bar"]].strftime("%Y-%m") if trades else None

    return {
        "signal_today": signal_today,
        "open_position": open_position,
        "verdict": v,
        "verdict_reason": v_reason,
        "first_trade_date": first_trade_date,
        **stats,
    }

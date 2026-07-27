"""Per-ticker scoring for the exhaustion dashboard.

Uses the exact production logic from p.py (single source of truth) so the
score/open-trade status always matches the validated backtest.

## Shared strategy-stats shape

evaluate()'s "vexh" key, strategy_vcp.py's evaluate(), and strategy_vcpo.py's
evaluate() all return the exact same shape: n_trades, win_rate,
profit_factor, avg_trade_days, last5_trades, avg_mae_wins_pct,
pct_near_zero_mae, max_trade_pnl_fraction, signal_today, open_position,
verdict, verdict_reason, first_trade_date. open_position is {entry_date,
entry_price, target, to_tp_pct, unrealized_pct, mae_pct, days_held} or None.
"""
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p import PortfolioSizedEngine, RSI, ATR, fetch_earnings_dates, earnings_flags_from_dates  # noqa: E402
import webapp.db as db  # noqa: E402
import webapp.vexh_engine as vexh_engine  # noqa: E402

warnings.filterwarnings("ignore", message="Some trades remain open")

E = PortfolioSizedEngine  # production config constants live on the engine class

# Controls whether the legacy per-condition detail dict is exposed/shown; `score` is computed either way.
SHOW_LEGACY_CONDITIONS = os.environ.get("SHOW_LEGACY_CONDITIONS", "true").strip().lower() in ("1", "true", "yes", "on")

# Bounds yf.Ticker.get_earnings_dates(), which has no built-in timeout and can hang the whole batch.
_EARNINGS_FETCH_TIMEOUT = 8
_earnings_executor = ThreadPoolExecutor(max_workers=8)


def _cached_earnings_dates(ticker: str) -> pd.DatetimeIndex:
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


def _with_earnings_flags(bars: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Attach earnings-window flag columns to a copy of the shared raw OHLCV cache."""
    df = bars.copy()
    df["EarningsWithinAvoidWindow"], df["EarningsImminent"] = earnings_flags_from_dates(
        df.index, _cached_earnings_dates(ticker))
    return df


def _trade_history(df: pd.DataFrame) -> tuple[dict | None, float | None, list[dict], dict, bool, str | None]:
    """Native-loop port of backtesting.py's Backtest.run(), validated byte-identical against it.
    Returns (open_position, avg_trade_days, last5_trades, trade_stats, signal_today, first_trade_date)."""
    closed_trades, open_pos, signal_today = vexh_engine.run(df)

    avg_trade_days = (round(sum(t["duration_days"] for t in closed_trades) / len(closed_trades), 1)
                       if closed_trades else None)
    last5_trades = [
        {"tp_pct": round(100 * t["return_pct"], 2), "days": t["duration_days"]}
        for t in closed_trades[-5:]
    ]
    trade_stats = _summarize_trades_native(closed_trades)
    first_trade_date = closed_trades[0]["entry_time"].strftime("%Y-%m") if closed_trades else None

    open_position = None
    if open_pos is not None:
        entry_time = df.index[open_pos["entry_bar"]]
        entry_price = open_pos["entry_price"]
        basis = df.Close.rolling(E.bb_len).mean()
        target = float(basis.loc[entry_time])
        last_close = float(df.Close.iloc[-1])
        days_held = int(df.index.get_loc(df.index[-1]) - open_pos["entry_bar"])
        open_position = {
            "entry_date": str(entry_time.date()),
            "entry_price": round(entry_price, 4),
            "target": round(target, 4),
            "to_tp_pct": round((target / last_close - 1) * 100, 2),
            "days_held": days_held,
            "unrealized_pct": round(100 * (last_close / entry_price - 1), 2),
            "mae_pct": open_pos["mae_pct"],
        }
    return open_position, avg_trade_days, last5_trades, trade_stats, signal_today, first_trade_date


def _summarize_trades_native(closed_trades: list[dict]) -> dict:
    if not closed_trades:
        return {"n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "avg_mae_wins_pct": None, "pct_near_zero_mae": None,
                "max_trade_pnl_fraction": 1.0}
    returns_pct = [t["return_pct"] * 100 for t in closed_trades]
    wins_idx = [i for i, r in enumerate(returns_pct) if r > 0]
    losses = [r for r in returns_pct if r <= 0]
    gross_win = sum(returns_pct[i] for i in wins_idx)
    gross_loss = -sum(losses)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins_idx) / len(returns_pct) * 100

    # Average adverse excursion across winning trades, and share of those wins that barely dipped.
    mae_wins = [closed_trades[i]["mae_pct"] for i in wins_idx]
    avg_mae_wins_pct = round(sum(mae_wins) / len(mae_wins), 2) if mae_wins else None
    pct_near_zero_mae = (round(sum(1 for m in mae_wins if m < 1.0) / len(mae_wins) * 100, 1)
                          if mae_wins else None)

    # Share of total %-return contributed by the single best trade (VEXH has no dollar sizing).
    total_return = sum(returns_pct)
    max_trade_pnl_fraction = (max(returns_pct) / total_return
                               if total_return > 0 else 1.0)

    return {"n_trades": len(returns_pct), "win_rate": round(wr, 1), "profit_factor": round(pf, 2),
            "avg_mae_wins_pct": avg_mae_wins_pct, "pct_near_zero_mae": pct_near_zero_mae,
            "max_trade_pnl_fraction": round(float(max_trade_pnl_fraction), 4)}


def _verdict(signal_today: bool, in_position: bool, n_trades: int, win_rate: float,
             pf: float) -> tuple[str, str]:
    """TAKE/SKIP/NO SIGNAL/IN TRADE, same logic as strategy_vcp.py's _verdict() (no TP HIT state)."""
    if in_position:
        return "IN TRADE", "a position from a prior signal is still open"
    if not signal_today:
        return "NO SIGNAL", "no entry signal on the latest close"
    if n_trades < 5:
        return "SKIP", f"only {n_trades} historical trades on this ticker -- not enough data to trust the signal"
    if pf >= 1.5 and win_rate >= 40:
        return "TAKE", f"{n_trades} trades historically, {win_rate:.1f}% WR, PF {pf:.2f} -- real edge on this ticker"
    return "SKIP", f"{n_trades} trades historically, {win_rate:.1f}% WR, PF {pf:.2f} -- no real edge on this ticker"


def _dist_confidence_tier(dist_atr: float) -> str:
    """LOW/MEDIUM/HIGH confidence tier from distance-above-SMA150 in ATRs, validated on 1,624 closed trades."""
    if dist_atr >= 3.0:
        return "HIGH"
    if dist_atr >= 1.5:
        return "MEDIUM"
    return "LOW"


# Graduated credit for the sma_dist gate instead of a flat 0/1.
_DIST_TIER_CREDIT = {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.0}


def evaluate(ticker: str, bars: pd.DataFrame) -> dict:
    df = _with_earnings_flags(bars, ticker)
    if df.empty or len(df) < E.sma_slow_len + 20:
        raise ValueError("no data" if df.empty else "insufficient history")

    c = df.Close
    price = float(c.iloc[-1])
    sma150 = float(c.rolling(E.sma_slow_len).mean().iloc[-1])
    basis = float(c.rolling(E.bb_len).mean().iloc[-1])
    std = float(c.rolling(E.bb_len).std(ddof=0).iloc[-1])
    lower = basis - E.bb_mult * std
    rsi = RSI(c, E.rsi_len)
    rsi_val = float(rsi.iloc[-1])
    atr = ATR(df.High, df.Low, df.Close, 14)
    atr_val = float(atr.iloc[-1])
    atr_pct = 100 * atr_val / price

    conditions = {
        "trend": {
            "pass": price > sma150,
            "value": f"close {price:.2f} vs SMA150 {sma150:.2f}",
        },
        "band": {
            "pass": price < lower,
            "value": f"close {100 * (price / lower - 1):+.1f}% vs lower band {lower:.2f}",
        },
        "rsi": {
            "pass": rsi_val <= E.rsi_lower,
            "value": round(rsi_val, 1),
        },
        "sma_dist": {
            # E.min_sma_dist_atr == 0 is p.py/Pine's "gate off" mode.
            "pass": E.min_sma_dist_atr == 0 or price >= sma150 + E.min_sma_dist_atr * atr_val,
            "value": f"{(price - sma150) / atr_val:.1f} ATR above SMA" if atr_val > 0 else "n/a",
            "tier": _dist_confidence_tier((price - sma150) / atr_val) if atr_val > 0 else "LOW",
        },
        "vol_ceil": {
            "pass": E.max_atr_pct == 0 or atr_pct <= 100 * E.max_atr_pct,
            "value": f"ATR {atr_pct:.1f}% <= {100 * E.max_atr_pct:.0f}%",
        },
        "vol_floor": {
            "pass": E.min_atr_pct == 0 or atr_pct >= 100 * E.min_atr_pct,
            "value": f"ATR {atr_pct:.1f}% >= {100 * E.min_atr_pct:.0f}%",
        },
    }

    open_position, avg_trade_days, last5_trades, trade_stats, signal_today, first_trade_date = _trade_history(df)

    non_dist_score = sum(1 for k, v in conditions.items() if k != "sma_dist" and v["pass"])
    score = non_dist_score + _DIST_TIER_CREDIT[conditions["sma_dist"]["tier"]]

    verdict, verdict_reason = _verdict(signal_today, open_position is not None,
                                        trade_stats["n_trades"], trade_stats["win_rate"],
                                        trade_stats["profit_factor"])

    vexh_stats = {
        "n_trades": trade_stats["n_trades"],
        "win_rate": trade_stats["win_rate"],
        "profit_factor": trade_stats["profit_factor"],
        "avg_trade_days": avg_trade_days,
        "last5_trades": last5_trades,
        "avg_mae_wins_pct": trade_stats["avg_mae_wins_pct"],
        "pct_near_zero_mae": trade_stats["pct_near_zero_mae"],
        "max_trade_pnl_fraction": trade_stats["max_trade_pnl_fraction"],
        "signal_today": signal_today,
        "open_position": open_position,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "first_trade_date": first_trade_date,
    }

    return {
        "ticker": ticker,
        "price": round(price, 4),
        "date": str(df.index[-1].date()),
        "score": score,
        "conditions": conditions if SHOW_LEGACY_CONDITIONS else None,
        "earnings_risk": bool(df["EarningsWithinAvoidWindow"].iloc[-1]),
        "vexh": vexh_stats,
    }

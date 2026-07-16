"""Per-ticker scoring for the exhaustion dashboard.

Uses the exact production logic from p.py (single source of truth) so the
score/open-trade status always matches the validated backtest.
"""
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import pandas as pd
from backtesting import Backtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p import PortfolioSizedEngine, RSI, ATR, fetch_earnings_dates, earnings_flags_from_dates  # noqa: E402

warnings.filterwarnings("ignore", message="Some trades remain open")

E = PortfolioSizedEngine  # production config constants live on the engine class

# Earnings dates are known well in advance and don't change intraday, unlike price
# data -- a much longer TTL than the 15-min price cache is safe here.
_EARNINGS_CACHE_TTL = 24 * 60 * 60
_earnings_cache: dict[str, tuple[float, pd.DatetimeIndex]] = {}
# yf.Ticker.get_earnings_dates() has no built-in timeout and can hang on a
# given ticker -- since compute_all() bulk-processes hundreds of tickers via
# ThreadPoolExecutor.map(), one hung call would block the ENTIRE batch
# forever (map waits for every result). Bound it with an explicit timeout so
# a single bad ticker degrades to "no earnings data" instead of freezing startup.
_EARNINGS_FETCH_TIMEOUT = 8
_earnings_executor = ThreadPoolExecutor(max_workers=8)


def _cached_earnings_dates(ticker: str) -> pd.DatetimeIndex:
    now = time.time()
    cached = _earnings_cache.get(ticker)
    if cached and now - cached[0] < _EARNINGS_CACHE_TTL:
        return cached[1]
    future = _earnings_executor.submit(fetch_earnings_dates, ticker)
    try:
        dates = future.result(timeout=_EARNINGS_FETCH_TIMEOUT)
    except (FutureTimeoutError, Exception):  # noqa: BLE001 - never let one ticker hang the batch
        dates = pd.DatetimeIndex([])
    _earnings_cache[ticker] = (now, dates)
    return dates


def _with_earnings_flags(bars: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Attach earnings-window flag columns to a copy of the shared raw OHLCV
    cache. Earnings dates have their own 24h cache (they don't change
    intraday), independent of the shared price-data cache in webapp/data.py."""
    df = bars.copy()
    df["EarningsWithinAvoidWindow"], df["EarningsImminent"] = earnings_flags_from_dates(
        df.index, _cached_earnings_dates(ticker))
    return df


def _summarize_trades(closed) -> dict:
    """PF/WR from closed trades' ReturnPct -- same percentage-based formula
    strategy_a.py/strategy_d.py/strategy_vcp.py use, so Exhaustion's baseline
    chip can be colored on the same good/fair/bad scale as the other 3."""
    if len(closed) == 0:
        return {"n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0}
    returns_pct = closed.ReturnPct * 100
    wins = returns_pct[returns_pct > 0]
    losses = returns_pct[returns_pct <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    pf = gross_win / gross_loss if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
    wr = len(wins) / len(returns_pct) * 100
    return {"n_trades": len(returns_pct), "win_rate": round(wr, 1), "profit_factor": round(pf, 2)}


def _trade_history(df: pd.DataFrame) -> tuple[dict | None, float | None, list[dict], dict]:
    """Run the engine with and without finalize_trades; the extra row (if any) is the
    open position. Returns (open_trade, avg_trade_days, last_5_closed_trades, trade_stats)."""
    runs = {}
    for fin in (False, True):
        bt = Backtest(df, PortfolioSizedEngine, cash=10_000, commission=0.001,
                      trade_on_close=True, finalize_trades=fin)
        runs[fin] = bt.run()["_trades"]
    closed, forced = runs[False], runs[True]

    avg_trade_days = (round(float(closed.Duration.dt.days.mean()), 1)
                       if len(closed) else None)
    last5 = closed.tail(5)
    last5_trades = [
        {"tp_pct": round(100 * row.ReturnPct, 2), "days": int(row.Duration.days)}
        for row in last5.itertuples()
    ]
    trade_stats = _summarize_trades(closed)

    open_trade = None
    if len(forced) > len(closed):
        tr = forced.iloc[-1]
        basis = df.Close.rolling(E.bb_len).mean()
        target = float(basis.loc[tr.EntryTime])
        last_close = float(df.Close.iloc[-1])
        bars_held = int(df.index.get_loc(df.index[-1]) - df.index.get_loc(tr.EntryTime))

        macro_ma = df.Close.rolling(E.sma_slow_len).mean()
        atr = ATR(df.High, df.Low, df.Close, 14)
        entry_atr = float(atr.loc[tr.EntryTime])
        entry_dist_atr = ((float(tr.EntryPrice) - float(macro_ma.loc[tr.EntryTime])) / entry_atr
                           if entry_atr > 0 else 0.0)
        entry_tier = _dist_confidence_tier(entry_dist_atr)
        earnings_soon = bool(df["EarningsImminent"].iloc[-1])

        advice, advice_reason = _trade_advice(target, last_close, bars_held, entry_tier, earnings_soon)
        open_trade = {
            "entry_date": str(tr.EntryTime.date()),
            "entry_price": round(float(tr.EntryPrice), 4),
            "target": round(target, 4),
            "bars_held": bars_held,
            "unrealized_pct": round(100 * (last_close / float(tr.EntryPrice) - 1), 2),
            "entry_confidence": entry_tier,
            "advice": advice,
            "advice_reason": advice_reason,
        }
    return open_trade, avg_trade_days, last5_trades, trade_stats


def _trade_advice(target: float, last_close: float, bars_held: int, entry_tier: str,
                   earnings_soon: bool = False) -> tuple[str, str]:
    """TAKE/SKIP call for someone considering entering *now* on an already-open
    signal, based on the same time stop (time_stop_bars) the engine itself uses
    to force-close stale trades, whether the mean-reversion target is already
    spent, the confidence tier the trade actually entered at (see
    _dist_confidence_tier -- LOW-tier entries historically win only ~41-56% of
    the time vs ~73%+ for HIGH-tier, so a LOW-tier open trade is a skip by
    default even with room left on the clock), and whether an earnings report
    is imminent (validated: holding through earnings drops win rate 62%->51%
    and ~triples the big-loser rate -- the live engine would preemptively
    close this position soon anyway, see PortfolioSizedEngine block 1b)."""
    bars_left = E.time_stop_bars - bars_held
    if last_close >= target:
        return "SKIP", "already at/above target -- upside spent"
    if earnings_soon:
        return "SKIP", "earnings report imminent -- engine will preemptively close to avoid gap risk"
    if bars_left <= 3:
        return "SKIP", f"time stop in {bars_left}d -- thesis running out of room"
    if entry_tier == "LOW":
        return "SKIP", f"entered at LOW confidence (<1.5 ATR from SMA) -- historically ~41-56% win rate"
    return "TAKE", f"{bars_left}d left before time stop, entered at {entry_tier} confidence"


def _dist_confidence_tier(dist_atr: float) -> str:
    """LOW/MEDIUM/HIGH confidence tier from distance-above-SMA150 (in ATRs) at
    entry. Backed by 1,624 closed trades across 336 tickers (trades_features.csv
    analysis, validated on a 2022-24/2024-26 chronological split): win rate rises
    from ~41% in the bottom quintile (<1.1 ATR) to ~73% in the top quintile
    (>3.5 ATR). RSI-at-entry and ATR%-at-entry were tested too and rejected --
    RSI showed only a weak effect and ATR% raises variance on both sides
    (bigger winners AND bigger losers) rather than being a quality signal."""
    if dist_atr >= 3.0:
        return "HIGH"
    if dist_atr >= 1.5:
        return "MEDIUM"
    return "LOW"


# The sma_dist gate contributes graduated credit to the score instead of a
# flat 0/1, reflecting the tier's own win-rate research above (LOW ~41-56%,
# HIGH ~73%+) directly in the number shown -- a HIGH-tier entry scores a full
# point, MEDIUM half a point, LOW none, so 6/6 now requires HIGH-tier
# distance, not just clearing the (much lower) 0.5-ATR pass/fail threshold.
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
            # E.min_sma_dist_atr == 0 is a supported "gate off" mode in p.py/Pine
            # (see PortfolioSizedEngine.min_sma_dist_atr's "0 = off" comment) --
            # must short-circuit the same way here or the score would disagree
            # with what the live engine actually does if that constant is ever 0.
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

    open_trade, avg_trade_days, last5_trades, trade_stats = _trade_history(df)

    non_dist_score = sum(1 for k, v in conditions.items() if k != "sma_dist" and v["pass"])
    score = non_dist_score + _DIST_TIER_CREDIT[conditions["sma_dist"]["tier"]]

    return {
        "ticker": ticker,
        "price": round(price, 4),
        "date": str(df.index[-1].date()),
        "score": score,
        "conditions": conditions,
        "to_tp_pct": round(100 * (basis / price - 1), 2),
        # Not one of the 6 score gates (keeps score comparable to the pre-existing
        # UI/semantics) but a real, validated block on live entries in p.py's
        # engine -- a 6/6 score with earnings_risk=True would NOT actually fire
        # a buy in the production backtest.
        "earnings_risk": bool(df["EarningsWithinAvoidWindow"].iloc[-1]),
        "open_trade": open_trade,
        "avg_trade_days": avg_trade_days,
        "last5_trades": last5_trades,
        # PF/WR from closed trades -- lets the dashboard color Exhaustion's
        # BASE chip on the same good/fair/bad scale as strategies A/D/VCP.
        "n_trades": trade_stats["n_trades"],
        "win_rate": trade_stats["win_rate"],
        "profit_factor": trade_stats["profit_factor"],
    }

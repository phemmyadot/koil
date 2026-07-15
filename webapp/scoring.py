"""Per-ticker scoring for the exhaustion dashboard.

Uses the exact production logic from p.py (single source of truth) so the
score/open-trade status always matches the validated backtest.
"""
import os
import sys
import time
import warnings

import pandas as pd
import yfinance as yf
from backtesting import Backtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p import PortfolioSizedEngine, RSI, ATR, fetch_earnings_dates, earnings_flags_from_dates  # noqa: E402

warnings.filterwarnings("ignore", message="Some trades remain open")

E = PortfolioSizedEngine  # production config constants live on the engine class

# Earnings dates are known well in advance and don't change intraday, unlike price
# data -- a much longer TTL than the 15-min price cache is safe here.
_EARNINGS_CACHE_TTL = 24 * 60 * 60
_earnings_cache: dict[str, tuple[float, pd.DatetimeIndex]] = {}


def _cached_earnings_dates(ticker: str) -> pd.DatetimeIndex:
    now = time.time()
    cached = _earnings_cache.get(ticker)
    if cached and now - cached[0] < _EARNINGS_CACHE_TTL:
        return cached[1]
    dates = fetch_earnings_dates(ticker)
    _earnings_cache[ticker] = (now, dates)
    return dates


def fetch(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start="2020-01-01", interval="1d",
                     progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.drop(columns=["Adj Close"], errors="ignore")
    df["EarningsWithinAvoidWindow"], df["EarningsImminent"] = earnings_flags_from_dates(
        df.index, _cached_earnings_dates(ticker))
    return df


def _trade_history(df: pd.DataFrame) -> tuple[dict | None, float | None, list[dict]]:
    """Run the engine with and without finalize_trades; the extra row (if any) is the
    open position. Returns (open_trade, avg_trade_days, last_5_closed_trades)."""
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
    return open_trade, avg_trade_days, last5_trades


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


def evaluate(ticker: str) -> dict:
    df = fetch(ticker)
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
            "pass": price >= sma150 + E.min_sma_dist_atr * atr_val,
            "value": f"{(price - sma150) / atr_val:.1f} ATR above SMA" if atr_val > 0 else "n/a",
            "tier": _dist_confidence_tier((price - sma150) / atr_val) if atr_val > 0 else "LOW",
        },
        "vol_ceil": {
            "pass": atr_pct <= 100 * E.max_atr_pct,
            "value": f"ATR {atr_pct:.1f}% <= {100 * E.max_atr_pct:.0f}%",
        },
        "vol_floor": {
            "pass": atr_pct >= 100 * E.min_atr_pct,
            "value": f"ATR {atr_pct:.1f}% >= {100 * E.min_atr_pct:.0f}%",
        },
    }

    open_trade, avg_trade_days, last5_trades = _trade_history(df)

    return {
        "ticker": ticker,
        "price": round(price, 4),
        "date": str(df.index[-1].date()),
        "score": sum(1 for v in conditions.values() if v["pass"]),
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
    }

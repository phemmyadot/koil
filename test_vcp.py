"""
Python replica of vcp.pine's core technicals (Stage 1 entry, Stage 4 multi-tier
stop/trail/partial-TP, Stage 5 time stop) for testing against real historical
data outside of TradingView.

Mirrors the Pine logic bar-for-bar:
    - ATR(22) compression filter: atr <= 1.15 * SMA(atr, 100)
    - Breakout: close > 20-bar prior high, while compressed
    - EMA(50) macro trend filter (used only by the time-stop override)
    - Multi-tier stop: breakeven at +7.9%, then either the "Dynamic ATR" ladder
      (2.5x ATR once trailing/TP-half hit, 4.5x ATR once breakeven, else initial
      3x ATR) or the "Static %" ladder (Dynamic ATR is vcp.pine's default and
      the one modeled below)
    - Partial TP at +11%, and every exit (ladder-floor stop, time stop): all
      fire via plain market orders (strategy.order / strategy.close) in the
      Pine script, NOT strategy.exit(stop=...). A market order triggered by a
      condition evaluated on bar i's close/low fills at bar i+1's OPEN -- it
      does NOT get a same-bar intrabar fill at the stop/target level the way a
      native stop order would. Modeled here as pending fills that execute on
      the following bar's open (validated: this measurably closed the gap to
      TradingView's matrix vs. assuming same-bar fills at the stop price).
    - Time stop at 20 bars, only if flat/underwater AND macro trend is bearish
    - calc_on_every_tick=true in the Pine script affects realtime/live bar
      recalculation only -- it has no effect on historical backtest results,
      so it's not modeled here.

Reports two views of the same trades:
    - real_trades: one row per actual round trip, blended P&L across the
      partial-TP and final legs. This is the meaningful number for judging
      actual trading performance.
    - pine_legs: one row per strategy.closedtrades entry (partial-TP fills and
      final exits counted separately, each weighted by position size for PF).
      This is what TradingView's matrix (Total Trades / Win Rate) displays.

Usage: .venv/Scripts/python.exe test_vcp.py TICKER [TICKER ...] [--since YYYY-MM-DD]
"""
import sys

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


def fetch(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, period="max", interval="1d", progress=False, auto_adjust=False)
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


def run(ticker: str, since: str | None = None) -> dict:
    df = fetch(ticker)
    if len(df) < 250:
        return {"ticker": ticker, "error": "insufficient history"}

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
    cutoff = pd.Timestamp(since) if since else None

    real_trades = []  # one row per actual round trip (blended P&L)
    pine_legs = []     # one row per strategy.closedtrades entry (matches TV's matrix)
    position = None
    pending_tp_at = None    # bar index the partial-TP order fills at (next open)
    pending_exit_at = None  # (reason, bar index) the exit order fills at (next open)

    for i in range(1, len(df)):
        # ---- fill any pending market orders at THIS bar's open ----
        if position is not None and pending_tp_at == i:
            position["tp_half_hit"] = True
            position["half_pnl_pct"] = (o.iloc[i] - position["entry_price"]) / position["entry_price"] * 100
            pine_legs.append({"days": i - position["entry_i"],
                               "pnl_pct": round(float(position["half_pnl_pct"]), 2), "weight": 0.5})
            pending_tp_at = None

        if position is not None and pending_exit_at is not None and pending_exit_at[1] == i:
            reason, _ = pending_exit_at
            entry_price = position["entry_price"]
            exit_price = o.iloc[i]
            final_pnl_pct = (exit_price - entry_price) / entry_price * 100
            if position["tp_half_hit"]:
                blended_pnl_pct = 0.5 * position["half_pnl_pct"] + 0.5 * final_pnl_pct
            else:
                blended_pnl_pct = final_pnl_pct
            days = i - position["entry_i"]
            real_trades.append({
                "entry_i": position["entry_i"], "exit_i": i, "days": days,
                "pnl_pct": round(float(blended_pnl_pct), 2),
                "tp_half_hit": position["tp_half_hit"], "reason": reason,
            })
            pine_legs.append({"days": days, "pnl_pct": round(float(final_pnl_pct), 2),
                               "weight": 0.5 if position["tp_half_hit"] else 1.0})
            position = None
            pending_exit_at = None
            continue

        # ---- entry (fills next bar's open, matching Pine's default fill timing) ----
        if position is None and breakout.iloc[i - 1] and not pd.isna(atr.iloc[i - 1]):
            if cutoff is not None and df.index[i] < cutoff:
                continue
            entry_price = o.iloc[i]
            entry_atr = atr.iloc[i]
            position = {
                "entry_i": i, "entry_price": entry_price,
                "stop": entry_price - entry_atr * ATR_MULT,
                "high_since": h.iloc[i], "be_activated": False, "tp_half_hit": False,
                "half_pnl_pct": None,
            }
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
            pending_exit_at = ("time_stop" if (time_stop and not stopped) else "stop", i + 1)

    def summarize(rows, rolling_window=20):
        wins = [t for t in rows if t["pnl_pct"] > 0]
        losses = [t for t in rows if t["pnl_pct"] <= 0]
        # PF is dollar-like: weight each leg's % return by its position size
        # (0.5 for a split leg, 1.0 otherwise) so a half-size partial-TP leg
        # doesn't count as if it were the whole position, matching Pine's
        # grossprofit/grossloss (which are real dollar sums, not raw % sums).
        gross_win = sum(t["pnl_pct"] * t.get("weight", 1.0) for t in wins)
        gross_loss = -sum(t["pnl_pct"] * t.get("weight", 1.0) for t in losses)
        pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
        wr = len(wins) / len(rows) * 100 if rows else 0.0
        # vcp.pine's "Avg Days" is a ROLLING window of the last `rolling_window`
        # trade durations (input default 20), not an all-time average.
        recent = rows[-rolling_window:]
        avg_days = sum(t["days"] for t in recent) / len(recent) if recent else 0.0
        return {
            "n_trades": len(rows), "win_rate": round(wr, 1),
            "profit_factor": round(pf, 3) if pf != float("inf") else "inf (no losers)",
            "avg_days": round(avg_days, 1), "avg_days_window": len(recent),
            "last5_pnl_pct": [t["pnl_pct"] for t in rows[-5:]],
        }

    real_stats = summarize(real_trades)
    real_stats["tp_half_rate"] = (round(100 * sum(t["tp_half_hit"] for t in real_trades) / len(real_trades), 1)
                                   if real_trades else 0.0)
    pine_stats = summarize(pine_legs)

    return {"ticker": ticker, "real_trades": real_stats, "pine_legs": pine_stats,
            "real_trades_list": real_trades}


if __name__ == "__main__":
    args = sys.argv[1:]
    since = None
    if "--since" in args:
        idx = args.index("--since")
        since = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    tickers = args or ["MSFT", "TSLA", "AAPL", "NVDA"]
    for tk in tickers:
        try:
            r = run(tk, since=since)
        except Exception as e:  # noqa: BLE001
            r = {"ticker": tk, "error": str(e)}
        print(r)

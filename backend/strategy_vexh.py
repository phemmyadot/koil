"""VEXH (pines/strategy_d_volatility_exhaustion.pine) ported for the dashboard, mirroring
p.py's PortfolioSizedEngine exactly. See STRATEGY_ARCHITECTURE.md for the shared-shape design.

run()'s fill semantics replicate backtesting.py's Broker exactly, without the library's
general-purpose order/broker machinery:
- Entries/closes decided in next() at bar i are queued orders that only fill when
  _process_orders() runs at the START of bar i+1's next() (see backtesting.py's
  Broker.next()/_process_orders()) -- so a decision made from bar i's data becomes visible
  starting bar i+1.
- Market entry AND trade.close() are BOTH non-contingent orders for is_contingent purposes
  (Order.is_contingent only covers SL/TP orders). With trade_on_close=True both therefore
  fill at bar i's close, recorded as if it happened on bar i itself, only becoming visible
  in self.trades/self.orders from bar i+1's next() on.
- tp= resting limit order (a genuinely contingent SL/TP order): live starting bar i+1, fills
  the first bar high >= tp, at price max(open, tp) for that bar (gap-through handling).
- Only one trade open at a time (buy() always sizes off currently-available cash and closes
  are always full closes).

Validated byte-identical (return %, entry/exit dates and prices) against backtesting.py's
Backtest.run() output across the full cached ticker universe.
"""
import pandas as pd

from p import SMA, RSI, ATR, PortfolioSizedEngine as E
import webapp.strategy_common as common

E_COMMISSION_RATE = 0.001


def compute_indicators(df: pd.DataFrame) -> dict:
    c, h, l = df.Close, df.High, df.Low
    return dict(
        macro_ma=SMA(c, E.sma_slow_len),
        bb_basis=SMA(c, E.bb_len),
        std=c.rolling(E.bb_len).std(ddof=0),
        rsi=RSI(c, E.rsi_len),
        atr=ATR(h, l, c, 14),
    )


def run(df: pd.DataFrame, ind: dict):
    """Returns (trades, signal_today, in_position, tp_hit, open_position). tp_hit is always
    False -- VEXH has no partial-take-profit mechanic, unlike VCP/VCPO's tp_half_hit."""
    c, h, l, o = df.Close, df.High, df.Low, df.Open
    macro_ma, bb_basis, std, rsi, atr = ind["macro_ma"], ind["bb_basis"], ind["std"], ind["rsi"], ind["atr"]
    bb_lower = bb_basis - std * E.bb_mult
    earn_avoid = df["EarningsWithinAvoidWindow"]
    earn_imminent = df["EarningsImminent"]

    # Entry gate computed once as a vectorized boolean Series (mirrors strategy_vcp.py's
    # `breakout` pattern) instead of scalar .iloc[] lookups per bar inside the loop.
    macro_bullish_s = c > macro_ma
    bb_exhaustion_s = c < bb_lower
    rsi_washed_out_s = rsi <= E.rsi_lower
    sma_dist_ok_s = True if E.min_sma_dist_atr == 0 else c >= macro_ma + E.min_sma_dist_atr * atr
    vol_ceil_ok_s = True if E.max_atr_pct == 0 else atr / c <= E.max_atr_pct
    vol_floor_ok_s = True if E.min_atr_pct == 0 else atr / c >= E.min_atr_pct
    earnings_ok_s = ~earn_avoid if E.avoid_earnings else True
    entry_after_start_s = df.index >= E.entry_start if E.entry_start is not None else True

    entry_signal = (macro_bullish_s & bb_exhaustion_s & rsi_washed_out_s & sma_dist_ok_s
                     & vol_ceil_ok_s & vol_floor_ok_s & earnings_ok_s & entry_after_start_s)

    macro_ma_arr = macro_ma.to_numpy()
    bb_basis_arr = bb_basis.to_numpy()
    c_arr = c.to_numpy()
    h_arr = h.to_numpy()
    l_arr = l.to_numpy()
    o_arr = o.to_numpy()
    earn_imminent_arr = earn_imminent.to_numpy()
    entry_signal_arr = entry_signal.to_numpy()

    n = len(df)
    trades: list[dict] = []
    position = None  # {"entry_bar", "entry_price", "visible_from", "low_since", "high_since"}
    pending_tp = None  # tp price, live once position opened; checked every bar from entry_bar+1 on

    sma_slow_len = E.sma_slow_len
    avoid_earnings = E.avoid_earnings
    time_stop_bars = E.time_stop_bars

    for i in range(1, n):
        # Running low/high-water-mark for MAE/MFE, updated for every bar the position is open --
        # including the bar it may close on below -- BEFORE any exit check.
        if position is not None:
            position["low_since"] = min(position["low_since"], l_arr[i])
            position["high_since"] = max(position["high_since"], h_arr[i])

        # TP fill check, processed first each bar (mirrors _process_orders running at the
        # START of next(), before this bar's own exit/entry logic).
        if (position is not None and pending_tp is not None and i > position["entry_bar"]
                and h_arr[i] >= pending_tp):
            exit_price = max(o_arr[i], pending_tp)
            mae_pct = (position["entry_price"] - position["low_since"]) / position["entry_price"] * 100
            mfe_pct = (position["high_since"] - position["entry_price"]) / position["entry_price"] * 100
            # qty=1/entry_price makes dollar_pnl equal the trade's percent return (no dollar
            # sizing model here) -- summarize()'s PF/gross-win/gross-loss math is dollar_pnl-based,
            # and matches p.py's PortfolioSizedEngine (backtesting.py sums ReturnPct, not $ price delta).
            common.record_trade(trades, df, position["entry_bar"], position["entry_price"], i, exit_price,
                                 qty=1 / position["entry_price"], mae_pct=mae_pct, mfe_pct=mfe_pct,
                                 commission_rate=E_COMMISSION_RATE)
            position = None
            pending_tp = None
            # Unlike a strategy-decided close (trade.close() inside next(), which always
            # return()s -- see p.py), a TP fill happens in _process_orders() BEFORE next()'s
            # own body runs, so entry logic below still evaluates against this same bar i.

        if i < sma_slow_len:
            continue

        current_price = c_arr[i]

        if position is not None and i > position["entry_bar"]:
            bar_index = i
            # A close decided here is only recorded as filled once processed at bar i+1 --
            # on the last bar, that processing step never happens, so the position stays open.
            if i < n - 1:
                closing = (current_price < macro_ma_arr[i]
                           or (avoid_earnings and earn_imminent_arr[i])
                           or (time_stop_bars > 0 and (bar_index - position["entry_bar"]) >= time_stop_bars))
                if closing:
                    mae_pct = (position["entry_price"] - position["low_since"]) / position["entry_price"] * 100
                    mfe_pct = (position["high_since"] - position["entry_price"]) / position["entry_price"] * 100
                    common.record_trade(trades, df, position["entry_bar"], position["entry_price"], i,
                                         current_price, qty=1 / position["entry_price"], mae_pct=mae_pct,
                                         mfe_pct=mfe_pct, commission_rate=E_COMMISSION_RATE)
                    position = None
                    pending_tp = None
                    continue
            continue
        elif position is not None:
            continue

        if entry_signal_arr[i]:
            # Broker._process_orders(): for a market order with trade_on_close and not
            # contingent, time_index = self._i - 1 -- so the trade is recorded as entered on
            # the signal bar itself (i), even though the fill is only processed (and becomes
            # visible to exit checks) starting the broker's next() call at bar i+1.
            position = {"entry_bar": i, "entry_price": current_price, "visible_from": i + 1,
                        "low_since": l_arr[i], "high_since": h_arr[i]}
            pending_tp = bb_basis_arr[i]

    signal_today = bool(entry_signal_arr[-1]) and position is None
    in_position = position is not None

    open_position = None
    if position is not None and position["visible_from"] <= n - 1:
        entry_price = position["entry_price"]
        target = float(bb_basis_arr[position["entry_bar"]])
        mae_pct = (entry_price - position["low_since"]) / entry_price * 100
        open_position = common.build_open_position(df, position["entry_bar"], entry_price, target, mae_pct)

    return trades, signal_today, in_position, False, open_position


def evaluate(ticker: str, bars: pd.DataFrame, ind: dict | None = None) -> dict:
    return common.evaluate_strategy(ticker, bars, run, compute_indicators, min_bars=E.sma_slow_len + 20, ind=ind)

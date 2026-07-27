"""
Native-loop port of p.py's PortfolioSizedEngine (a backtesting.py Strategy),
replicating backtesting.py's exact order/fill semantics without the
library's general-purpose broker/order machinery:

- Entries/closes decided in next() at bar i are queued orders that only
  fill when _process_orders() runs at the START of bar i+1's next() (see
  backtesting.py's Broker.next()/_process_orders()) -- so a decision made
  from bar i's data becomes visible starting bar i+1.
- Market entry AND trade.close() are BOTH non-contingent orders for
  is_contingent purposes (Order.is_contingent only covers SL/TP orders --
  see Order.is_contingent and the "Order placed by Trade.close()" comment
  in Order.cancel()). With trade_on_close=True both therefore fill at bar
  i's close (Broker._process_orders: is_market_order and trade_on_close
  and not is_contingent -> time_index=self._i-1, price=data.Close[-2] at
  processing time i+1) -- recorded as if it happened on bar i itself, only
  becoming visible in self.trades/self.orders from bar i+1's next() on.
- tp= resting limit order (a genuinely contingent SL/TP order): live
  starting bar i+1 (the bar after entry_bar), fills the first bar
  high >= tp (a sell order closing the long), at price max(open, tp) for
  that bar (gap-through handling).
- Only one trade open at a time here (buy() always sizes off currently-
  available cash and closes are always full closes), so pl_pct simplifies
  to (exit/entry - 1) - commission_pct, per Trade.pl_pct.

Validated byte-identical (ReturnPct/EntryTime/EntryPrice/exit set) against
backtesting.py's Backtest.run() output across the full cached ticker
universe before this replaced it in scoring.py.
"""
import pandas as pd

from p import SMA, RSI, ATR, PortfolioSizedEngine as E


def run(df: pd.DataFrame) -> tuple[list[dict], dict | None]:
    """Returns (closed_trades, open_trade_dict_or_None).
    closed_trades: [{entry_time, entry_price, exit_time, exit_price,
    return_pct, duration_days, mae_pct}], oldest first. mae_pct mirrors
    strategy_vcp.py's own field: (entry_price - low_since) / entry_price *
    100, tracked from entry_bar through the bar the trade closes.
    open_trade_dict: {entry_bar, entry_price, mae_pct} or None.
    """
    c, h, l, o = df.Close, df.High, df.Low, df.Open
    macro_ma = SMA(c, E.sma_slow_len)
    bb_basis = SMA(c, E.bb_len)
    std = c.rolling(E.bb_len).std(ddof=0)
    bb_lower = bb_basis - std * E.bb_mult
    rsi = RSI(c, E.rsi_len)
    atr = ATR(h, l, c, 14)
    earn_avoid = df["EarningsWithinAvoidWindow"]
    earn_imminent = df["EarningsImminent"]

    # Entry gate computed once as a vectorized boolean Series (mirrors
    # strategy_vcp.py's `breakout` pattern) instead of 6+ separate scalar
    # .iloc[] lookups/comparisons per bar inside the loop -- this was the
    # actual bottleneck (~18ms/ticker), not the library-vs-native-loop
    # distinction, since indicator calc alone is only ~1.5ms.
    macro_bullish_s = c > macro_ma
    bb_exhaustion_s = c < bb_lower
    rsi_washed_out_s = rsi <= E.rsi_lower
    sma_dist_ok_s = (True if E.min_sma_dist_atr == 0
                      else c >= macro_ma + E.min_sma_dist_atr * atr)
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

    commission_rate = 0.001
    n = len(df)

    closed_trades: list[dict] = []
    position = None  # {"entry_bar", "entry_price"}
    pending_tp = None  # tp price, live once position opened; checked every bar from entry_bar+1 on

    sma_slow_len = E.sma_slow_len
    avoid_earnings = E.avoid_earnings
    time_stop_bars = E.time_stop_bars

    for i in range(1, n):
        # Running low-water-mark for MAE, updated for every bar the position
        # is open -- including the bar it may close on below -- BEFORE any
        # exit check, same convention as strategy_vcp.py's low_since (updated
        # unconditionally each bar prior to that bar's own exit logic).
        if position is not None:
            position["low_since"] = min(position["low_since"], l_arr[i])

        # --- TP fill check, processed first each bar (mirrors _process_orders
        # running at the START of next(), before this bar's own exit/entry logic) ---
        if (position is not None and pending_tp is not None and i > position["entry_bar"]
                and h_arr[i] >= pending_tp):
            exit_price = max(o_arr[i], pending_tp)
            _close(closed_trades, df, position, i, exit_price, commission_rate)
            position = None
            pending_tp = None
            # Unlike a strategy-decided close (trade.close() inside next(),
            # which always return()s -- see p.py), a TP fill happens in
            # _process_orders() BEFORE next()'s own body runs, so entry
            # logic below still evaluates against this same bar i.

        if i < sma_slow_len:
            continue

        current_price = c_arr[i]

        if position is not None and i > position["entry_bar"]:
            bar_index = i
            # A close decided here is only recorded as filled once processed
            # at bar i+1 (see module docstring) -- on the last bar, that
            # processing step never happens, so the position stays open.
            if i < n - 1:
                if current_price < macro_ma_arr[i]:
                    _close(closed_trades, df, position, i, current_price, commission_rate)
                    position = None
                    pending_tp = None
                    continue
                if avoid_earnings and earn_imminent_arr[i]:
                    _close(closed_trades, df, position, i, current_price, commission_rate)
                    position = None
                    pending_tp = None
                    continue
                if time_stop_bars > 0 and (bar_index - position["entry_bar"]) >= time_stop_bars:
                    _close(closed_trades, df, position, i, current_price, commission_rate)
                    position = None
                    pending_tp = None
                    continue
            continue
        elif position is not None:
            continue

        if entry_signal_arr[i]:
            # Broker._process_orders(): for a market order with trade_on_close
            # and not contingent, time_index = self._i - 1 -- so the trade is
            # recorded as entered on the signal bar itself (i), even though
            # the fill is only processed (and becomes visible to exit checks)
            # starting the broker's next() call at bar i+1.
            position = {"entry_bar": i, "entry_price": current_price, "visible_from": i + 1,
                        "low_since": l_arr[i]}
            pending_tp = bb_basis_arr[i]

    open_trade = None
    if position is not None and position["visible_from"] <= n - 1:
        entry_price = position["entry_price"]
        open_trade = {
            "entry_bar": position["entry_bar"],
            "entry_price": entry_price,
            "mae_pct": round((entry_price - position["low_since"]) / entry_price * 100, 2),
        }

    return closed_trades, open_trade


def _close(closed_trades, df, position, exit_bar, exit_price, commission_rate):
    entry_price = position["entry_price"]
    entry_commission = commission_rate * entry_price
    exit_commission = commission_rate * exit_price
    commission_pct = (entry_commission + exit_commission) / entry_price
    return_pct = (exit_price / entry_price - 1) - commission_pct
    mae_pct = (entry_price - position["low_since"]) / entry_price * 100
    closed_trades.append({
        "entry_bar": position["entry_bar"],
        "entry_time": df.index[position["entry_bar"]],
        "entry_price": entry_price,
        "exit_bar": exit_bar,
        "exit_time": df.index[exit_bar],
        "exit_price": exit_price,
        "return_pct": return_pct,
        "duration_days": (df.index[exit_bar] - df.index[position["entry_bar"]]).days,
        "mae_pct": round(float(mae_pct), 2),
    })

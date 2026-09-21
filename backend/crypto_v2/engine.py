"""Per-ticker orchestration: features -> structure -> liquidity -> setups -> scoring, plus a
deterministic bar-by-bar backtest replay that produces historical stats and today's live
signal(s), if any.

Entry: whichever setup detector fires first on a bar's close is the signal, filled at the next
bar's open -- doc's "close confirmation" model, the same convention strategy_vcp.py already
uses. When more than one setup fires the same bar, _SETUP_PRIORITY breaks the tie --
arbitrary but fixed and disclosed (recorded on the trade), not hidden. Stop: the most recently
confirmed swing low minus an ATR buffer (doc §28's "thesis invalidation," not a flat %) -- one
shared stop basis across all four setup types for this pass, a deliberate simplification (see
setups.py's docstring). Targets: TP1/TP2 at R-multiples of the stop distance (doc §29), 50% off
at TP1 with a breakeven stop move, trailing the remainder via ATR (doc §30) -- the same
mechanics as strategy_vcp.py's run(), just R-multiple-based instead of %-based.
"""
import math

import pandas as pd

import backend.strategy_common as common
from backend.crypto_v2 import config as config_module
from backend.crypto_v2 import features as features_mod
from backend.crypto_v2 import liquidity as liquidity_mod
from backend.crypto_v2 import scoring as scoring_mod
from backend.crypto_v2 import setups as setups_mod
from backend.crypto_v2 import structure as structure_mod

MIN_BARS = 130
_SETUP_PRIORITY = setups_mod.ALL_SETUPS


def _compute_all(df: pd.DataFrame, btc_df: pd.DataFrame | None, eth_df: pd.DataFrame | None, config: dict) -> dict:
    feats = features_mod.compute_features(df, btc_df, eth_df, config)
    swing_high, swing_low = structure_mod.swing_points(df, config["structure"]["swing_left"], config["structure"]["swing_right"])
    bullish_bos, bearish_bos = structure_mod.break_of_structure(df, swing_high, swing_low)
    struct = pd.DataFrame({"swing_high": swing_high, "swing_low": swing_low,
                            "bullish_bos": bullish_bos, "bearish_bos": bearish_bos})
    levels = liquidity_mod.liquidity_levels(df, config["liquidity"])
    liq = pd.concat([levels, liquidity_mod.sweep_signals(df, levels)], axis=1)

    setups = {
        "breakout": setups_mod.breakout(df, feats, swing_high, config),
        "momentum_continuation": setups_mod.momentum_continuation(df, feats, struct, config),
        "liquidity_sweep_reversal": setups_mod.liquidity_sweep_reversal(df, feats, struct, liq, config),
        "range_breakout": setups_mod.range_breakout(df, feats, swing_high, config),
    }
    return {"features": feats, "structure": struct, "liquidity": liq, "setups": setups}


def backtest(df: pd.DataFrame, computed: dict, config: dict) -> tuple[list[dict], dict | None, str | None]:
    """Sequential bar-by-bar replay -- returns (trades, open_position, open_setup_type)."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    feats, struct, setups = computed["features"], computed["structure"], computed["setups"]
    risk_cfg = config["risk"]
    atr = feats["atr"]

    trades: list[dict] = []
    position = None
    pending_tp_at = None
    pending_exit_at = None
    equity = risk_cfg["initial_capital"]

    for i in range(1, len(df)):
        if position is not None and pending_tp_at == i:
            position["tp_half_hit"] = True
            fill_price = o.iloc[i]
            leg_qty = position["qty"] * 0.5
            equity += leg_qty * (fill_price - position["entry_price"])
            common.record_trade(trades, df, position["entry_i"], position["entry_price"], i, fill_price, leg_qty)
            pending_tp_at = None

        if position is not None and pending_exit_at == i:
            entry_price = position["entry_price"]
            exit_price = o.iloc[i]
            leg_qty = position["qty"] * 0.5 if position["tp_half_hit"] else position["qty"]
            equity += leg_qty * (exit_price - entry_price)
            mae_pct = (entry_price - position["low_since"]) / entry_price * 100
            mfe_pct = (position["high_since"] - entry_price) / entry_price * 100
            common.record_trade(trades, df, position["entry_i"], entry_price, i, exit_price, leg_qty, mae_pct, mfe_pct)
            position = None
            pending_exit_at = None
            continue

        if position is None:
            triggered_type = next(
                (name for name in _SETUP_PRIORITY if bool(setups[name][0].iloc[i - 1])), None)
            swing_low_prior = struct["swing_low"].iloc[i - 1]
            if triggered_type is not None and not pd.isna(atr.iloc[i - 1]) and not pd.isna(swing_low_prior):
                entry_price = o.iloc[i]
                entry_atr = atr.iloc[i]
                stop = swing_low_prior - entry_atr * risk_cfg["stop_atr_buffer_mult"]
                # Floored at half an ATR -- a swing low sitting right at (or above) entry would
                # otherwise give a ~0 or negative stop distance (mirrors strategy_vcp.py's own
                # "stop_distance > 0" sizing guard) -- and CEILED at stop_atr_ceiling_mult*ATR,
                # since an unbounded "last confirmed swing low" is frequently very wide on
                # volatile tickers (see config.py's risk.stop_atr_ceiling_mult comment for the
                # pooled-backtest evidence). Without the ceiling, R (the risk unit driving both
                # position size and the tp1_r breakeven/protection trigger) can balloon to
                # multiples of a sane ATR-based stop.
                stop_distance = min(max(entry_price - stop, entry_atr * 0.5), entry_atr * risk_cfg["stop_atr_ceiling_mult"])
                stop = entry_price - stop_distance
                qty = (equity * risk_cfg["risk_pct"] / 100) / stop_distance if stop_distance > 0 else 0.0
                position = {"entry_i": i, "entry_price": entry_price, "qty": qty, "stop": stop,
                            "stop_distance": stop_distance, "setup_type": triggered_type,
                            "high_since": h.iloc[i], "low_since": l.iloc[i],
                            "be_activated": False, "tp_half_hit": False}

        if position is None:
            continue

        position["high_since"] = max(position["high_since"], h.iloc[i])
        position["low_since"] = min(position["low_since"], l.iloc[i])
        entry_price = position["entry_price"]
        r = position["stop_distance"]
        max_gain_r = (position["high_since"] - entry_price) / r if r > 0 else 0.0
        close_i = c.iloc[i]
        cur_atr = atr.iloc[i]
        bars_in_trade = i - position["entry_i"]

        if max_gain_r >= risk_cfg["tp1_r"] and not position["be_activated"]:
            position["stop"] = entry_price
            position["be_activated"] = True
        if position["tp_half_hit"] and not pd.isna(cur_atr):
            position["stop"] = max(position["stop"], close_i - cur_atr * 2.5)
        elif position["be_activated"]:
            position["stop"] = max(position["stop"], entry_price)

        if max_gain_r >= risk_cfg["tp1_r"] and not position["tp_half_hit"] and pending_tp_at is None:
            pending_tp_at = i + 1

        stopped = close_i < position["stop"] or l.iloc[i] < position["stop"]
        time_stop = bars_in_trade >= risk_cfg["max_bars"] and close_i <= entry_price
        if (stopped or time_stop) and pending_exit_at is None:
            pending_exit_at = i + 1

    open_position = None
    open_setup_type = None
    if position is not None:
        entry_price = position["entry_price"]
        target = entry_price + risk_cfg["tp1_r"] * position["stop_distance"]
        mae_pct = (entry_price - position["low_since"]) / entry_price * 100
        open_position = common.build_open_position(df, position["entry_i"], entry_price, target, mae_pct, stop=position["stop"])
        open_setup_type = position["setup_type"]
    return trades, open_position, open_setup_type


def universe_returns_by_ticker(bars_by_ticker: dict[str, pd.DataFrame], lookback_days: int) -> dict[str, float]:
    """Every tracked ticker's own N-day return, gathered up front so each ticker's relative-
    strength percentile can be ranked against the whole set in one pass (see evaluate()'s
    universe_returns arg) instead of each ticker computing this independently."""
    out: dict[str, float] = {}
    for ticker, df in bars_by_ticker.items():
        if df is None or len(df) <= lookback_days:
            continue
        ret = features_mod.own_return(df, lookback_days).iloc[-1]
        if not pd.isna(ret):
            out[ticker] = float(ret)
    return out


def _relative_strength_percentile(asset_return: float | None, universe_returns: list[float]) -> float | None:
    if asset_return is None or not universe_returns:
        return None
    below = sum(1 for r in universe_returns if r < asset_return)
    return round(below / len(universe_returns) * 100, 1)


def _safe_round(value, digits: int):
    """None for NaN/inf/-inf, not just NaN -- a bare pd.isna() check lets +-inf through (a
    division-by-zero feature edge case, e.g. rvol against a zero-volume window), and JSON has
    no representation for it (json.dumps raises ValueError: Out of range float values)."""
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def evaluate(ticker: str, df: pd.DataFrame, btc_df: pd.DataFrame | None, eth_df: pd.DataFrame | None,
             universe_returns: list[float], market_cap: float | None, quote_volume_24h: float | None,
             config: dict | None = None) -> dict:
    config = config or config_module.CONFIG
    if df is None or len(df) < MIN_BARS:
        raise ValueError("insufficient history")

    computed = _compute_all(df, btc_df, eth_df, config)
    trades, open_position, open_setup_type = backtest(df, computed, config)
    stats = common.summarize(trades)

    last_i = len(df) - 1
    feats = computed["features"]
    rs_percentile = _relative_strength_percentile(feats["asset_return"].iloc[last_i], universe_returns)

    signals_today = []
    for name in _SETUP_PRIORITY:
        detected, components_series = computed["setups"][name]
        if bool(detected.iloc[last_i]):
            fired_components = {k: bool(v.iloc[last_i]) for k, v in components_series.items()}
            score, breakdown = scoring_mod.setup_score(fired_components, config["scoring"])
            signals_today.append({"setup_type": name, "side": "LONG", "setup_score": score,
                                   "score_breakdown": breakdown, "components": fired_components})

    feature_snapshot = {
        "close": _safe_round(df["Close"].iloc[last_i], 8),
        "atr_pct": _safe_round(feats["atr_pct"].iloc[last_i], 4),
        "rvol": _safe_round(feats["rvol"].iloc[last_i], 2),
        "rsi": _safe_round(feats["rsi"].iloc[last_i], 2),
        "rs_btc": _safe_round(feats["rs_btc"].iloc[last_i], 4),
        "rs_percentile": rs_percentile,
        "bullish_bos": bool(computed["structure"]["bullish_bos"].iloc[last_i]),
    }

    return {
        "ticker": ticker,
        "date": str(df.index[last_i].date()),
        "price": _safe_round(df["Close"].iloc[last_i], 8),
        "strategy_name": config_module.STRATEGY_NAME,
        "strategy_version": config_module.STRATEGY_VERSION,
        "config_hash": config_module.config_hash(),
        "n_trades": stats["n_trades"],
        "win_rate": stats["win_rate"],
        "profit_factor": stats["profit_factor"],
        "avg_trade_days": stats["avg_trade_days"],
        "open_position": open_position,
        "open_setup_type": open_setup_type,
        "signals_today": signals_today,
        "execution_score": scoring_mod.execution_score(market_cap, quote_volume_24h),
        "feature_snapshot": feature_snapshot,
    }

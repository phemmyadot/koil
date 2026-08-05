"""Single shared default quality filter -- served to the frontend via GET /api/filter-defaults,
also used by strategy-state notifications and the daily review chatbot."""

DEFAULT_FILTER = {
    "strategies": ["vcpo"],
    "min_win_rate": 70,
    "min_profit_factor": 2.5,
    "min_trades": 15,
    "min_phase_score": 2,
    "min_coil_bars": 5,
}

# Short key -> wire key (a ticker's computed payload uses "strategy_vcpo" etc.; VEXH is irregular).
STRATEGY_WIRE_KEY = {"vcp": "strategy_vcp", "vcpo": "strategy_vcpo", "vexh": "vexh"}


def passes_default_filter(payload: dict | None, strat_payload: dict | None) -> bool:
    if payload is None or strat_payload is None:
        return False
    if strat_payload.get("n_trades", 0) < DEFAULT_FILTER["min_trades"]:
        return False
    if strat_payload.get("win_rate", 0) < DEFAULT_FILTER["min_win_rate"]:
        return False
    if strat_payload.get("profit_factor", 0) < DEFAULT_FILTER["min_profit_factor"]:
        return False
    prebreak = payload.get("prebreak")
    if prebreak is None:
        return False
    if prebreak.get("score", 0) < DEFAULT_FILTER["min_phase_score"]:
        return False
    if prebreak.get("squeeze_counter", 0) < DEFAULT_FILTER["min_coil_bars"]:
        return False
    return True

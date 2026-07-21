"""
Per-ticker parameter optimization for strategies A/D/VCP -- runs on its own,
much slower cadence than signal computation (daily, not every 2h), since the
"best config" for a ticker's 10-year history doesn't meaningfully change
hour to hour. Results persist to disk so a server restart doesn't force
re-running the expensive sweep.

This is deliberately kept separate from webapp/app.py's compute_all(): that
function must stay fast (it's the request-facing signal computation), while
this sweep is expensive (hundreds of backtest runs per ticker per strategy)
and only ever needs to be fresh within about a day.
"""
import json
import os
import threading
import time

import webapp.data as data
import webapp.strategy_a as strategy_a
import webapp.strategy_d as strategy_d
import webapp.strategy_vcp as strategy_vcp
import webapp.strategy_vcpo as strategy_vcpo
from webapp.tickers import TICKERS

OPTIMIZE_INTERVAL = 24 * 60 * 60  # re-sweep at most once a day
CHECK_INTERVAL = 60 * 60          # how often the background loop wakes to check
PERSIST_PATH = os.path.join(os.path.dirname(__file__), "optimized_cache.json")

# Training/holdout sweep is expensive (hundreds of backtests per ticker per
# strategy) and only feeds an optional UI comparison -- off by default so a
# fresh deploy isn't stuck computing it before it can serve baseline signals.
SHOW_TRAINING_HOLDOUT = os.environ.get("SHOW_TRAINING_HOLDOUT", "").strip().lower() in ("1", "true", "yes", "on")

_optimized: dict[str, dict] = {}
_last_optimized_time: float | None = None
_lock = threading.Lock()

_MODULES = {"strategy_a": strategy_a, "strategy_d": strategy_d, "strategy_vcp": strategy_vcp,
            "strategy_vcpo": strategy_vcpo}


def get_optimized(ticker: str, strategy_key: str) -> dict | None:
    with _lock:
        return _optimized.get(ticker, {}).get(strategy_key)


def _optimize_one(ticker: str) -> tuple[str, dict]:
    bars = data.get_bars(ticker)
    result = {}
    if bars is not None:
        for key, module in _MODULES.items():
            try:
                result[key] = module.optimize(ticker, bars)
            except Exception:  # noqa: BLE001 - one bad ticker/strategy shouldn't kill the sweep
                result[key] = None
    return ticker, result


def optimize_all(tickers: list[str] | None = None) -> None:
    """Blocking. Sweeps every ticker's per-strategy config. Expensive (minutes,
    not seconds) -- call this from a background thread, never the request path."""
    global _last_optimized_time
    tickers = tickers or TICKERS
    results = {}
    for tk in tickers:  # CPU-bound (GIL-limited); threading wouldn't help here
        _, res = _optimize_one(tk)
        results[tk] = res
    with _lock:
        _optimized.update(results)
        _last_optimized_time = time.time()
    _save_to_disk()


def _save_to_disk() -> None:
    with _lock:
        payload = {"saved_at": _last_optimized_time, "optimized": _optimized}
    try:
        with open(PERSIST_PATH, "w") as f:
            json.dump(payload, f)
    except OSError:
        pass  # non-fatal -- worst case we re-sweep next boot


def _load_from_disk() -> bool:
    global _optimized, _last_optimized_time
    if not os.path.exists(PERSIST_PATH):
        return False
    try:
        with open(PERSIST_PATH) as f:
            payload = json.load(f)
        with _lock:
            _optimized = payload.get("optimized", {})
            _last_optimized_time = payload.get("saved_at")
        return True
    except (OSError, json.JSONDecodeError):
        return False


def is_stale() -> bool:
    if _last_optimized_time is None:
        return True
    return time.time() - _last_optimized_time > OPTIMIZE_INTERVAL


def start_background_optimizer() -> None:
    """Load whatever's on disk immediately (non-blocking, near-instant) so
    the dashboard has *something* to show right away; kick off a fresh sweep
    in the background if what's on disk is missing or stale. No-op entirely
    when SHOW_TRAINING_HOLDOUT is off -- only baseline signals get computed."""
    if not SHOW_TRAINING_HOLDOUT:
        return
    loaded = _load_from_disk()

    def initial_sweep():
        if not loaded or is_stale():
            optimize_all()

    threading.Thread(target=initial_sweep, daemon=True).start()

    def loop():
        while True:
            time.sleep(CHECK_INTERVAL)
            if is_stale():
                optimize_all()
    threading.Thread(target=loop, daemon=True).start()

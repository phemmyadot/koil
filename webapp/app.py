"""Exhaustion dashboard backend.

Run from the project root:
    .\\.venv\\Scripts\\python.exe -m uvicorn webapp.app:app --port 8123

Architecture: fetch and compute are decoupled from the request path.
webapp/data.py owns a shared raw-OHLCV cache (one fetch serves all four
strategy evaluators, refreshed on a market-hours-aware schedule -- see that
module for the exact rule). On startup, and whenever the background
refresher decides the cache is stale, every ticker's full payload (all four
strategies) is recomputed once and held in memory. A page request is then
just a fast in-memory read -- no network call, no per-request backtest run.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import webapp.data as data
from webapp.scoring import evaluate
from webapp.tickers import TICKERS
import webapp.strategy_a as strategy_a
import webapp.strategy_d as strategy_d
import webapp.strategy_vcp as strategy_vcp

app = FastAPI(title="Exhaustion Dashboard")

_computed: list[dict] = []
_computed_errors: dict[str, str] = {}
_computed_asof: str | None = None
_compute_lock = threading.Lock()


def _eval_other_strategy(module, ticker: str, bars) -> dict | None:
    """Independently error-isolated -- one strategy failing on a ticker
    shouldn't drop the other strategies' results for that same ticker."""
    try:
        return module.evaluate(ticker, bars)
    except Exception:  # noqa: BLE001
        return None


def _compute_one(ticker: str) -> tuple[str, dict | None, str | None]:
    bars = data.get_bars(ticker)
    if bars is None:
        return ticker, None, data.get_error(ticker) or "no data"
    try:
        payload = evaluate(ticker, bars)
        payload["strategy_a"] = _eval_other_strategy(strategy_a, ticker, bars)
        payload["strategy_d"] = _eval_other_strategy(strategy_d, ticker, bars)
        payload["strategy_vcp"] = _eval_other_strategy(strategy_vcp, ticker, bars)
        return ticker, payload, None
    except Exception as e:  # noqa: BLE001 - per-ticker failures must not break the page
        return ticker, None, str(e) or type(e).__name__


def compute_all() -> None:
    """Recompute every ticker's full payload from whatever's currently in the
    raw cache. Pure CPU work, no network -- safe to call from a background
    thread without blocking request handling."""
    global _computed, _computed_errors, _computed_asof
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_compute_one, TICKERS))
    with _compute_lock:
        _computed = [p for _, p, _ in results if p is not None]
        _computed_errors = {t: e for t, _, e in results if e is not None}
        _computed_asof = datetime.now(timezone.utc).isoformat(timespec="seconds")


def refresh_and_compute() -> None:
    """Fetch (if needed) then recompute. Called once at startup and by the
    background refresher whenever webapp.data says the cache is stale."""
    data.warm_cache()
    compute_all()


@app.on_event("startup")
def _on_startup():
    refresh_and_compute()

    def loop():
        while True:
            time.sleep(data.CHECK_INTERVAL)
            if data.is_stale():
                refresh_and_compute()
    threading.Thread(target=loop, daemon=True).start()


@app.get("/api/meta")
def meta():
    return {"total_tickers": len(TICKERS), "last_fetch": data.last_fetch_time()}


@app.get("/api/tickers")
def tickers(refresh: int = 0):
    if refresh:
        refresh_and_compute()
    with _compute_lock:
        return {
            "asof": _computed_asof,
            "cached": not refresh,
            "tickers": list(_computed),
            "errors": dict(_computed_errors),
        }


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"),
                           html=True), name="static")

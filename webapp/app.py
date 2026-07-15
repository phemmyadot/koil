"""Exhaustion dashboard backend.

Run from the project root:
    .\\.venv\\Scripts\\python.exe -m uvicorn webapp.app:app --port 8123
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webapp.scoring import evaluate
from webapp.tickers import TICKERS
import webapp.strategy_a as strategy_a
import webapp.strategy_d as strategy_d
import webapp.strategy_vcp as strategy_vcp

CACHE_TTL = 15 * 60  # seconds
_cache: dict[str, tuple[float, dict]] = {}
_errors: dict[str, str] = {}

app = FastAPI(title="Exhaustion Dashboard")


def _eval_other_strategy(module, ticker: str) -> dict | None:
    """Each extra strategy does its own data fetch and is independently
    error-isolated -- one failing (e.g. insufficient history) shouldn't drop
    the other strategies' results for that ticker."""
    try:
        return module.evaluate(ticker)
    except Exception:  # noqa: BLE001
        return None


def _get_one(ticker: str, refresh: bool) -> tuple[str, dict | None, str | None]:
    now = time.time()
    if not refresh and ticker in _cache and now - _cache[ticker][0] < CACHE_TTL:
        return ticker, _cache[ticker][1], None
    try:
        payload = evaluate(ticker)
        payload["strategy_a"] = _eval_other_strategy(strategy_a, ticker)
        payload["strategy_d"] = _eval_other_strategy(strategy_d, ticker)
        payload["strategy_vcp"] = _eval_other_strategy(strategy_vcp, ticker)
        _cache[ticker] = (now, payload)
        return ticker, payload, None
    except Exception as e:  # noqa: BLE001 - per-ticker failures must not break the page
        return ticker, None, str(e) or type(e).__name__


@app.get("/api/meta")
def meta():
    """Instant, no-fetch endpoint so the frontend can seed a progress-bar
    estimate (ticker count) before kicking off the slow /api/tickers call."""
    return {"total_tickers": len(TICKERS)}


@app.get("/api/tickers")
def tickers(refresh: int = 0):
    all_cached = not refresh and all(
        t in _cache and time.time() - _cache[t][0] < CACHE_TTL for t in TICKERS)
    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(lambda t: _get_one(t, bool(refresh)), TICKERS))
    payloads = [p for _, p, _ in results if p is not None]
    errors = {t: e for t, _, e in results if e is not None}
    return {
        "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cached": all_cached,
        "tickers": payloads,
        "errors": errors,
    }


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"),
                           html=True), name="static")

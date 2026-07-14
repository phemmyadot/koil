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

CACHE_TTL = 15 * 60  # seconds
_cache: dict[str, tuple[float, dict]] = {}
_errors: dict[str, str] = {}

app = FastAPI(title="Exhaustion Dashboard")


def _get_one(ticker: str, refresh: bool) -> tuple[str, dict | None, str | None]:
    now = time.time()
    if not refresh and ticker in _cache and now - _cache[ticker][0] < CACHE_TTL:
        return ticker, _cache[ticker][1], None
    try:
        payload = evaluate(ticker)
        _cache[ticker] = (now, payload)
        return ticker, payload, None
    except Exception as e:  # noqa: BLE001 - per-ticker failures must not break the page
        return ticker, None, str(e) or type(e).__name__


@app.get("/api/tickers")
def tickers(refresh: int = 0):
    all_cached = not refresh and all(
        t in _cache and time.time() - _cache[t][0] < CACHE_TTL for t in TICKERS)
    with ThreadPoolExecutor(max_workers=20) as pool:
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

"""Single SQLite database backing all of the app's persistent caches --
price bars, computed strategy results, earnings dates, and the universe
screening marker. Replaces price_cache.pkl / computed_cache.pkl /
earnings_cache.pkl / universe_last_screened.txt (see
webapp/db_implementation.md for the full design rationale).

One file, one connection, five tables. SQLite over Postgres/MySQL: this is a
single-process app on one box, no concurrent writers from other hosts, no
need for a network round-trip -- SQLite is a file, same bind-mount
deployment story the pickle files had, zero new infrastructure. Python's
sqlite3 is stdlib, no new dependency.

check_same_thread=False: the app's background threads (data.py's warm_cache
worker pool, app.py's compute_all worker pool, scoring.py's earnings
executor) all write from threads other than the one that opened the
connection. A single module-level connection + lock serializes all access,
same discipline the old pickle caches used (each had a threading.Lock
around their in-memory dict).
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "app_data.db")

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")  # crash-safe without the os.replace()-onto-bind-mount
                                            # dance the old pickle saves needed -- WAL mode's
                                            # separate -wal/-shm files handle that internally.

ET = ZoneInfo("America/New_York")


def _init_schema() -> None:
    with _lock, _conn:
        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS bars (
                ticker TEXT NOT NULL,
                date   TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                PRIMARY KEY (ticker, date)
            );
            CREATE INDEX IF NOT EXISTS idx_bars_ticker ON bars(ticker);

            CREATE TABLE IF NOT EXISTS fetch_meta (
                ticker TEXT PRIMARY KEY,
                last_fetched_at REAL NOT NULL,
                last_bar_date TEXT NOT NULL,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS computed_results (
                ticker TEXT PRIMARY KEY,
                payload TEXT,
                source_fetched_at REAL NOT NULL,
                computed_at REAL NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS earnings_dates (
                ticker TEXT NOT NULL,
                earnings_date TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (ticker, earnings_date)
            );
            CREATE INDEX IF NOT EXISTS idx_earnings_ticker ON earnings_dates(ticker);

            CREATE TABLE IF NOT EXISTS universe_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_screened_date TEXT NOT NULL
            );
        """)


_init_schema()


# ─────────────────────────── price bars ───────────────────────────

def get_last_bar_date(ticker: str) -> str | None:
    """Most recent date actually stored for this ticker -- the anchor for
    incremental fetch (fetch only start=last_bar_date+1 onward). None if
    this ticker has never been fetched at all."""
    with _lock:
        row = _conn.execute(
            "SELECT last_bar_date FROM fetch_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row[0] if row else None


def get_fetched_at(ticker: str) -> float | None:
    with _lock:
        row = _conn.execute(
            "SELECT last_fetched_at FROM fetch_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row[0] if row else None


def get_error(ticker: str) -> str | None:
    with _lock:
        row = _conn.execute(
            "SELECT last_error FROM fetch_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row[0] if row and row[0] else None


def upsert_bars(ticker: str, df: pd.DataFrame, fetched_at: float) -> None:
    """Insert/replace one row per date in df, then update fetch_meta's
    last_bar_date/last_fetched_at for this ticker. df's index must be a
    DatetimeIndex; columns Open/High/Low/Close/Volume (yfinance's default
    casing). Called with a df that may be the full history (cold fetch) or
    just the new tail (incremental fetch) -- INSERT OR REPLACE makes both
    cases correct without the caller needing to know which happened."""
    if df.empty:
        # Nothing new -- still bump last_fetched_at so the TTL check knows
        # this ticker was actually re-checked just now, not skipped.
        with _lock, _conn:
            _conn.execute("""
                UPDATE fetch_meta SET last_fetched_at = ?, last_error = NULL
                WHERE ticker = ?
            """, (fetched_at, ticker))
        return

    rows = [
        (ticker, idx.strftime("%Y-%m-%d"), float(row["Open"]), float(row["High"]),
         float(row["Low"]), float(row["Close"]), int(row["Volume"]))
        for idx, row in df.iterrows()
    ]
    last_bar_date = df.index.max().strftime("%Y-%m-%d")

    with _lock, _conn:
        _conn.executemany("""
            INSERT INTO bars (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume
        """, rows)
        _conn.execute("""
            INSERT INTO fetch_meta (ticker, last_fetched_at, last_bar_date, last_error)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(ticker) DO UPDATE SET
                last_fetched_at=excluded.last_fetched_at,
                last_bar_date=excluded.last_bar_date,
                last_error=NULL
        """, (ticker, fetched_at, last_bar_date))


def mark_fetch_error(ticker: str, fetched_at: float, error: str) -> None:
    """A fetch attempt failed (rate-limited, insufficient history, etc.) --
    record the error and fetched_at so the next scheduled refresh knows to
    retry, without touching whatever bars (if any) were already stored."""
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO fetch_meta (ticker, last_fetched_at, last_bar_date, last_error)
            VALUES (?, ?, '', ?)
            ON CONFLICT(ticker) DO UPDATE SET
                last_fetched_at=excluded.last_fetched_at, last_error=excluded.last_error
        """, (ticker, fetched_at, error))


def load_all_bars() -> dict[str, pd.DataFrame]:
    """Startup bulk-load into RAM -- data.py keeps this as its hot-path
    in-memory cache (_raw_cache), same as before; the DB is the durable
    backing store underneath it, not a per-request query target. Reading
    ~2000 tickers' full history in one query + one groupby is much faster
    than 2000 individual per-ticker queries."""
    with _lock:
        df = pd.read_sql_query("SELECT * FROM bars ORDER BY ticker, date", _conn)
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"])
    out: dict[str, pd.DataFrame] = {}
    for ticker, group in df.groupby("ticker"):
        g = group.set_index("date")[["open", "high", "low", "close", "volume"]]
        g.columns = ["Open", "High", "Low", "Close", "Volume"]
        g.index.name = None
        out[ticker] = g
    return out


def load_all_fetch_meta() -> tuple[dict[str, float], dict[str, str]]:
    """Returns (fetched_at_by_ticker, error_by_ticker) -- mirrors data.py's
    old _fetched_at / _raw_errors dicts for the startup bulk-load."""
    with _lock:
        rows = _conn.execute("SELECT ticker, last_fetched_at, last_error FROM fetch_meta").fetchall()
    fetched_at = {tk: fa for tk, fa, _err in rows}
    errors = {tk: err for tk, _fa, err in rows if err}
    return fetched_at, errors


# ─────────────────────────── computed results ───────────────────────────

def get_computed(ticker: str) -> tuple[dict | None, float | None, str | None]:
    """Returns (payload, source_fetched_at, error) for one ticker. payload
    is None if the last compute attempt for this ticker errored."""
    with _lock:
        row = _conn.execute(
            "SELECT payload, source_fetched_at, error FROM computed_results WHERE ticker = ?",
            (ticker,)
        ).fetchone()
    if row is None:
        return None, None, None
    payload_json, source_fetched_at, error = row
    payload = json.loads(payload_json) if payload_json else None
    return payload, source_fetched_at, error


def upsert_computed(ticker: str, payload: dict | None, source_fetched_at: float,
                     computed_at: float, error: str | None) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO computed_results (ticker, payload, source_fetched_at, computed_at, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                payload=excluded.payload, source_fetched_at=excluded.source_fetched_at,
                computed_at=excluded.computed_at, error=excluded.error
        """, (ticker, json.dumps(payload) if payload is not None else None,
              source_fetched_at, computed_at, error))


def get_max_computed_at() -> float | None:
    """Most recent computed_at across all tickers -- lets app.py restore
    _computed_asof on startup from whatever's already in the DB, instead of
    it reading None (and the frontend showing a cold-start loader) even
    though every ticker's result is actually already cached and fresh."""
    with _lock:
        row = _conn.execute("SELECT MAX(computed_at) FROM computed_results").fetchone()
    return row[0] if row and row[0] is not None else None


def load_all_computed() -> tuple[list[dict], dict[str, str], dict[str, float]]:
    """Returns (computed_list, errors_by_ticker, source_fetch_by_ticker) --
    mirrors app.py's old _computed / _computed_errors / _computed_source_fetch
    for the startup bulk-load."""
    with _lock:
        rows = _conn.execute(
            "SELECT ticker, payload, source_fetched_at, error FROM computed_results"
        ).fetchall()
    computed, errors, source_fetch = [], {}, {}
    for ticker, payload_json, source_fetched_at, error in rows:
        source_fetch[ticker] = source_fetched_at
        if payload_json:
            computed.append(json.loads(payload_json))
        if error:
            errors[ticker] = error
    return computed, errors, source_fetch


# ─────────────────────────── earnings dates ───────────────────────────

_EARNINGS_TTL = 24 * 60 * 60


def get_earnings_dates(ticker: str) -> pd.DatetimeIndex | None:
    """None on a cache miss or expired TTL -- caller should treat that as
    'need to fetch fresh from Yahoo,' same as the old in-memory cache."""
    with _lock:
        rows = _conn.execute(
            "SELECT earnings_date, fetched_at FROM earnings_dates WHERE ticker = ?", (ticker,)
        ).fetchall()
    if not rows:
        return None
    fetched_at = rows[0][1]
    if pd.Timestamp.now(tz="UTC").timestamp() - fetched_at >= _EARNINGS_TTL:
        return None
    real_dates = sorted(d for d, _ in rows if d != "__none__")
    return pd.DatetimeIndex(real_dates)


def upsert_earnings_dates(ticker: str, dates: pd.DatetimeIndex, fetched_at: float) -> None:
    with _lock, _conn:
        _conn.execute("DELETE FROM earnings_dates WHERE ticker = ?", (ticker,))
        if len(dates):
            _conn.executemany(
                "INSERT INTO earnings_dates (ticker, earnings_date, fetched_at) VALUES (?, ?, ?)",
                [(ticker, d.strftime("%Y-%m-%d"), fetched_at) for d in dates]
            )
        else:
            # Still record that we checked, with a phantom row-less marker --
            # store a single sentinel row so get_earnings_dates's TTL check
            # has something to key off even when a ticker genuinely has no
            # upcoming/recent earnings dates at all.
            _conn.execute(
                "INSERT INTO earnings_dates (ticker, earnings_date, fetched_at) VALUES (?, ?, ?)",
                (ticker, "__none__", fetched_at)
            )


# ─────────────────────────── universe marker ───────────────────────────

def get_last_screened_date() -> str | None:
    with _lock:
        row = _conn.execute("SELECT last_screened_date FROM universe_meta WHERE id = 1").fetchone()
    return row[0] if row else None


def set_last_screened_date(date: str) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO universe_meta (id, last_screened_date) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_screened_date=excluded.last_screened_date
        """, (date,))

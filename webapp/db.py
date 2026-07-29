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
worker pool, app.py's compute_all worker pool, strategy_vexh.py's earnings
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
                source_bar_date TEXT NOT NULL,
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
                last_screened_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watchlist_tickers (
                ticker TEXT PRIMARY KEY,
                added_at REAL NOT NULL
            );
        """)
    _migrate_universe_meta_date_to_epoch()
    _migrate_computed_results_fetch_epoch_to_bar_date()


def _migrate_universe_meta_date_to_epoch() -> None:
    """One-time migration: universe_meta used to store last_screened_date
    (a date string, from the once-per-calendar-day rule) before the refresh
    architecture changed to a rolling interval keyed off an epoch timestamp
    (last_screened_at). If a pre-migration DB still has the old column,
    convert its value to an epoch (midnight UTC of that date) so the new
    interval check has something sane to compare against instead of
    treating an old DB as "never screened" and forcing an immediate re-screen."""
    with _lock, _conn:
        cols = [row[1] for row in _conn.execute("PRAGMA table_info(universe_meta)").fetchall()]
        if "last_screened_date" not in cols:
            return
        if "last_screened_at" not in cols:
            # CREATE TABLE IF NOT EXISTS is a no-op against an existing
            # pre-migration table, so the new column was never actually
            # added -- add it explicitly before writing to it below.
            _conn.execute("ALTER TABLE universe_meta ADD COLUMN last_screened_at REAL")
        row = _conn.execute("SELECT last_screened_date FROM universe_meta WHERE id = 1").fetchone()
        if row and row[0]:
            # UPDATE, not INSERT -- the row already exists (we just read it),
            # and the old last_screened_date column is still NOT NULL at
            # this point, which an INSERT (even one that resolves via
            # ON CONFLICT) would need to satisfy for the whole row.
            epoch = pd.Timestamp(row[0], tz="UTC").timestamp()
            _conn.execute("UPDATE universe_meta SET last_screened_at = ? WHERE id = 1", (epoch,))
        _conn.execute("ALTER TABLE universe_meta DROP COLUMN last_screened_date")


def _migrate_computed_results_fetch_epoch_to_bar_date() -> None:
    """One-time migration: computed_results used to key staleness off
    source_fetched_at (an epoch -- data.py's fetch attempt timestamp, which
    advances on every gap-fetch even when zero new rows come back) before
    switching to source_bar_date (the actual last stored bar date, which
    only advances on real new data). There's no reliable way to derive what
    each ticker's last_bar_date WAS at the time of that old fetch timestamp,
    so rather than fabricate a mapping, this just clears the stale rows --
    every ticker recomputes once on the next compute_all() pass (same cost
    as any other cold-start recompute) and re-populates correctly keyed
    going forward.

    BUG FIXED HERE: this used to drop source_fetched_at without ever adding
    source_bar_date back -- CREATE TABLE IF NOT EXISTS is a no-op against a
    table that already exists, so a DB that had already run this migration's
    DROP COLUMN step permanently ended up with NEITHER column. Every restart
    after that hit "no such column: source_bar_date" loading computed_results,
    silently starting _computed cold every single time -- which combined with
    the (correct) restart-staleness fetch-skip meant a server could sit with
    zero computed results for a full CHECK_INTERVAL before self-healing,
    showing "Loading first dataset..." with no progress the whole time."""
    with _lock, _conn:
        cols = [row[1] for row in _conn.execute("PRAGMA table_info(computed_results)").fetchall()]
        if "source_fetched_at" in cols:
            _conn.execute("DELETE FROM computed_results")
            _conn.execute("ALTER TABLE computed_results DROP COLUMN source_fetched_at")
            cols.remove("source_fetched_at")
        if "source_bar_date" not in cols:
            _conn.execute("ALTER TABLE computed_results ADD COLUMN source_bar_date TEXT")


_init_schema()


# ─────────────────────────── price bars ───────────────────────────

def has_any_bars() -> bool:
    """True if the DB has ANY stored price bar for ANY ticker -- a pure
    existence check (SELECT ... LIMIT 1), not a staleness/duration
    calculation. Used to decide "is this genuinely a cold/empty DB" as a
    simple yes/no, separate from CHECK_INTERVAL-based freshness checks
    elsewhere (data.warm_cache, app._universe_refresh_if_needed)."""
    with _lock:
        row = _conn.execute("SELECT 1 FROM bars LIMIT 1").fetchone()
    return row is not None


def set_watchlist_tickers(tickers: list[str], added_at: float) -> None:
    """Replaces the full server-known watchlist ticker set with `tickers` -- the client is the
    source of truth (watchlists themselves live in the browser's localStorage), this table just
    lets the background fetch/compute loop know which extra tickers to keep alive so a ticker
    that later fails universe re-screening doesn't silently go stale on a saved watchlist."""
    with _lock, _conn:
        _conn.execute("DELETE FROM watchlist_tickers")
        _conn.executemany("INSERT INTO watchlist_tickers (ticker, added_at) VALUES (?, ?)",
                           [(tk, added_at) for tk in tickers])


def get_watchlist_tickers() -> list[str]:
    with _lock:
        rows = _conn.execute("SELECT ticker FROM watchlist_tickers").fetchall()
    return [r[0] for r in rows]


def has_any_computed() -> bool:
    """True if computed_results has ANY row at all -- same pure existence
    check as has_any_bars(), for the same reason: bars can exist (so the
    startup fetch is correctly skipped) while computed_results is still
    empty (e.g. wiped by a schema migration, or a prior process crashed
    before ever completing a compute pass) -- without this, the app would
    sit with asof=null until the next 2h loop wake, with nothing to trigger
    a compute pass in the meantime."""
    with _lock:
        row = _conn.execute("SELECT 1 FROM computed_results LIMIT 1").fetchone()
    return row is not None


def get_last_bar_date(ticker: str) -> str | None:
    """Most recent date actually stored for this ticker -- the anchor for
    incremental fetch (fetch only start=last_bar_date+1 onward). None if
    this ticker has never been fetched at all."""
    with _lock:
        row = _conn.execute(
            "SELECT last_bar_date FROM fetch_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row[0] if row else None


def get_last_bar_close(ticker: str, date: str) -> float | None:
    """Close price stored for this ticker on `date`. Yahoo can revise a same-day bar's
    Close as the session settles without changing last_bar_date at all (still today's
    date) -- compute_all()'s reuse check needs this alongside the date, or a same-day
    price correction silently never triggers a recompute."""
    with _lock:
        row = _conn.execute(
            "SELECT close FROM bars WHERE ticker = ? AND date = ?", (ticker, date)
        ).fetchone()
    return row[0] if row else None


def get_fetched_at(ticker: str) -> float | None:
    with _lock:
        row = _conn.execute(
            "SELECT last_fetched_at FROM fetch_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row[0] if row else None


def get_max_fetched_at() -> float | None:
    """Most recent last_fetched_at across all tickers -- lets app.py check
    whether prices are already fresh before deciding to fetch immediately on
    startup, so a process restart shortly after the previous process's last
    fetch doesn't re-hit Yahoo for every ticker just because it restarted."""
    with _lock:
        row = _conn.execute("SELECT MAX(last_fetched_at) FROM fetch_meta").fetchone()
    return row[0] if row and row[0] is not None else None


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
        # this ticker was actually re-checked just now, not skipped. Must be
        # an upsert, not a bare UPDATE: a bare UPDATE silently affects 0 rows
        # if this ticker somehow has no fetch_meta row yet (e.g. bars were
        # written some other way, or a prior write was interrupted), leaving
        # last_bar_date permanently NULL/missing even though real bars exist
        # -- which crashes compute_all() with a NOT NULL constraint failure
        # the next time this ticker is (re)computed. Derive last_bar_date
        # from the bars table itself if there's no existing fetch_meta row
        # to preserve it from.
        with _lock, _conn:
            existing = _conn.execute(
                "SELECT last_bar_date FROM fetch_meta WHERE ticker = ?", (ticker,)
            ).fetchone()
            if existing:
                _conn.execute("""
                    UPDATE fetch_meta SET last_fetched_at = ?, last_error = NULL
                    WHERE ticker = ?
                """, (fetched_at, ticker))
            else:
                row = _conn.execute(
                    "SELECT MAX(date) FROM bars WHERE ticker = ?", (ticker,)
                ).fetchone()
                last_bar_date = row[0] if row and row[0] else None
                if last_bar_date is not None:
                    _conn.execute("""
                        INSERT INTO fetch_meta (ticker, last_fetched_at, last_bar_date, last_error)
                        VALUES (?, ?, ?, NULL)
                        ON CONFLICT(ticker) DO UPDATE SET
                            last_fetched_at=excluded.last_fetched_at, last_error=NULL
                    """, (ticker, fetched_at, last_bar_date))
                # else: no bars and no fetch_meta row and an empty fetch --
                # genuinely nothing to record yet, leave it as a true miss.
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

def get_computed(ticker: str) -> tuple[dict | None, str | None, str | None]:
    """Returns (payload, source_bar_date, error) for one ticker. payload
    is None if the last compute attempt for this ticker errored.
    source_bar_date is app.py's _bar_fingerprint() this result was computed
    from ("date|close", not a bare date -- see its docstring) -- the
    staleness key: recompute only if the current fingerprint no longer matches."""
    with _lock:
        row = _conn.execute(
            "SELECT payload, source_bar_date, error FROM computed_results WHERE ticker = ?",
            (ticker,)
        ).fetchone()
    if row is None:
        return None, None, None
    payload_json, source_bar_date, error = row
    payload = json.loads(payload_json) if payload_json else None
    return payload, source_bar_date, error


def upsert_computed(ticker: str, payload: dict | None, source_bar_date: str,
                     computed_at: float, error: str | None) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO computed_results (ticker, payload, source_bar_date, computed_at, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                payload=excluded.payload, source_bar_date=excluded.source_bar_date,
                computed_at=excluded.computed_at, error=excluded.error
        """, (ticker, json.dumps(payload) if payload is not None else None,
              source_bar_date, computed_at, error))


def get_max_computed_at() -> float | None:
    """Most recent computed_at across all tickers -- lets app.py restore
    _computed_asof on startup from whatever's already in the DB, instead of
    it reading None (and the frontend showing a cold-start loader) even
    though every ticker's result is actually already cached and fresh."""
    with _lock:
        row = _conn.execute("SELECT MAX(computed_at) FROM computed_results").fetchone()
    return row[0] if row and row[0] is not None else None


def load_all_computed() -> tuple[list[dict], dict[str, str], dict[str, str]]:
    """Returns (computed_list, errors_by_ticker, source_bar_date_by_ticker)
    -- mirrors app.py's _computed / _computed_errors / _computed_source_fetch
    for the startup bulk-load."""
    with _lock:
        rows = _conn.execute(
            "SELECT ticker, payload, source_bar_date, error FROM computed_results"
        ).fetchall()
    computed, errors, source_fetch = [], {}, {}
    for ticker, payload_json, source_bar_date, error in rows:
        source_fetch[ticker] = source_bar_date
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

def get_last_screened_at() -> float | None:
    with _lock:
        row = _conn.execute("SELECT last_screened_at FROM universe_meta WHERE id = 1").fetchone()
    return row[0] if row else None


def set_last_screened_at(epoch: float) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO universe_meta (id, last_screened_at) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_screened_at=excluded.last_screened_at
        """, (epoch,))

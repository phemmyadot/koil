"""Single SQLite database backing all of the app's persistent caches --
price bars, computed strategy results, earnings dates, and the universe
screening marker. Replaces price_cache.pkl / computed_cache.pkl /
earnings_cache.pkl / universe_last_screened.txt (see
backend/db_implementation.md for the full design rationale).

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
import hashlib
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

            CREATE TABLE IF NOT EXISTS candidate_tickers (
                ticker TEXT PRIMARY KEY,
                fetched_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                tp_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                last_alert_tp_pct REAL,
                last_alert_stop_pct REAL,
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            -- Only one OPEN position per ticker at a time -- a later entry after a position
            -- fully closes starts a NEW position row (see 2026-07-31-position-fills-design.md),
            -- never reopens the old one. SQLite supports partial indexes since 3.8 (2013),
            -- long before any version this project would run on.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_one_open_per_ticker
                ON positions(ticker) WHERE status = 'open';

            CREATE TABLE IF NOT EXISTS position_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                strategy_key TEXT NOT NULL,
                signal_date TEXT NOT NULL,
                kind TEXT NOT NULL,
                fill_date TEXT NOT NULL,
                price REAL,
                units REAL NOT NULL,
                instrument TEXT NOT NULL,
                exit_reason TEXT,
                opt_side TEXT,
                opt_type TEXT,
                strike REAL,
                premium REAL,
                expiry_date TEXT,
                iv_at_entry REAL,
                notes TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_position_fills_position ON position_fills(position_id);
        """)
    _migrate_universe_meta_date_to_epoch()
    _migrate_computed_results_fetch_epoch_to_bar_date()
    _migrate_taken_trades_to_positions_and_fills()
    _migrate_position_fills_price_nullable()
    # trade_daily_marks/notifications table+index creation lives here, AFTER the migration
    # above, not in the main executescript -- a pre-migration DB still has these tables with
    # the old trade_id column at that point, and CREATE INDEX ... ON table(position_id) would
    # fail immediately against a column that doesn't exist yet. The migration function renames
    # trade_id -> position_id in place for an existing table; CREATE TABLE IF NOT EXISTS here
    # is then a no-op for it and only actually creates the table fresh for a brand-new DB.
    with _lock, _conn:
        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS trade_daily_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                mark_date TEXT NOT NULL,
                close_price REAL NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (position_id, mark_date)
            );
            CREATE INDEX IF NOT EXISTS idx_trade_daily_marks_position ON trade_daily_marks(position_id);

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                pct REAL NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read_at);

            -- No user/account table exists in this single-user app, so subscriptions aren't
            -- scoped to a user id -- every stored subscription receives every push (see
            -- docs/superpowers/specs/2026-07-31-pwa-push-design.md).
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
        """)


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


def _migrate_taken_trades_to_positions_and_fills() -> None:
    """One-time migration: the old single-entry/single-exit taken_trades table is replaced by
    positions (one row per ticker campaign) + position_fills (flat entry/exit events) -- see
    docs/superpowers/specs/2026-07-31-position-fills-design.md. Each taken_trades row converts
    1:1 into one positions row (preserving its original id, so trade_daily_marks/notifications
    FK values keep pointing at the right thing after their own column rename below) plus one
    'entry' fill from entry_price/entry_date, and one 'exit' fill too if the trade was already
    closed. No-ops entirely once taken_trades no longer exists (already migrated, or a fresh DB
    that was never on the old schema)."""
    with _lock, _conn:
        tables = {row[0] for row in _conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "taken_trades" not in tables:
            return

        rows = _conn.execute("SELECT * FROM taken_trades").fetchall()
        cols = [d[0] for d in _conn.execute("SELECT * FROM taken_trades LIMIT 1").description]
        old_trades = [dict(zip(cols, r)) for r in rows]

        for t in old_trades:
            _conn.execute("""
                INSERT INTO positions (id, ticker, status, tp_price, stop_price, opened_at,
                                        closed_at, last_alert_tp_pct, last_alert_stop_pct, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (t["id"], t["ticker"], t["status"], t["tp_price"], t["stop_price"],
                  t["confirmed_at"], t["closed_at"], t["last_alert_tp_pct"], t["last_alert_stop_pct"],
                  t["notes"]))

            entry_created_at = t["confirmed_at"]
            _conn.execute("""
                INSERT INTO position_fills (position_id, strategy_key, signal_date, kind, fill_date,
                    price, units, instrument, exit_reason, opt_side, opt_type, strike, premium,
                    expiry_date, iv_at_entry, notes, created_at)
                VALUES (?, ?, ?, 'entry', ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?)
            """, (t["id"], t["strategy_key"], t["signal_date"], t["entry_date"],
                  t["entry_price"], t["units"] if t["instrument"] == "spot" else t["contracts"],
                  t["instrument"], t["opt_side"], t["opt_type"], t["strike"], t["premium"],
                  t["expiry_date"], t["iv_at_entry"], entry_created_at))

            if t["status"] == "closed" and t["exit_price"] is not None:
                exit_units = t["units"] if t["instrument"] == "spot" else t["contracts"]
                _conn.execute("""
                    INSERT INTO position_fills (position_id, strategy_key, signal_date, kind, fill_date,
                        price, units, instrument, exit_reason, opt_side, opt_type, strike, premium,
                        expiry_date, iv_at_entry, notes, created_at)
                    VALUES (?, ?, ?, 'exit', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """, (t["id"], t["strategy_key"], t["signal_date"],
                      (t["closed_at"] or entry_created_at)[:10], t["exit_price"], exit_units,
                      t["instrument"], t["exit_reason"], t["opt_side"], t["opt_type"], t["strike"],
                      t["premium"], t["expiry_date"], t["iv_at_entry"], t["closed_at"] or entry_created_at))

        # Repoint trade_daily_marks/notifications at the new column name -- the FK VALUES are
        # unchanged (positions.id == the old taken_trades.id, preserved above), only the column
        # is renamed from trade_id to position_id.
        mark_cols = [row[1] for row in _conn.execute("PRAGMA table_info(trade_daily_marks)").fetchall()]
        if "trade_id" in mark_cols:
            _conn.execute("ALTER TABLE trade_daily_marks RENAME COLUMN trade_id TO position_id")
        notif_cols = [row[1] for row in _conn.execute("PRAGMA table_info(notifications)").fetchall()]
        if "trade_id" in notif_cols:
            _conn.execute("ALTER TABLE notifications RENAME COLUMN trade_id TO position_id")

        _conn.execute("DROP TABLE taken_trades")


def _migrate_position_fills_price_nullable() -> None:
    """One-time migration: position_fills.price used to be REAL NOT NULL for every fill,
    spot or option -- for options it held the underlying stock's price, which
    docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md found is never actually
    read anywhere except a since-fixed alert-math bug that compared it against average premium
    (wrong units). Options now only ever use premium; price is spot-only and optional. SQLite
    has no ALTER COLUMN, so this is the standard create-copy-drop-rename dance, matching the
    style _migrate_taken_trades_to_positions_and_fills already uses for this table. A fresh DB
    never hits this at all -- the CREATE TABLE IF NOT EXISTS above already declares price
    nullable, so this only fires against a pre-existing DB whose price column is still NOT NULL."""
    with _lock, _conn:
        cols = _conn.execute("PRAGMA table_info(position_fills)").fetchall()
        price_col = next((c for c in cols if c[1] == "price"), None)
        if price_col is None or price_col[3] == 0:  # column[3] is "notnull"; 0 means already nullable
            return
        _conn.execute("""
            CREATE TABLE position_fills_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                strategy_key TEXT NOT NULL,
                signal_date TEXT NOT NULL,
                kind TEXT NOT NULL,
                fill_date TEXT NOT NULL,
                price REAL,
                units REAL NOT NULL,
                instrument TEXT NOT NULL,
                exit_reason TEXT,
                opt_side TEXT,
                opt_type TEXT,
                strike REAL,
                premium REAL,
                expiry_date TEXT,
                iv_at_entry REAL,
                notes TEXT,
                created_at TEXT NOT NULL
            )
        """)
        _conn.execute("""
            INSERT INTO position_fills_new (id, position_id, strategy_key, signal_date, kind,
                fill_date, price, units, instrument, exit_reason, opt_side, opt_type, strike,
                premium, expiry_date, iv_at_entry, notes, created_at)
            SELECT id, position_id, strategy_key, signal_date, kind, fill_date, price, units,
                instrument, exit_reason, opt_side, opt_type, strike, premium, expiry_date,
                iv_at_entry, notes, created_at
            FROM position_fills
        """)
        _conn.execute("DROP TABLE position_fills")
        _conn.execute("ALTER TABLE position_fills_new RENAME TO position_fills")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_position_fills_position ON position_fills(position_id)")


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


def set_candidate_tickers(tickers: list[str], fetched_at: float) -> None:
    """Replaces the full candidate ticker set -- the Yahoo screener result for the cycle
    currently running. Written once per cycle (step 1) so every other step/reader
    (_active_tickers(), the technical filter) reads this table instead of re-hitting Yahoo."""
    with _lock, _conn:
        _conn.execute("DELETE FROM candidate_tickers")
        _conn.executemany("INSERT INTO candidate_tickers (ticker, fetched_at) VALUES (?, ?)",
                           [(tk, fetched_at) for tk in tickers])


def get_candidate_tickers() -> list[str]:
    with _lock:
        rows = _conn.execute("SELECT ticker FROM candidate_tickers").fetchall()
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


def get_bars_checksum(ticker: str) -> str | None:
    """Hash of every stored bar for this ticker (date + OHLCV, in date order) -- the
    staleness key compute_all() reuses a cached payload against. A last-bar-only
    fingerprint (date + close) misses a revision to an EARLIER bar in the history (Yahoo
    has been observed revising settled data, not just the current session's still-open
    bar); hashing the whole stored history catches any change anywhere in it. None if
    this ticker has no bars stored at all."""
    with _lock:
        rows = _conn.execute(
            "SELECT date, open, high, low, close, volume FROM bars WHERE ticker = ? ORDER BY date",
            (ticker,)
        ).fetchall()
    if not rows:
        return None
    h = hashlib.sha256()
    for row in rows:
        h.update("|".join(str(v) for v in row).encode())
        h.update(b"\n")
    return h.hexdigest()


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
    source_bar_date is the get_bars_checksum() value this result was computed
    from -- the staleness key: recompute only if the current checksum no
    longer matches."""
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


# ─────────────────────────── positions ───────────────────────────
# See docs/superpowers/specs/2026-07-31-position-fills-design.md. A position is one row per
# ticker "campaign" -- status/avg-cost/P&L are all derived by replaying its position_fills
# (see backend/app.py's replay_fills()), never stored directly except status/closed_at, which
# are kept as a fast-lookup cache of that same replay (see the design doc's "Status derivation"
# section for why: _active_tickers()/the alert engine need "all open positions" as an indexed
# query, not a full fills-replay per ticker on every request).

_POSITION_COLUMNS = [
    "id", "ticker", "status", "tp_price", "stop_price", "opened_at", "closed_at",
    "last_alert_tp_pct", "last_alert_stop_pct", "notes",
]

_FILL_COLUMNS = [
    "id", "position_id", "strategy_key", "signal_date", "kind", "fill_date", "price", "units",
    "instrument", "exit_reason", "opt_side", "opt_type", "strike", "premium", "expiry_date",
    "iv_at_entry", "notes", "created_at",
]


def _row_to_position(row) -> dict:
    return dict(zip(_POSITION_COLUMNS, row))


def _row_to_fill(row) -> dict:
    return dict(zip(_FILL_COLUMNS, row))


def insert_position(ticker: str, tp_price: float, stop_price: float, opened_at: str,
                     notes: str | None = None) -> int:
    """Creates a new open position. Raises sqlite3.IntegrityError if this ticker already has
    an open position (idx_positions_one_open_per_ticker) -- callers should add a fill to the
    existing open position instead (see find_open_position_by_ticker)."""
    with _lock, _conn:
        cur = _conn.execute("""
            INSERT INTO positions (ticker, status, tp_price, stop_price, opened_at, notes)
            VALUES (?, 'open', ?, ?, ?, ?)
        """, (ticker, tp_price, stop_price, opened_at, notes))
        return cur.lastrowid


def get_position(position_id: int) -> dict | None:
    with _lock:
        row = _conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
    return _row_to_position(row) if row else None


def find_open_position_by_ticker(ticker: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM positions WHERE ticker = ? AND status = 'open'", (ticker,)
        ).fetchone()
    return _row_to_position(row) if row else None


def list_positions(status: str | None = None) -> list[dict]:
    with _lock:
        if status:
            rows = _conn.execute(
                "SELECT * FROM positions WHERE status = ? ORDER BY opened_at DESC, id DESC", (status,)
            ).fetchall()
        else:
            rows = _conn.execute("SELECT * FROM positions ORDER BY opened_at DESC, id DESC").fetchall()
    return [_row_to_position(r) for r in rows]


def update_position_fields(position_id: int, fields: dict) -> None:
    """Generic partial update -- fields is a dict of column -> new value. For position-level
    fields only (tp_price, stop_price, notes); fills are corrected via update_fill_fields."""
    if not fields:
        return
    set_clause = ", ".join(f"{c} = ?" for c in fields)
    with _lock, _conn:
        _conn.execute(f"UPDATE positions SET {set_clause} WHERE id = ?",
                       [*fields.values(), position_id])


def set_position_status(position_id: int, status: str, closed_at: str | None) -> None:
    """Flips status/closed_at -- called after every fill insert/delete, recomputed from a fresh
    replay of that position's fills (see app.py), never a value the caller invents directly."""
    with _lock, _conn:
        _conn.execute("UPDATE positions SET status = ?, closed_at = ? WHERE id = ?",
                       (status, closed_at, position_id))


def delete_position(position_id: int) -> None:
    """Hard delete -- cancels the whole position and every one of its fills. Cascades to daily
    marks and notifications so nothing orphaned lingers referencing a since-deleted position_id."""
    with _lock, _conn:
        _conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        _conn.execute("DELETE FROM position_fills WHERE position_id = ?", (position_id,))
        _conn.execute("DELETE FROM trade_daily_marks WHERE position_id = ?", (position_id,))
        _conn.execute("DELETE FROM notifications WHERE position_id = ?", (position_id,))


def update_position_alert_pct(position_id: int, *, tp_pct: float | None = None,
                               stop_pct: float | None = None) -> None:
    """Bumps last_alert_tp_pct/last_alert_stop_pct -- only the side(s) passed are touched,
    so the alert engine can update one side without clobbering the other."""
    if tp_pct is None and stop_pct is None:
        return
    with _lock, _conn:
        if tp_pct is not None:
            _conn.execute("UPDATE positions SET last_alert_tp_pct = ? WHERE id = ?", (tp_pct, position_id))
        if stop_pct is not None:
            _conn.execute("UPDATE positions SET last_alert_stop_pct = ? WHERE id = ?", (stop_pct, position_id))


# ─────────────────────────── position fills ───────────────────────────

def insert_fill(fill: dict) -> int:
    """Insert one entry/exit event. `fill` must supply every column in _FILL_COLUMNS except id
    (option-only fields may be None for instrument='spot'; exit_reason is None for kind='entry')."""
    cols = [c for c in _FILL_COLUMNS if c != "id"]
    with _lock, _conn:
        cur = _conn.execute(
            f"INSERT INTO position_fills ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [fill[c] for c in cols],
        )
        return cur.lastrowid


def get_fill(fill_id: int) -> dict | None:
    with _lock:
        row = _conn.execute("SELECT * FROM position_fills WHERE id = ?", (fill_id,)).fetchone()
    return _row_to_fill(row) if row else None


def list_fills(position_id: int) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM position_fills WHERE position_id = ? ORDER BY fill_date, id",
            (position_id,)
        ).fetchall()
    return [_row_to_fill(r) for r in rows]


def list_fills_bulk(position_ids: list[int]) -> dict[int, list[dict]]:
    """Fills for many positions in one query -- for list/analytics views that would otherwise
    need one list_fills() call per position."""
    if not position_ids:
        return {}
    placeholders = ", ".join("?" * len(position_ids))
    with _lock:
        rows = _conn.execute(
            f"SELECT * FROM position_fills WHERE position_id IN ({placeholders}) ORDER BY position_id, fill_date, id",
            position_ids,
        ).fetchall()
    out: dict[int, list[dict]] = {pid: [] for pid in position_ids}
    for r in rows:
        fill = _row_to_fill(r)
        out[fill["position_id"]].append(fill)
    return out


def update_fill_fields(fill_id: int, fields: dict) -> None:
    """Generic partial update for one fill -- e.g. correcting a wrong exit price."""
    if not fields:
        return
    set_clause = ", ".join(f"{c} = ?" for c in fields)
    with _lock, _conn:
        _conn.execute(f"UPDATE position_fills SET {set_clause} WHERE id = ?",
                       [*fields.values(), fill_id])


def delete_fill(fill_id: int) -> None:
    with _lock, _conn:
        _conn.execute("DELETE FROM position_fills WHERE id = ?", (fill_id,))


# ─────────────────────────── trade daily marks ───────────────────────────

def upsert_trade_daily_mark(position_id: int, mark_date: str, close_price: float, updated_at: str) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO trade_daily_marks (position_id, mark_date, close_price, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(position_id, mark_date) DO UPDATE SET
                close_price=excluded.close_price, updated_at=excluded.updated_at
        """, (position_id, mark_date, close_price, updated_at))


def get_trade_daily_marks(position_id: int) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT mark_date, close_price FROM trade_daily_marks WHERE position_id = ? ORDER BY mark_date",
            (position_id,)
        ).fetchall()
    return [{"mark_date": d, "close_price": c} for d, c in rows]


def get_trade_daily_marks_bulk(position_ids: list[int]) -> dict[int, list[dict]]:
    """Marks for many positions in one query -- for the analytics/cohort view, which would
    otherwise need one get_trade_daily_marks() call per filtered position."""
    if not position_ids:
        return {}
    placeholders = ", ".join("?" * len(position_ids))
    with _lock:
        rows = _conn.execute(
            f"SELECT position_id, mark_date, close_price FROM trade_daily_marks "
            f"WHERE position_id IN ({placeholders}) ORDER BY position_id, mark_date",
            position_ids,
        ).fetchall()
    out: dict[int, list[dict]] = {pid: [] for pid in position_ids}
    for pid, mark_date, close_price in rows:
        out[pid].append({"mark_date": mark_date, "close_price": close_price})
    return out


# ─────────────────────────── notifications ───────────────────────────

def insert_notification(position_id: int, kind: str, pct: float, message: str, created_at: str) -> int:
    with _lock, _conn:
        cur = _conn.execute("""
            INSERT INTO notifications (position_id, kind, pct, message, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (position_id, kind, pct, message, created_at))
        return cur.lastrowid


def list_notifications(unread_only: bool = False) -> list[dict]:
    with _lock:
        if unread_only:
            rows = _conn.execute(
                "SELECT id, position_id, kind, pct, message, created_at, read_at "
                "FROM notifications WHERE read_at IS NULL ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = _conn.execute(
                "SELECT id, position_id, kind, pct, message, created_at, read_at "
                "FROM notifications ORDER BY created_at DESC"
            ).fetchall()
    cols = ["id", "position_id", "kind", "pct", "message", "created_at", "read_at"]
    return [dict(zip(cols, r)) for r in rows]


def mark_notification_read(notification_id: int, read_at: str) -> None:
    with _lock, _conn:
        _conn.execute("UPDATE notifications SET read_at = ? WHERE id = ?", (read_at, notification_id))


def mark_all_notifications_read(read_at: str) -> None:
    with _lock, _conn:
        _conn.execute("UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (read_at,))


# ─────────────────────────── push subscriptions ───────────────────────────

def upsert_push_subscription(endpoint: str, p256dh: str, auth: str, now_iso: str) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth,
                last_seen_at = excluded.last_seen_at
        """, (endpoint, p256dh, auth, now_iso, now_iso))


def delete_push_subscription(endpoint: str) -> None:
    with _lock, _conn:
        _conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def list_push_subscriptions() -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions"
        ).fetchall()
    cols = ["id", "endpoint", "p256dh", "auth"]
    return [dict(zip(cols, r)) for r in rows]


def touch_push_subscription(endpoint: str, now_iso: str) -> None:
    with _lock, _conn:
        _conn.execute("UPDATE push_subscriptions SET last_seen_at = ? WHERE endpoint = ?", (now_iso, endpoint))

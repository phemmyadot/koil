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

            CREATE TABLE IF NOT EXISTS background_loop_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_close_fetch_at TEXT
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
                notes TEXT,
                -- Appended last -- must match ALTER TABLE's real column order (always appends),
                -- not this CREATE TABLE's text order, since _POSITION_COLUMNS reads positionally.
                last_alert_option_gain_pct REAL,
                -- "crush" | "spike" | NULL -- last IV-change flag alerted on, so a position only
                -- re-fires when the flag actually changes (crush->recovered->crush again, etc.),
                -- not every cycle it stays in the same flagged state. See
                -- docs/superpowers/specs/2026-08-07-alert-system-audit.md.
                last_alert_iv_flag TEXT,
                -- Calendar date (YYYY-MM-DD) the expiry-risk alert last fired on this position --
                -- once per day, like earnings_alert_log but position- not ticker-scoped since
                -- TP/expiry are position-specific even for the same ticker.
                last_alert_expiry_date TEXT,
                -- "BEARISH" | NULL -- last trend-filter state alerted on, mirrors last_alert_iv_flag's
                -- change-only re-fire logic.
                last_alert_trend_state TEXT,
                -- Running high-water-mark, same shape as last_alert_option_gain_pct but for spot
                -- unrealized% (option_gain alert is option-only).
                last_alert_spot_gain_pct REAL,
                -- 0/1 -- one-shot "gain achieved early relative to this strategy's average hold
                -- time" flag, never un-fires (unlike the threshold columns, there's only one
                -- band here, not several to re-cross).
                last_alert_early_profit INTEGER,
                -- "exceeded" | "deep" | NULL -- last MAE severity level alerted on, same
                -- change-only re-fire shape as last_alert_iv_flag/last_alert_trend_state.
                last_alert_mae_level TEXT
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
                position_id INTEGER,
                kind TEXT NOT NULL,
                pct REAL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read_at);

            -- Ticker-scoped (not position-scoped): a ticker held across multiple open positions
            -- still gets exactly one earnings push per calendar day -- see
            -- _fire_earnings_alerts() in app.py.
            CREATE TABLE IF NOT EXISTS earnings_alert_log (
                ticker TEXT PRIMARY KEY,
                alert_date TEXT NOT NULL
            );

            -- Same shape as earnings_alert_log, one row per (symbol, kind) -- e.g. ("USO",
            -- "oil_spike"), ("QQQ", "nasdaq_selloff") -- so distinct market-context alert kinds
            -- on the same symbol don't clobber each other's dedup state. See
            -- _fire_market_context_alerts() in app.py.
            CREATE TABLE IF NOT EXISTS market_context_alert_log (
                symbol TEXT NOT NULL,
                kind TEXT NOT NULL,
                alert_date TEXT NOT NULL,
                PRIMARY KEY (symbol, kind)
            );

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

            -- Daily trade review chatbot -- see
            -- docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md. user_id is
            -- DEFAULT_USER_ID (app.py) today, a real FK once multi-user auth lands (see
            -- 2026-08-04-multi-user-trades-design.md) -- every table here is born user-scoped so
            -- that migration needs zero schema change later, only a different value written in.
            CREATE TABLE IF NOT EXISTS user_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_user_documents_user ON user_documents(user_id);

            -- document_id/chunk_index are NULL for enrichment chunks (source != 'upload') --
            -- those aren't tied to the originally-uploaded file, they're tied to the review/chat
            -- that produced them (source_review_id) instead. See design doc Part 1, "The
            -- document is a living memory store."
            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                document_id INTEGER REFERENCES user_documents(id),
                source TEXT NOT NULL,
                source_review_id INTEGER,
                chunk_index INTEGER,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_document_chunks_document ON document_chunks(document_id);
            CREATE INDEX IF NOT EXISTS idx_document_chunks_user ON document_chunks(user_id);

            -- review_date is the TRADING date this review covers (the date whose 4pm close data
            -- was used), not the calendar date of every chat message -- a review started Tue 4pm
            -- and chatted on through Wed 6am is still "Tuesday's review." status is 'active'
            -- (exactly one review per user can be active at a time) or 'locked' (every past
            -- review, permanently, once its cycle ends at 7am -- see design doc Part 7).
            CREATE TABLE IF NOT EXISTS daily_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                review_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                summary_text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (user_id, review_date)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_reviews_user ON daily_reviews(user_id);

            -- No direct user_id -- reached via review_id, same "inherit scoping through the
            -- parent" pattern as position_fills/trade_daily_marks. role='system' is the 7am
            -- lock-notice row (see design doc Part 7), distinct from 'assistant' so the frontend
            -- can style it as a system notice rather than something Claude generated.
            CREATE TABLE IF NOT EXISTS review_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id INTEGER NOT NULL REFERENCES daily_reviews(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_review_chat_review ON review_chat_messages(review_id);

            -- Singleton per user (not a hardcoded id=1 singleton like universe_meta -- that
            -- pattern breaks the moment a second user exists). Overwritten each time, no version
            -- history, per the design doc's v1 decision.
            CREATE TABLE IF NOT EXISTS review_memory_summary (
                user_id INTEGER PRIMARY KEY,
                summary_text TEXT NOT NULL,
                last_updated_review_id INTEGER NOT NULL REFERENCES daily_reviews(id),
                updated_at TEXT NOT NULL
            );
        """)
    _migrate_notifications_position_id_nullable()


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


def _migrate_notifications_position_id_nullable() -> None:
    """One-time migration: notifications.position_id/pct used to be NOT NULL, back when every
    notification was a TP/stop progress alert tied to a real Trades-tab position. See
    docs/superpowers/specs/2026-08-04-strategy-signal-transition-notifications-design.md --
    strategy state-change alerts (PENDING/OPEN/TP/NO SIGNAL on a ticker's own backtested signal,
    not a real position) have no position to attach to and no meaningful pct. Same
    create-copy-drop-rename dance as _migrate_position_fills_price_nullable; a fresh DB never
    hits this since CREATE TABLE IF NOT EXISTS above already declares both nullable."""
    with _lock, _conn:
        cols = _conn.execute("PRAGMA table_info(notifications)").fetchall()
        position_id_col = next((c for c in cols if c[1] == "position_id"), None)
        if position_id_col is None or position_id_col[3] == 0:  # column[3] is "notnull"
            return
        _conn.execute("""
            CREATE TABLE notifications_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER,
                kind TEXT NOT NULL,
                pct REAL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            )
        """)
        _conn.execute("""
            INSERT INTO notifications_new (id, position_id, kind, pct, message, created_at, read_at)
            SELECT id, position_id, kind, pct, message, created_at, read_at FROM notifications
        """)
        _conn.execute("DROP TABLE notifications")
        _conn.execute("ALTER TABLE notifications_new RENAME TO notifications")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read_at)")


def _migrate_positions_add_option_gain_alert_column() -> None:
    with _lock, _conn:
        cols = [row[1] for row in _conn.execute("PRAGMA table_info(positions)").fetchall()]
        if "last_alert_option_gain_pct" in cols:
            return
        _conn.execute("ALTER TABLE positions ADD COLUMN last_alert_option_gain_pct REAL")


def _migrate_positions_add_alert_framework_columns() -> None:
    """See docs/superpowers/specs/2026-08-07-alert-system-audit.md -- ALTER TABLE always appends
    at the physical end of the table regardless of where a column appears in CREATE TABLE's text
    (see this project's own prior migration gotcha), so _POSITION_COLUMNS's order below must
    match the order these ALTERs actually run in, not the CREATE TABLE text order."""
    with _lock, _conn:
        cols = [row[1] for row in _conn.execute("PRAGMA table_info(positions)").fetchall()]
        new_cols = [
            ("last_alert_iv_flag", "TEXT"),
            ("last_alert_expiry_date", "TEXT"),
            ("last_alert_trend_state", "TEXT"),
            ("last_alert_spot_gain_pct", "REAL"),
            ("last_alert_early_profit", "INTEGER"),
            ("last_alert_mae_level", "TEXT"),
        ]
        for name, sql_type in new_cols:
            if name in cols:
                continue
            _conn.execute(f"ALTER TABLE positions ADD COLUMN {name} {sql_type}")


_init_schema()
_migrate_positions_add_option_gain_alert_column()
_migrate_positions_add_alert_framework_columns()


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
    """cached_earnings_dates() can call this for the same ticker from two overlapping compute
    passes (e.g. the background loop and a manual per-ticker fetch both seeing a cache miss at
    once) -- DELETE-then-INSERT let one thread's DELETE run between the other's DELETE and
    INSERT, so both would try to insert the same (ticker, earnings_date) row and one would hit
    the UNIQUE constraint (seen in production: NTNX's whole strategy compute failing on this
    IntegrityError, silently discarding its correctly-computed prebreak result along with it --
    see _compute_one()'s own fix for that separate failure mode). INSERT OR REPLACE is atomic
    per row, so two racing upserts just each apply cleanly instead of conflicting."""
    with _lock, _conn:
        _conn.execute("DELETE FROM earnings_dates WHERE ticker = ?", (ticker,))
        if len(dates):
            _conn.executemany(
                "INSERT OR REPLACE INTO earnings_dates (ticker, earnings_date, fetched_at) VALUES (?, ?, ?)",
                [(ticker, d.strftime("%Y-%m-%d"), fetched_at) for d in dates]
            )
        else:
            # Still record that we checked, with a phantom row-less marker --
            # store a single sentinel row so get_earnings_dates's TTL check
            # has something to key off even when a ticker genuinely has no
            # upcoming/recent earnings dates at all.
            _conn.execute(
                "INSERT OR REPLACE INTO earnings_dates (ticker, earnings_date, fetched_at) VALUES (?, ?, ?)",
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


# ─────────────────────── background loop marker ───────────────────────
# See docs/superpowers/specs/2026-08-03-market-hours-background-fetch-design.md.
# Tracks the last time the background loop did its once-per-close-period fetch,
# so a process restart doesn't forget it already ran today's and redo it immediately.

def get_last_close_fetch_at() -> str | None:
    with _lock:
        row = _conn.execute("SELECT last_close_fetch_at FROM background_loop_meta WHERE id = 1").fetchone()
    return row[0] if row else None


def set_last_close_fetch_at(iso: str) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO background_loop_meta (id, last_close_fetch_at) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_close_fetch_at=excluded.last_close_fetch_at
        """, (iso,))


# ─────────────────────────── positions ───────────────────────────
# See docs/superpowers/specs/2026-07-31-position-fills-design.md. A position is one row per
# ticker "campaign" -- status/avg-cost/P&L are all derived by replaying its position_fills
# (see backend/app.py's replay_fills()), never stored directly except status/closed_at, which
# are kept as a fast-lookup cache of that same replay (see the design doc's "Status derivation"
# section for why: _active_tickers()/the alert engine need "all open positions" as an indexed
# query, not a full fills-replay per ticker on every request).

_POSITION_COLUMNS = [
    "id", "ticker", "status", "tp_price", "stop_price", "opened_at", "closed_at",
    "last_alert_tp_pct", "last_alert_stop_pct", "notes", "last_alert_option_gain_pct",
    "last_alert_iv_flag", "last_alert_expiry_date", "last_alert_trend_state",
    "last_alert_spot_gain_pct", "last_alert_early_profit", "last_alert_mae_level",
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
                               stop_pct: float | None = None, option_gain_pct: float | None = None) -> None:
    """Bumps last_alert_tp_pct/last_alert_stop_pct/last_alert_option_gain_pct -- only the
    side(s) passed are touched, so the alert engine can update one without clobbering another."""
    if tp_pct is None and stop_pct is None and option_gain_pct is None:
        return
    with _lock, _conn:
        if tp_pct is not None:
            _conn.execute("UPDATE positions SET last_alert_tp_pct = ? WHERE id = ?", (tp_pct, position_id))
        if stop_pct is not None:
            _conn.execute("UPDATE positions SET last_alert_stop_pct = ? WHERE id = ?", (stop_pct, position_id))
        if option_gain_pct is not None:
            _conn.execute("UPDATE positions SET last_alert_option_gain_pct = ? WHERE id = ?", (option_gain_pct, position_id))


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

def insert_notification(position_id: int | None, kind: str, pct: float | None, message: str, created_at: str) -> int:
    with _lock, _conn:
        cur = _conn.execute("""
            INSERT INTO notifications (position_id, kind, pct, message, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (position_id, kind, pct, message, created_at))
        return cur.lastrowid


def get_earnings_alert_date(ticker: str) -> str | None:
    with _lock:
        row = _conn.execute(
            "SELECT alert_date FROM earnings_alert_log WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row[0] if row else None


def set_earnings_alert_date(ticker: str, alert_date: str) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO earnings_alert_log (ticker, alert_date) VALUES (?, ?)
            ON CONFLICT (ticker) DO UPDATE SET alert_date = excluded.alert_date
        """, (ticker, alert_date))


def get_market_context_alert_date(symbol: str, kind: str) -> str | None:
    with _lock:
        row = _conn.execute(
            "SELECT alert_date FROM market_context_alert_log WHERE symbol = ? AND kind = ?", (symbol, kind)
        ).fetchone()
    return row[0] if row else None


def set_market_context_alert_date(symbol: str, kind: str, alert_date: str) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO market_context_alert_log (symbol, kind, alert_date) VALUES (?, ?, ?)
            ON CONFLICT (symbol, kind) DO UPDATE SET alert_date = excluded.alert_date
        """, (symbol, kind, alert_date))


NOTIFICATIONS_LIST_MIN = 20  # see list_notifications's own docstring


def list_notifications(unread_only: bool = False) -> list[dict]:
    """unread_only=True: every unread row, no cap (this is what drives the bell's badge count,
    which must always be a true count). unread_only=False: all unread rows are ALWAYS included
    (never trimmed to fit a limit), topped up with the most recent read rows until the total
    reaches NOTIFICATIONS_LIST_MIN (20) -- so >=20 unread shows exactly those with no read rows
    mixed in, and <20 unread fills the rest of the panel with recent history instead of leaving
    it looking sparse. See
    docs/superpowers/specs/2026-08-04-strategy-signal-transition-notifications-design.md."""
    cols = ["id", "position_id", "kind", "pct", "message", "created_at", "read_at"]
    with _lock:
        if unread_only:
            rows = _conn.execute(
                "SELECT id, position_id, kind, pct, message, created_at, read_at "
                "FROM notifications WHERE read_at IS NULL ORDER BY created_at DESC"
            ).fetchall()
            return [dict(zip(cols, r)) for r in rows]

        unread_rows = _conn.execute(
            "SELECT id, position_id, kind, pct, message, created_at, read_at "
            "FROM notifications WHERE read_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
        read_needed = max(NOTIFICATIONS_LIST_MIN - len(unread_rows), 0)
        read_rows = []
        if read_needed:
            read_rows = _conn.execute(
                "SELECT id, position_id, kind, pct, message, created_at, read_at "
                "FROM notifications WHERE read_at IS NOT NULL ORDER BY created_at DESC LIMIT ?",
                (read_needed,),
            ).fetchall()
    rows = sorted(unread_rows + read_rows, key=lambda r: r[5], reverse=True)  # r[5] = created_at
    return [dict(zip(cols, r)) for r in rows]


def mark_notification_read(notification_id: int, read_at: str) -> None:
    with _lock, _conn:
        _conn.execute("UPDATE notifications SET read_at = ? WHERE id = ?", (read_at, notification_id))


def mark_all_notifications_read(read_at: str) -> None:
    with _lock, _conn:
        _conn.execute("UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (read_at,))


def list_notifications_since(since_iso: str) -> list[dict]:
    """Uncapped, unlike list_notifications() (which tops out at NOTIFICATIONS_LIST_MIN for the
    bell panel) -- used by the daily review snapshot (review_snapshot.py), which needs every
    notification from today, not a fixed-size recent-history window."""
    with _lock:
        rows = _conn.execute(
            "SELECT id, position_id, kind, pct, message, created_at, read_at "
            "FROM notifications WHERE created_at >= ? ORDER BY created_at DESC",
            (since_iso,),
        ).fetchall()
    cols = ["id", "position_id", "kind", "pct", "message", "created_at", "read_at"]
    return [dict(zip(cols, r)) for r in rows]


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


# ─────────────────────── daily review chatbot ───────────────────────
# See docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md.

def insert_user_document(user_id: int, filename: str, file_path: str, file_type: str, uploaded_at: str) -> int:
    with _lock, _conn:
        cur = _conn.execute("""
            INSERT INTO user_documents (user_id, filename, file_path, file_type, uploaded_at, status)
            VALUES (?, ?, ?, ?, ?, 'processing')
        """, (user_id, filename, file_path, file_type, uploaded_at))
        return cur.lastrowid


def update_user_document_status(document_id: int, status: str) -> None:
    with _lock, _conn:
        _conn.execute("UPDATE user_documents SET status = ? WHERE id = ?", (status, document_id))


def get_active_user_document(user_id: int) -> dict | None:
    """Most recently uploaded 'ready' document for this user -- see design doc's "First-visit
    onboarding prompt" (upload is a once-per-lifecycle action, so there's normally at most one,
    but a re-upload isn't hard-blocked, hence "most recent" not "the only one")."""
    with _lock:
        row = _conn.execute("""
            SELECT id, user_id, filename, file_path, file_type, uploaded_at, status
            FROM user_documents WHERE user_id = ? AND status = 'ready'
            ORDER BY uploaded_at DESC LIMIT 1
        """, (user_id,)).fetchone()
    if row is None:
        return None
    cols = ["id", "user_id", "filename", "file_path", "file_type", "uploaded_at", "status"]
    return dict(zip(cols, row))


def insert_document_chunk(user_id: int, document_id: int | None, source: str, source_review_id: int | None,
                           chunk_index: int | None, content: str, embedding_bytes: bytes, created_at: str) -> int:
    with _lock, _conn:
        cur = _conn.execute("""
            INSERT INTO document_chunks
                (user_id, document_id, source, source_review_id, chunk_index, content, embedding, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, document_id, source, source_review_id, chunk_index, content, embedding_bytes, created_at))
        return cur.lastrowid


def get_document_chunks(user_id: int) -> list[dict]:
    with _lock:
        rows = _conn.execute("""
            SELECT id, user_id, document_id, source, source_review_id, chunk_index, content, embedding, created_at
            FROM document_chunks WHERE user_id = ?
        """, (user_id,)).fetchall()
    cols = ["id", "user_id", "document_id", "source", "source_review_id", "chunk_index", "content", "embedding", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def insert_daily_review(user_id: int, review_date: str, summary_text: str, embedding_bytes: bytes,
                         snapshot_json: str, created_at: str) -> int:
    with _lock, _conn:
        cur = _conn.execute("""
            INSERT INTO daily_reviews (user_id, review_date, status, summary_text, embedding, snapshot_json, created_at)
            VALUES (?, ?, 'active', ?, ?, ?, ?)
        """, (user_id, review_date, summary_text, embedding_bytes, snapshot_json, created_at))
        return cur.lastrowid


def get_daily_review(user_id: int, review_date: str) -> dict | None:
    with _lock:
        row = _conn.execute("""
            SELECT id, user_id, review_date, status, summary_text, embedding, snapshot_json, created_at
            FROM daily_reviews WHERE user_id = ? AND review_date = ?
        """, (user_id, review_date)).fetchone()
    if row is None:
        return None
    cols = ["id", "user_id", "review_date", "status", "summary_text", "embedding", "snapshot_json", "created_at"]
    return dict(zip(cols, row))


def get_active_daily_review(user_id: int) -> dict | None:
    """The one review currently in status='active', if any -- see design doc Part 7 (exactly one
    review cycle can be active per user at a time)."""
    with _lock:
        row = _conn.execute("""
            SELECT id, user_id, review_date, status, summary_text, embedding, snapshot_json, created_at
            FROM daily_reviews WHERE user_id = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """, (user_id,)).fetchone()
    if row is None:
        return None
    cols = ["id", "user_id", "review_date", "status", "summary_text", "embedding", "snapshot_json", "created_at"]
    return dict(zip(cols, row))


def set_daily_review_status(review_id: int, status: str) -> None:
    with _lock, _conn:
        _conn.execute("UPDATE daily_reviews SET status = ? WHERE id = ?", (status, review_id))


def list_review_chat_messages(review_id: int) -> list[dict]:
    with _lock:
        rows = _conn.execute("""
            SELECT id, review_id, role, content, created_at
            FROM review_chat_messages WHERE review_id = ? ORDER BY created_at ASC, id ASC
        """, (review_id,)).fetchall()
    cols = ["id", "review_id", "role", "content", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def insert_review_chat_message(review_id: int, role: str, content: str, created_at: str) -> int:
    with _lock, _conn:
        cur = _conn.execute("""
            INSERT INTO review_chat_messages (review_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
        """, (review_id, role, content, created_at))
        return cur.lastrowid


def get_review_memory_summary(user_id: int) -> dict | None:
    with _lock:
        row = _conn.execute("""
            SELECT user_id, summary_text, last_updated_review_id, updated_at
            FROM review_memory_summary WHERE user_id = ?
        """, (user_id,)).fetchone()
    if row is None:
        return None
    cols = ["user_id", "summary_text", "last_updated_review_id", "updated_at"]
    return dict(zip(cols, row))


def upsert_review_memory_summary(user_id: int, summary_text: str, last_updated_review_id: int, updated_at: str) -> None:
    with _lock, _conn:
        _conn.execute("""
            INSERT INTO review_memory_summary (user_id, summary_text, last_updated_review_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET summary_text=excluded.summary_text,
                last_updated_review_id=excluded.last_updated_review_id, updated_at=excluded.updated_at
        """, (user_id, summary_text, last_updated_review_id, updated_at))

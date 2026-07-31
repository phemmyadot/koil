# Price Data Storage: Incremental Fetch + Real DB

**STATUS: IMPLEMENTED.** This doc was the design proposal; the migration described below has been carried out. `backend/db.py` exists with all five tables, `backend/data.py`/`backend/app.py`/`backend/scoring.py` all read/write through it, and `backend/migrate_pickle_to_db.py` handles the one-time pickle→DB migration for any server that already had data in the old `price_cache.pkl`/`computed_cache.pkl`/`earnings_cache.pkl`/`universe_last_screened.txt` files. See `backend/caching_and_data_retrieval.md` for the current, as-built description -- the rest of this document is kept as the original design rationale for why each decision was made.

**Scope note:** this doc originally covered price bars only. Since it was written, two more pieces of state went from "doesn't exist" to "real, shipped, in-memory-only code" that has the exact same problem price bars had: `backend/app.py`'s `_computed`/`_computed_source_fetch` (per-ticker strategy results, briefly persisted to `computed_cache.pkl` before this migration) and `backend/scoring.py`'s `_earnings_cache` (per-ticker earnings dates, 24h TTL, briefly unpersisted before this migration). All three are covered below as one schema, since a real DB migration was done for them together rather than as three separate piecemeal changes.

## Problem, precisely

`backend/data.py`'s `_fetch_one()` always does:

```python
yf.download(ticker, start=HISTORY_START, interval="1d", ...)
```

`HISTORY_START = "2021-01-01"` — fixed, never moves. Every time a ticker needs refetching (TTL expired, cold cache, manual Refresh), it re-downloads the **entire ~4.5-year history**, even though on a normal day only 1 new daily bar exists since the last fetch. This is true regardless of storage format — pickle today, or a DB tomorrow, doesn't change what gets *requested from Yahoo* unless the fetch logic itself changes.

**This is the actual bottleneck your idea targets.** Moving to a DB alone does not fix it — you'd still be asking Yahoo for 4.5 years of daily bars every refresh, just storing the result differently. The fix is incremental fetching: if a ticker has data through date X, only request `X+1` onward.

A DB becomes valuable *because* it makes incremental storage (per-row upsert, not whole-blob rewrite) natural — which is exactly the shape your proposal describes ("if in DB, only fetch from last update; if not, fetch all; keep permanently even if unused"). So the two ideas are complementary, not separate: DB is the storage layer that makes incremental fetch clean to implement and durable.

## Current architecture (for contrast)

- `backend/price_cache.pkl` — one pickle file, one Python dict: `{"bars": {ticker: DataFrame}, "fetched_at": {ticker: epoch}}`.
- Whole-file read on process start (`_load_price_cache()`), whole-file rewrite on every `warm_cache()` batch (`_save_price_cache()`).
- Per-ticker TTL (`_cache_is_fresh()`): same-day (ET), plus a 2-hour re-check while market is open.
- `warm_cache(tickers, force=False)`: skips tickers whose cache entry is fresh; force=True (manual Refresh) ignores TTL and refetches everyone's **full history**.
- Currently ~270KB for a ~200-ticker cache (per today's file); at ~2000 tickers this would be several MB — still small enough to hold entirely in RAM and pickle whole, but every save serializes the *entire* dict even if only 3 tickers changed.

This works. It is not broken. The two real weaknesses are: (1) full-history refetch (addressed above) and (2) whole-blob read/write meaning a corrupt pickle loses everything, and every save pays for every ticker's data even when 1997 of 2000 didn't change.

## Proposed architecture

### Storage: SQLite, one DB file, five tables

SQLite, not Postgres/MySQL: this is a single-process app on one box, no concurrent writers from multiple hosts, no need for a network round-trip to the DB. SQLite is a file (`backend/app_data.db`) — same bind-mount deployment story as `tickers.py`/`price_cache.pkl` today, zero new infrastructure (no separate DB container, no connection string, no new failure mode of "DB is down"). Python's `sqlite3` is stdlib — no new dependency. One file, one connection, covers price bars + computed results + earnings dates — no reason to split these across separate DB files when they're all small, all owned by this one process, and some queries (e.g. "is this ticker's computed result still valid") need to reason about both `bars`/`fetch_meta` and `computed_results` together.

```sql
-- ── Price bars (backend/data.py's _raw_cache) ──────────────────────────────
CREATE TABLE bars (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,   -- ISO date, e.g. '2026-07-24'
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX idx_bars_ticker ON bars(ticker);

CREATE TABLE fetch_meta (
    ticker TEXT PRIMARY KEY,
    last_fetched_at REAL NOT NULL,   -- epoch, mirrors today's _fetched_at
    last_bar_date TEXT NOT NULL,     -- most recent date actually stored -- the incremental-fetch anchor
    last_error TEXT                  -- mirrors today's _raw_errors; NULL if last fetch succeeded
);

-- ── Computed strategy results (backend/app.py's _computed) ─────────────────
-- One row per ticker -- the full per-ticker payload (VEXH/VCP/VCPO/prebreak/
-- setup_score) stored as JSON, not normalized into columns. This blob is
-- deeply nested, per-strategy-shaped, and evolves often (new strategies,
-- new scoring dimensions) -- normalizing it into relational columns would
-- mean a schema migration every time backend/scoring.py or backend/score.py
-- gains a new field. SQLite's JSON1 extension (compiled in by default since
-- 3.38, bundled with Python's sqlite3 on any remotely current Python) lets
-- SQL still query into it (json_extract(payload, '$.score')) if ever needed
-- without committing to a rigid column-per-field schema now.
CREATE TABLE computed_results (
    ticker TEXT PRIMARY KEY,
    payload TEXT,                     -- JSON-serialized full payload dict; NULL if the compute
                                      -- attempt errored (see `error` below) -- nullable, not
                                      -- NOT NULL, since a ticker can have an error with no payload
    source_fetched_at REAL NOT NULL, -- the fetch_meta.last_fetched_at this was computed FROM --
                                      -- the staleness key: recompute only if this no longer
                                      -- matches fetch_meta.last_fetched_at for the same ticker
    computed_at REAL NOT NULL,       -- epoch this row was last written
    error TEXT                       -- mirrors today's _computed_errors; NULL if compute succeeded
);

-- ── Earnings dates (backend/scoring.py's _earnings_cache) ───────────────────
-- Currently NOT persisted at all -- every restart re-fetches every ticker's
-- earnings calendar from Yahoo before its 24h in-memory TTL would have
-- required it, purely because the process restarted. Same class of bug
-- fixed for price bars, not yet fixed here.
CREATE TABLE earnings_dates (
    ticker TEXT NOT NULL,
    earnings_date TEXT NOT NULL,     -- ISO date of one reported/expected earnings date
    fetched_at REAL NOT NULL,        -- when this ticker's earnings calendar was last fetched --
                                      -- the 24h TTL check today's _EARNINGS_CACHE_TTL applies to
    PRIMARY KEY (ticker, earnings_date)
);
CREATE INDEX idx_earnings_ticker ON earnings_dates(ticker);

-- ── Universe screening marker (backend/app.py's universe_last_screened.txt) ─
-- Folded in for completeness -- currently a one-line text file, not a real
-- table's worth of data, but no reason to keep a 6th small file around once
-- the other three caches move into one DB. Single row, ticker/id irrelevant.
CREATE TABLE universe_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- enforces exactly one row
    last_screened_date TEXT NOT NULL         -- ISO date, mirrors today's file content
);
```

Table-by-table mapping from today's in-memory/on-disk state:

| Table | Replaces | Currently persisted? |
|---|---|---|
| `bars` | `data.py`'s `_raw_cache` | Yes -- `price_cache.pkl` (whole-blob pickle) |
| `fetch_meta` | `data.py`'s `_fetched_at` / `_raw_errors` | Yes -- inside `price_cache.pkl` |
| `computed_results` | `app.py`'s `_computed` / `_computed_source_fetch` / `_computed_errors` | Yes -- `computed_cache.pkl` (whole-blob pickle, added since this doc's first draft) |
| `earnings_dates` | `scoring.py`'s `_earnings_cache` | **No** -- pure in-memory, lost on every restart |
| `universe_meta` | `app.py`'s `universe_last_screened.txt` | Yes -- plain text file |

The `earnings_dates` gap is worth calling out on its own: it's the one piece of state in this whole app that currently has zero persistence at all. It doesn't cause wrong behavior (worst case is one redundant Yahoo earnings-calendar fetch per ticker per restart, degrading to "no earnings data" on failure per `_cached_earnings_dates`'s existing timeout handling), but it's the same class of unnecessary-Yahoo-traffic-on-every-redeploy problem the price-bar and computed-results caches were both built to fix, just not yet extended to earnings dates.

### Incremental fetch logic

```python
def _fetch_one(ticker: str) -> ...:
    last_date = get_last_bar_date(ticker)  # None if never fetched
    if last_date is None:
        start = HISTORY_START               # cold: full history, same as today
    else:
        start = last_date + timedelta(days=1)  # warm: only the gap
    df = yf.download(ticker, start=start, ...)
    if df.empty:
        return ticker, "up to date", None   # nothing new -- e.g. re-checked same day, weekend
    upsert_bars(ticker, df)                 # INSERT OR REPLACE, row per date
    return ticker, "ok", None
```

Weekend/holiday re-checks naturally no-op (`df.empty`) instead of needing the TTL to prevent them — the incremental request itself asks for a date range with nothing new in it, Yahoo returns empty, done. This is a secondary, minor win: it also makes the existing TTL logic (`_cache_is_fresh`) less load-bearing, though I'd keep it — no reason to make a network call at all if we already know today's bar isn't out yet.

### "Keep permanently, even if unused"

This is a real, distinct feature request beyond incremental fetch — currently there's no concept of "a ticker used to be in the universe, now isn't, but we still have its data." Today, if a ticker drops out of `backend/tickers.py` (fails the daily re-screen), its entry in `price_cache.pkl` just sits there unused until the pickle is regenerated from scratch (which never happens automatically) — so in practice it's already accidentally "kept forever," just not deliberately.

With a DB this becomes explicit and correct: `bars` table rows are never deleted just because a ticker drops out of the current universe. If a ticker re-enters the universe later (re-screened back in), `_fetch_one` finds its `last_bar_date` still there and only fetches the gap since — even if that gap is weeks or months, not years. This is a genuine, meaningful win specifically because of the DB's per-row structure; a whole-blob pickle *could* technically do the same (never prune the dict), but has no efficient way to fetch only the gap for a ticker that's been stale for months without also holding stale entries for thousands of other tickers in the same in-memory dict/file the whole time.

### API surface changes (`backend/data.py`)

Mostly additive, minimal disruption to `app.py`'s callers:

```python
def get_bars(ticker: str) -> pd.DataFrame | None:
    # Same signature/behavior as today -- reads from DB instead of the
    # in-memory dict, but every existing caller (backend/app.py's
    # _compute_one, strategy_vcp.py, etc.) is unaffected.
    ...

def warm_cache(tickers=None, force=False) -> None:
    # Same signature. force=True still means "ignore staleness" but no
    # longer means "refetch full history" -- it means "check for new bars
    # right now regardless of TTL," which is actually MORE correct for the
    # manual Refresh button's intent (get current data fast) than today's
    # behavior (redundantly re-download years of unchanged history).
    ...
```

`get_bars()` could either (a) query SQLite fresh on every call, reconstructing a DataFrame, or (b) keep an in-memory DataFrame cache populated from SQLite on startup and updated incrementally, with SQLite as the durable backing store rather than the hot path. **(b) is the right choice** — `_compute_one()` is called once per ticker per `compute_all()` pass, up to every 30 minutes; going to SQLite on every read adds real per-call overhead (query + reconstruct DataFrame) for no benefit, since the whole point of `backend/data.py`'s existing design ("a page request never triggers a network call") is that reads are from RAM. SQLite becomes the persistence layer underneath the same in-memory dict that exists today, not a replacement for it.

## What this does NOT fix

Worth being explicit, since it's tempting to read "move to a DB" as a bigger win than it is:

- **Compute time** (`compute_all()`, the VEXH backtest cost) — unrelated. This doc is entirely about the *fetch* side. See the separate VEXH duplicate-backtest-run fix already implemented in `backend/scoring.py`.
- **Yahoo rate limiting** — incremental fetch reduces the *size* of each request (1 day vs 4.5 years of data transferred), which may reduce how often Yahoo throttles, but doesn't change the *request count* (still one `yf.download()` call per ticker per refresh) or add real backoff/retry logic. Worth a separate look if rate-limiting remains a recurring problem after this lands.
- **The daily universe re-screen** (`build_universe.py`) — separate subsystem, separate data (candidate screening via `yf.screen()`, not price history), out of scope here.

## Migration path

1. Add `backend/db.py`: schema creation (idempotent `CREATE TABLE IF NOT EXISTS` for all five tables), plus the access functions each cache needs:
   - Price bars: `get_last_bar_date(ticker)`, `upsert_bars(ticker, df)`, `load_all_bars() -> dict[str, DataFrame]` (startup bulk-load into RAM, replaces `data.py`'s `_load_price_cache()`).
   - Computed results: `get_computed(ticker) -> dict | None`, `upsert_computed(ticker, payload, source_fetched_at, error)`, `load_all_computed() -> dict[str, dict]` (replaces `app.py`'s `_load_computed_cache()`).
   - Earnings dates: `get_earnings_dates(ticker) -> DatetimeIndex | None` (checks `fetched_at` against the TTL itself, returns `None` on miss/stale), `upsert_earnings_dates(ticker, dates)` (replaces `scoring.py`'s in-memory-only `_earnings_cache`).
   - Universe marker: `get_last_screened_date()`, `set_last_screened_date(date)` (replaces `app.py`'s `universe_last_screened.txt` read/write).
2. Update `data.py`'s `_fetch_one()` for incremental `start=` date, per above.
3. Update `data.py`'s `_save_price_cache()` equivalent to upsert only the tickers actually fetched this batch, not rewrite everything -- same for `app.py`'s `_save_computed_cache()` equivalent (upsert only tickers that were actually recomputed this pass, which per the reuse logic already shipped is usually a small subset of the full universe) -- this is where the DB's row-level granularity actually pays off versus today's whole-dict pickle rewrites.
4. Update `scoring.py`'s `_cached_earnings_dates()` to check the DB (via `get_earnings_dates`) before falling back to a live Yahoo fetch, and to write through `upsert_earnings_dates` on a successful fetch -- this is a net-new persistence layer, not a migration of existing on-disk data, since `_earnings_cache` was never saved anywhere.
5. One-time migration: on first startup after this change, for each of `price_cache.pkl` and `computed_cache.pkl` that exists while `app_data.db` doesn't yet, read the pickle and bulk-insert into the corresponding table, then the pickle can be deleted (or left as a dead file, gitignored either way). `universe_last_screened.txt` similarly seeds `universe_meta` once, then can be deleted. No migration needed for earnings dates -- there's nothing on disk to migrate from.
6. Update `docker-compose.yml`/`deploy.sh`/`.gitignore` — replace the `price_cache.pkl`/`computed_cache.pkl`/`universe_last_screened.txt` bind mounts with a single `app_data.db` mount (and drop the pickle files' `.tmp`-rename workaround entirely -- SQLite's own journal/WAL mode handles crash safety internally without touching the mounted file's inode, so none of the `os.replace()`-onto-a-bind-mount `EBUSY` issues that pickle hit apply here).

## Effort vs. payoff

Real, worthwhile change — but it's a fetch/persistence-layer change, not a compute-layer one (see the separate VEXH duplicate-backtest-run fix already implemented for the compute side), and the biggest single lever (avoiding full-history redownload) doesn't strictly require SQLite to implement; it could be done by changing `_fetch_one()`'s `start=` logic while keeping the pickle format, storing `last_bar_date` in the existing `_fetched_at`-style dict and appending new rows to each ticker's DataFrame in place. Same is true of `computed_results` and `earnings_dates` -- both could stay pickle-based (the former already does; the latter could gain a pickle cache with far less effort than a full DB migration).

The DB's actual differentiated value, beyond what incremental-pickle-everywhere would already get you: per-ticker durability (one corrupt row doesn't lose 2000 tickers' worth of data the way a corrupt pickle blob does today), avoids the O(all tickers) rewrite cost on every save (three separate whole-blob pickle writes today -- `price_cache.pkl`, `computed_cache.pkl`, and a hypothetical earnings pickle -- collapse into targeted row upserts), natural "keep forever" semantics for delisted/dropped tickers, and **one file instead of four** (`price_cache.pkl` + `computed_cache.pkl` + `universe_last_screened.txt` + a hypothetical earnings pickle → one `app_data.db`), which also simplifies `docker-compose.yml`/`deploy.sh` down to a single bind mount and a single missing-file guard instead of the current four.

Whether that's worth the migration effort right now depends on how much the "one corrupt pickle loses everything" risk and the "several small files to keep bind-mounted in sync" operational overhead actually bite in practice — neither has caused a real incident yet, per this session's work, but the pattern (bind-mount gotchas, `EBUSY` on atomic writes, remembering to add each new cache file to `.gitignore`/`docker-compose.yml`/`deploy.sh` in three places) has already repeated three times for three separate caches.

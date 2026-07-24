# Price Data Storage: Incremental Fetch + Real DB

## Problem, precisely

`webapp/data.py`'s `_fetch_one()` always does:

```python
yf.download(ticker, start=HISTORY_START, interval="1d", ...)
```

`HISTORY_START = "2021-01-01"` — fixed, never moves. Every time a ticker needs refetching (TTL expired, cold cache, manual Refresh), it re-downloads the **entire ~4.5-year history**, even though on a normal day only 1 new daily bar exists since the last fetch. This is true regardless of storage format — pickle today, or a DB tomorrow, doesn't change what gets *requested from Yahoo* unless the fetch logic itself changes.

**This is the actual bottleneck your idea targets.** Moving to a DB alone does not fix it — you'd still be asking Yahoo for 4.5 years of daily bars every refresh, just storing the result differently. The fix is incremental fetching: if a ticker has data through date X, only request `X+1` onward.

A DB becomes valuable *because* it makes incremental storage (per-row upsert, not whole-blob rewrite) natural — which is exactly the shape your proposal describes ("if in DB, only fetch from last update; if not, fetch all; keep permanently even if unused"). So the two ideas are complementary, not separate: DB is the storage layer that makes incremental fetch clean to implement and durable.

## Current architecture (for contrast)

- `webapp/price_cache.pkl` — one pickle file, one Python dict: `{"bars": {ticker: DataFrame}, "fetched_at": {ticker: epoch}}`.
- Whole-file read on process start (`_load_price_cache()`), whole-file rewrite on every `warm_cache()` batch (`_save_price_cache()`).
- Per-ticker TTL (`_cache_is_fresh()`): same-day (ET), plus a 2-hour re-check while market is open.
- `warm_cache(tickers, force=False)`: skips tickers whose cache entry is fresh; force=True (manual Refresh) ignores TTL and refetches everyone's **full history**.
- Currently ~270KB for a ~200-ticker cache (per today's file); at ~2000 tickers this would be several MB — still small enough to hold entirely in RAM and pickle whole, but every save serializes the *entire* dict even if only 3 tickers changed.

This works. It is not broken. The two real weaknesses are: (1) full-history refetch (addressed above) and (2) whole-blob read/write meaning a corrupt pickle loses everything, and every save pays for every ticker's data even when 1997 of 2000 didn't change.

## Proposed architecture

### Storage: SQLite, one row per (ticker, date)

```sql
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
    last_bar_date TEXT NOT NULL      -- most recent date actually stored -- the incremental-fetch anchor
);
```

SQLite, not Postgres/MySQL: this is a single-process app on one box, no concurrent writers from multiple hosts, no need for a network round-trip to the DB. SQLite is a file (`webapp/price_data.db`) — same bind-mount deployment story as `tickers.py`/`price_cache.pkl` today, zero new infrastructure (no separate DB container, no connection string, no new failure mode of "DB is down"). Python's `sqlite3` is stdlib — no new dependency.

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

This is a real, distinct feature request beyond incremental fetch — currently there's no concept of "a ticker used to be in the universe, now isn't, but we still have its data." Today, if a ticker drops out of `webapp/tickers.py` (fails the daily re-screen), its entry in `price_cache.pkl` just sits there unused until the pickle is regenerated from scratch (which never happens automatically) — so in practice it's already accidentally "kept forever," just not deliberately.

With a DB this becomes explicit and correct: `bars` table rows are never deleted just because a ticker drops out of the current universe. If a ticker re-enters the universe later (re-screened back in), `_fetch_one` finds its `last_bar_date` still there and only fetches the gap since — even if that gap is weeks or months, not years. This is a genuine, meaningful win specifically because of the DB's per-row structure; a whole-blob pickle *could* technically do the same (never prune the dict), but has no efficient way to fetch only the gap for a ticker that's been stale for months without also holding stale entries for thousands of other tickers in the same in-memory dict/file the whole time.

### API surface changes (`webapp/data.py`)

Mostly additive, minimal disruption to `app.py`'s callers:

```python
def get_bars(ticker: str) -> pd.DataFrame | None:
    # Same signature/behavior as today -- reads from DB instead of the
    # in-memory dict, but every existing caller (webapp/app.py's
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

`get_bars()` could either (a) query SQLite fresh on every call, reconstructing a DataFrame, or (b) keep an in-memory DataFrame cache populated from SQLite on startup and updated incrementally, with SQLite as the durable backing store rather than the hot path. **(b) is the right choice** — `_compute_one()` is called once per ticker per `compute_all()` pass, up to every 30 minutes; going to SQLite on every read adds real per-call overhead (query + reconstruct DataFrame) for no benefit, since the whole point of `webapp/data.py`'s existing design ("a page request never triggers a network call") is that reads are from RAM. SQLite becomes the persistence layer underneath the same in-memory dict that exists today, not a replacement for it.

## What this does NOT fix

Worth being explicit, since it's tempting to read "move to a DB" as a bigger win than it is:

- **Compute time** (`compute_all()`, the VEXH backtest cost) — unrelated. This doc is entirely about the *fetch* side. See the separate VEXH duplicate-backtest-run fix already implemented in `webapp/scoring.py`.
- **Yahoo rate limiting** — incremental fetch reduces the *size* of each request (1 day vs 4.5 years of data transferred), which may reduce how often Yahoo throttles, but doesn't change the *request count* (still one `yf.download()` call per ticker per refresh) or add real backoff/retry logic. Worth a separate look if rate-limiting remains a recurring problem after this lands.
- **The daily universe re-screen** (`build_universe.py`) — separate subsystem, separate data (candidate screening via `yf.screen()`, not price history), out of scope here.

## Migration path

1. Add `webapp/db.py`: schema creation (idempotent `CREATE TABLE IF NOT EXISTS`), `get_last_bar_date(ticker)`, `upsert_bars(ticker, df)`, `load_all_bars() -> dict[str, DataFrame]` (startup bulk-load into RAM, replaces `_load_price_cache()`).
2. Update `_fetch_one()` for incremental `start=` date, per above.
3. Update `_save_price_cache()`'s equivalent to upsert only the tickers that were actually fetched this batch, not rewrite everything — this is where the DB's row-level granularity actually pays off versus today's whole-dict pickle rewrite.
4. One-time migration: on first startup after this change, if `price_cache.pkl` exists and `price_data.db` doesn't, read the pickle and bulk-insert into SQLite, then the pickle can be deleted (or left as a dead file, gitignored either way).
5. Update `docker-compose.yml`/`deploy.sh`/`.gitignore` — same bind-mount pattern, swap `price_cache.pkl` for `price_data.db` (and drop the `.tmp` rename entry, since SQLite doesn't need the atomic-swap-onto-a-bind-mount workaround pickle needed — SQLite's own journal/WAL mode handles crash safety internally without touching the mounted file's inode).

## Effort vs. payoff

Real, worthwhile change — but it's a fetch-layer change, not a compute-layer one, and the biggest lever (avoiding full-history redownload) doesn't strictly require SQLite to implement; it could be done by changing `_fetch_one()`'s `start=` logic while keeping the pickle format, storing `last_bar_date` in the existing `_fetched_at`-style dict and appending new rows to each ticker's DataFrame in place. The DB adds real value on top of that (per-ticker durability, no whole-blob corruption risk, natural "keep forever" semantics, avoids the O(all tickers) rewrite cost on every save) but is the more expensive path of the two viable options.

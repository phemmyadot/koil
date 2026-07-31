# Caching & Data Retrieval

How the dashboard gets data from Yahoo Finance to the browser, what's cached where, and why. Four separate pieces of state, one shared SQLite database (`webapp/db.py`), each with its own staleness rule.

## The database — `webapp/db.py`

Everything persistent lives in one file, `webapp/app_data.db` (SQLite, WAL mode), across five tables:

| # | Table(s) | What | Owner | TTL / staleness rule |
|---|---|---|---|---|
| 1 | (n/a — `webapp/tickers.py` + `universe_meta`) | Ticker universe (which symbols exist at all) | `build_universe.py` / `app.py` | Re-screened at most once/day, automatically |
| 2 | `bars`, `fetch_meta` | Raw price bars (OHLCV per ticker) | `data.py` | Same-day (ET), + every 2h while market open |
| 3 | `computed_results` | Computed strategy results (VEXH/VCP/VCPO/prebreak/score) | `app.py` | Recomputed only if the ticker's price bars actually changed |
| 4 | `earnings_dates` | Earnings dates | `scoring.py` | 24h |

`webapp/tickers.py` itself stays a generated `.py` file, not a DB table — it's imported directly (`from webapp.tickers import TICKERS`), not deserialized, so `importlib.reload()` works on it. Everything else that used to be separate pickle files (`price_cache.pkl`, `computed_cache.pkl`, `earnings_cache.pkl`) and a plain-text marker (`universe_last_screened.txt`) is now rows in this one DB. See `webapp/db_implementation.md` for the full design rationale and schema (that doc is now marked as implemented — this doc describes the as-built result).

**Why SQLite over the old pickle files:** the old design (one whole-blob pickle per cache) meant every save rewrote the *entire* file regardless of how many tickers actually changed, and a corrupt file lost everything in that cache at once. SQLite gives per-row upserts (touch one ticker, write one row) and per-ticker durability (a bad row doesn't take down the other ~2000). It also enabled the actual point of this migration: **incremental price fetching** — see below.

**Why SQLite over Postgres/MySQL:** single-process app on one box, no concurrent writers from other hosts, no need for a network round-trip. SQLite is a file, same bind-mount deployment story the pickle files had, zero new infrastructure. Python's `sqlite3` is stdlib.

**In-memory hot path, unchanged in spirit:** `data.py`'s `_raw_cache`/`_fetched_at`/`_raw_errors` and `app.py`'s `_computed`/`_computed_errors`/`_computed_source_fetch` are still plain Python dicts/lists in RAM — a page request is still a pure in-memory read, no per-request DB query. The DB is the durable backing store loaded into those dicts once on import (`db.load_all_bars()`, `db.load_all_computed()`), not a replacement for keeping hot data in RAM.

## The pipeline, end to end

```
build_universe.py          data.py                  app.py                   scoring.py
────────────────────       ─────────────────         ─────────────────        ──────────────
Yahoo screener API    →    tickers.py          →     TICKERS (in-memory)
                                                             │
                       Yahoo price history     →     _raw_cache              →  _compute_one()
                       (yf.download, START =    (db: bars, fetch_meta)           per ticker:
                        last stored date,                                         - VEXH (scoring.evaluate)
                        NOT full history)                                         - VCP / VCPO
                                                                                    - prebreak
                                                                                    - setup_score
                                                                             →  _computed
                                                                                (db: computed_results)
                       Yahoo earnings dates                                →  db.get_earnings_dates /
                       (per ticker, inside                                    upsert_earnings_dates
                        VEXH's evaluate)                                      (db: earnings_dates)

                                                      GET /api/tickers  ──────────────────────────►
                                                      (reads _computed, no network call, no compute)
```

Three separate things get fetched from Yahoo (universe screen, price bars, earnings dates), and one thing gets computed locally (the strategy evaluations, pure CPU, no network). Each has its own staleness rule because each has a different cost: screening ~2200 candidates takes ~60-90s and only needs to happen once a day; price bars need to be fresh multiple times a day while the market's open; earnings dates barely change at all; and the compute step should only redo work for tickers whose inputs actually changed.

## 1. Ticker universe — `webapp/tickers.py` + `universe_meta` table

**How it's built** (`build_universe.py`):
1. `fetch_candidates()` — Yahoo's equity screener API (`yf.screen`), filtered by market cap / volume / price / exchange (env-var configurable, see `BUILD_UNIVERSE_*` in `.env`). Returns ~2200 symbols.
2. `screen_technicals()` — downloads 2 years of daily bars per candidate (chunked, 100 at a time) and keeps only those matching at least one of VEXH/VCP/VCPO's actual entry-setup conditions (`passes_technical_filters`), not a generic trend filter.
3. `write_tickers_file()` — overwrites `webapp/tickers.py` with the survivors.

**Staleness rule:** `app.py`'s `_daily_universe_refresh_if_needed()`, called every 30 minutes by the background loop, checks `db.get_last_screened_date()` — if it already says today, do nothing; otherwise run the full re-screen and call `db.set_last_screened_date()`. So the expensive ~60-90s screen runs **at most once per day**, automatically, without anyone touching the Refresh button.

**Manual override:** the Refresh button (`GET /api/tickers?refresh=1`) calls `rebuild_universe()` directly, which always does the real re-screen regardless of the daily marker.

## 2. Price bars — `webapp/data.py`

**What it is:** `_raw_cache: dict[ticker, DataFrame]` — daily OHLCV, backed by the `bars` table (one row per ticker per date) and `fetch_meta` (one row per ticker: `last_fetched_at`, `last_bar_date`, `last_error`).

**Fetch — now incremental, the actual point of the DB migration:** `_fetch_one(ticker, force)`:
- `force=False` (normal refresh): looks up `db.get_last_bar_date(ticker)`. If the ticker has never been fetched, requests the full `HISTORY_START` (`2021-01-01`) window — same cold-start behavior as before. If it *has* stored bars already, requests **only `start = last_bar_date + 1 day`** — a normal day's refresh asks Yahoo for ~1 new row per ticker instead of ~4.5 years of already-unchanged history.
- `force=True` (manual Refresh button): always requests the full history window regardless of what's stored, same as "get everything as of right now."
- `db.upsert_bars(ticker, df, fetched_at)` does `INSERT ... ON CONFLICT DO UPDATE` per date row — correct whether `df` is a full-history cold fetch or a few-row incremental tail. An empty `df` (e.g. re-checked on a weekend, nothing new) still bumps `last_fetched_at` without touching `last_bar_date`, so the TTL check knows it was re-checked just now.

**TTL gate (unchanged):** `_cache_is_fresh()` still checks same-day (ET) + 2h-while-open before even attempting a fetch — the incremental-fetch change reduces the *size* of a fetch, it doesn't change *when* one happens.

**Rate-limit handling:** if Yahoo returns `YFRateLimitError` for any ticker mid-batch, `warm_cache()` stops submitting new work immediately (cancels not-yet-started futures) rather than grinding through the rest of a ~2000-ticker batch against a wall. Whatever wasn't reached just stays stale and gets picked up on the next scheduled refresh.

**When it actually runs:**
- On startup (`_on_startup()`, non-blocking — see "Startup sequencing" below).
- Every 30 minutes, the background loop checks `data.is_stale()`: `True` if never fetched, or market's open and it's been >2h, or market's closed and today's post-close snapshot hasn't been taken yet. Otherwise **zero network calls**.
- Manual Refresh (`force=True`).

## 3. Computed strategy results — `webapp/app.py`

**What it is:** `_computed: list[dict]` — one payload per ticker, each containing VEXH's evaluation (which also forms the base of the payload), VCP, VCPO, prebreak state, and the 0-10 `setup_score` per strategy. This is exactly what `GET /api/tickers` returns to the browser — a page load is a pure in-memory read, no compute, no network.

**Built by** `compute_all()`, which only recomputes a ticker if its price bars actually changed:

- For each ticker, compare `data.get_fetched_at(ticker)` against `_computed_source_fetch[ticker]` (the fetch timestamp this ticker's *stored* result was computed from). Equal → reuse the stored payload/error as-is. Different (or missing) → recompute via `_compute_one()`.
- This is why the manual Refresh button doesn't need a separate "force recompute" flag: `force=True` on `warm_cache()` refetches every ticker's bars, which naturally advances every `fetched_at`, which naturally invalidates every cached compute result.
- Only tickers actually (re)computed this pass get written to `computed_results` — `db.upsert_computed(ticker, payload, source_fetched_at, computed_at, error)` per ticker, not a whole-table rewrite. Reused tickers' DB rows are already correct from a prior pass and aren't touched.

**Restoring `asof` after a restart:** `_load_computed_from_db()` calls `db.get_max_computed_at()` (a `MAX(computed_at)` across all rows) to restore `_computed_asof` on import. Without this, a restart with a fully warm DB would still read back `asof=None`, and the frontend treats that as "nothing computed yet" — showing the cold-start loader even though every ticker's result was already there and correct.

## 4. Earnings dates — `webapp/scoring.py`

**What it is:** used by VEXH's evaluation to flag "earnings report within N days," which factors into the score and (in `p.py`'s live engine) blocks new entries near a report. No longer a separate in-memory dict — `_cached_earnings_dates(ticker)` calls `db.get_earnings_dates(ticker)` directly, which returns `None` on a miss or expired 24h TTL (checked inside `db.py`, not in `scoring.py`).

**Fetch:** on a cache miss, fetches via a dedicated 8-worker executor with an 8-second timeout per ticker (`yf.Ticker.get_earnings_dates()` has no built-in timeout and can hang, which would otherwise stall the whole `compute_all()` batch since it waits for every result). `db.upsert_earnings_dates(ticker, dates, fetched_at)` writes immediately — no debouncing needed (unlike the old pickle version, which had to debounce because a whole-file rewrite on every one of ~2100 per-ticker cache misses was expensive; a single-row upsert isn't).

## Startup sequencing

`@app.on_event("startup")` used to call `refresh_and_compute()` inline, which meant **uvicorn didn't start accepting HTTP connections until the first fetch+compute pass finished** — irrelevant on a truly cold first-ever start, but a needless delay on every redeploy after that, since the port wouldn't even open despite the DB often already being warm.

Now the first `refresh_and_compute()` call runs inside the same background thread as the periodic 30-minute loop. The server is reachable immediately; `GET /api/tickers` can legitimately return `"asof": null` with an empty ticker list for the first few seconds/minutes after a cold start while that background pass is still running.

**Frontend handling of this** (`webapp/static/index.html`): a plain page load checks `data.asof === null` and, if so, shows a loading overlay and polls `/api/meta` every 2s for real progress — `fetch_progress: {done, total}` while `warm_cache()` is fetching, then `compute_progress: {done, total}` while `compute_all()` is running (these are mutually exclusive, since fetching and computing happen sequentially). The label switches from "Now fetching tickers… X of Y" to "Now computing… X of Y" automatically as the backend hands off between phases.

## The reload/`TICKERS` gotcha

`app.py`'s `TICKERS` and `data.py`'s `TICKERS` are **separate names bound at import time** (`data.py` does `from webapp.tickers import TICKERS`). When `rebuild_universe()` calls `importlib.reload(tickers_module)`, only `app.py`'s own `TICKERS` variable gets reassigned — `data.py`'s copy is untouched and silently stale for the rest of the process's life unless something explicitly passes the new list in.

This is why `refresh_and_compute()` always calls `data.warm_cache(TICKERS, force=force)` with `app.py`'s `TICKERS` passed explicitly, rather than relying on `warm_cache()`'s own `tickers or TICKERS` fallback (which would silently fetch bars for the *old* universe). This was a real bug once (every ticker in a freshly-screened universe came back `"no data"` because the price cache was still keyed to the old, empty-at-the-time list) — if you ever add a new module that reads `TICKERS`, make sure it's reading `app.py`'s live copy, not re-importing its own.

## Migrating from the old pickle files

If a server still has `price_cache.pkl` / `computed_cache.pkl` / `earnings_cache.pkl` / `universe_last_screened.txt` from before this migration, run `webapp/migrate_pickle_to_db.py` once (`python -m webapp.migrate_pickle_to_db`) to bulk-insert their contents into `app_data.db` before/after the first deploy that includes this change — otherwise that server starts the DB cold and re-fetches/recomputes everything from scratch on the next refresh. Safe to run more than once; doesn't delete the old files itself.

## Docker & deployment

`webapp/tickers.py` and the three SQLite files (`app_data.db`, `app_data.db-wal`, `app_data.db-shm` — WAL mode's sidecar files) are:
- **Gitignored** (`.gitignore`) — generated at runtime, per-environment, never committed.
- **Bind-mounted** in `docker-compose.yml` so they persist on the host across `docker compose up -d --build`, instead of being baked into (and lost with) the container's writable layer. All three DB files are mounted individually, matching the one-file-per-mount pattern the old pickle caches used.
- **Guarded in `deploy.sh`** with a `[ ! -f ... ] && touch ...` check before `docker compose up`, because Docker creates a bind-mount target as a **directory** if the host file doesn't exist yet — which then breaks SQLite's ability to open it as a database file. This bit the project with the old pickle files before the guard was added everywhere, so the same guard now covers all three DB files too.
- **Crash-safety via WAL mode, not manual atomic swaps.** The old pickle saves needed a workaround for `os.replace()` failing with `EBUSY` on a bind-mounted file (the atomic tmp-then-rename pattern doesn't work when the target is a mount point). SQLite's WAL journal mode handles crash safety internally without ever needing to swap the main DB file's inode, so none of that `EBUSY` dance applies here.

## Known limitations

- **No pruning.** Rows for tickers that drop out of the universe are never deleted — they just sit there unused. Currently harmless (small enough in aggregate) but means the DB grows slowly forever. This was actually a deliberate, explicit design choice (see `db_implementation.md`'s "keep permanently, even if unused" section) — a ticker that re-enters the universe later picks up its old `last_bar_date` and only fetches the gap, instead of a full cold refetch.
- **`/api/debug/memory`** (added during a memory investigation) reports live RSS, per-cache sizes, and forces a GC pass — useful for checking cache health/size on the real server without guessing. Not authentication-protected; relies on the box being LAN-only behind Cloudflare Tunnel with no public route configured to that path.

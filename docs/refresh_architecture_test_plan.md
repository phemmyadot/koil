# Refresh Architecture — Test Plan

Validates `webapp/refresh_architecture.md` against the actual running code. Each test names the file/function under test, the exact steps, and the pass condition. Several of these map directly to real bugs found and fixed earlier in this project's history (noted inline) — this plan exists so those don't silently regress.

## 1. Build — nothing happens

**Claim**: no fetch, no compute at image-build time.

**Test**: `docker build` (or just import the modules) with no `app_data.db` present.
- **Steps**: `rm -f webapp/app_data.db*`; `python -c "import webapp.data, webapp.app"` (import only, don't call `_on_startup`).
- **Pass**: no network calls occur; `webapp/db.py`'s `_init_schema()` creates empty tables; `data._raw_cache`/`app._computed` are empty dicts/lists after import.

## 2. App load — pure read, no fetch, no staleness check

**Claim**: a page load (`GET /api/tickers`, `GET /api/meta`) never triggers a fetch or compute; it only reads what's already in memory/DB.

**Test A — warm DB, server just started**:
- **Steps**: with a DB already populated, start the server, immediately (before the background loop's first pass could plausibly finish) call `GET /api/meta` and `GET /api/tickers`.
- **Pass**: both return instantly (no multi-second hang), `asof` reflects the *prior* compute pass's timestamp (not null), `tickers` array is non-empty.

**Test B — cold DB**:
- **Steps**: with `app_data.db` deleted, start the server, immediately call `GET /api/tickers`.
- **Pass**: response returns instantly with `"asof": null` and an empty `tickers` array — it must NOT block waiting for the background fetch to complete. (This is what the frontend's loading overlay covers — see `webapp/static/index.html`'s `asof === null` check.)

## 3. Background loop — three independent gates, each all-or-nothing per its own rule

### 3a. Tickers: skip if screened within 2h, else re-screen fully

**Test**:
- **Steps**: call `db.set_last_screened_at(time.time())` (simulate "just screened"), then call `app._universe_refresh_if_needed()`.
- **Pass**: `rebuild_universe()` is NOT called (no Yahoo screener hit) — verify via a monkeypatched `build_universe.fetch_candidates` that asserts it's never invoked.
- **Steps (stale case)**: call `db.set_last_screened_at(time.time() - 3*60*60)` (3h ago), call `_universe_refresh_if_needed()` again.
- **Pass**: `rebuild_universe()` IS called this time.

### 3b. Prices: gap-fetch only, never full re-pull for an already-known ticker

**Test**:
- **Steps**: for a ticker with `last_bar_date` already set to yesterday, call `data._fetch_one(ticker, force=False)`.
- **Pass**: the `yf.download(..., start=...)` call uses `start = last_bar_date + 1 day`, not `HISTORY_START`. Verify by monkeypatching `yf.download` to capture its `start=` argument.
- **Regression check (the empty-fetch bug, fixed this session)**: run the same test where Yahoo returns an empty DataFrame (no new bar today). Confirm `db.get_last_bar_date(ticker)` is UNCHANGED afterward (not wiped to `None`) and `db.get_fetched_at(ticker)` correctly advances. This directly covers the `upsert_bars` empty-branch bug where a bare `UPDATE` silently no-op'd on a ticker with no prior `fetch_meta` row.

### 3c. Earnings: skip if fetched within 24h, else fetch fresh

**Test**:
- **Steps**: call `db.upsert_earnings_dates(ticker, dates, fetched_at=time.time())`, then call `scoring._cached_earnings_dates(ticker)` with `fetch_earnings_dates` monkeypatched to assert-fail if called.
- **Pass**: no call to `fetch_earnings_dates` — served from `db.get_earnings_dates`'s cache hit.
- **Steps (stale case)**: `db.upsert_earnings_dates(ticker, dates, fetched_at=time.time() - 25*60*60)` (25h ago), call again.
- **Pass**: `fetch_earnings_dates` IS called this time.

### 3d. Compute: recompute only if price data actually changed

**Test (the core regression this session's bugs centered on)**:
- **Steps**: run `app.compute_all()` once for a ticker with real bars (genuine compute, not reused). Confirm `db.get_computed(ticker)` returns a real payload with `source_bar_date` matching `db.get_last_bar_date(ticker)`.
- **Pass (reuse case)**: call `app.compute_all()` again immediately, with no price data change. Time it — must be near-instant (sub-millisecond per ticker range, not tens-to-hundreds of ms), confirming the ticker was reused, not recomputed. Verify via a monkeypatched `_compute_one` that asserts it's never called for this ticker on the second pass.
- **Pass (real-change case)**: mutate `last_bar_date` for the ticker (simulate a genuine new bar), call `compute_all()` a third time. Confirm `_compute_one` IS called this time, and the new result is persisted with the updated `source_bar_date`.
- **Regression check (the fault-isolation bug, fixed this session)**: force one ticker's `db.upsert_computed()` call to raise (e.g. monkeypatch it to throw for one specific ticker in a batch of 3+). Confirm the OTHER tickers in the same `compute_all()` pass still persist correctly — the whole pass must not abort because of one bad ticker.

### 3e. Background loop startup timing: restart shouldn't bypass the 2h rule

**Test (the restart-staleness bug, fixed this session)**:
- **Steps**: set `db` so `get_max_fetched_at()` returns a timestamp 5 minutes old (well within `CHECK_INTERVAL`). Start the server (or directly invoke the startup `loop()` logic in isolation).
- **Pass**: the loop's first pass is SKIPPED (no immediate `warm_cache`/`compute_all` call) — it computes the remaining wait time and sleeps first. Verify by monkeypatching `refresh_and_compute` to assert-fail if called before the expected sleep duration elapses.
- **Steps (genuinely stale case)**: `get_max_fetched_at()` returns `None` (never fetched) or a timestamp older than `CHECK_INTERVAL`.
- **Pass**: the loop's first pass runs immediately, no wait.

### 3f. Background loop resilience: one bad pass must not kill the loop forever

**Test (the NaT-crash regression, fixed this session)**:
- **Steps**: monkeypatch `refresh_and_compute` to raise once, then succeed on subsequent calls. Run the loop for 2+ iterations (with `CHECK_INTERVAL` shortened for the test).
- **Pass**: the exception is caught and logged; the loop continues to the next cycle rather than the background thread dying. Confirm via checking that a subsequent call still happens (thread is still alive and looping).

## 4. Manual refresh — force tickers + prices (→ forces compute), earnings NOT forced

**Test**:
- **Steps**: with a ticker's earnings dates fresh (fetched < 24h ago) and prices fresh (fetched < 2h ago), call `GET /api/tickers?refresh=1`.
- **Pass — universe**: `rebuild_universe()` is called regardless of the daily/interval marker (verify the screener hit happens even though `_universe_refresh_if_needed()`'s own gate would have skipped it).
- **Pass — prices**: `data.warm_cache(TICKERS, force=True)` is called — verify via monkeypatched `_fetch_one` that `force=True` is passed, meaning full-history re-fetch, not gap-fetch, regardless of freshness.
- **Pass — compute**: because `force=True` refetches full history, every ticker's `last_bar_date`/`fetched_at` changes, which correctly cascades into `compute_all()` recomputing everyone (this is expected, not a bug — verify it happens, don't treat it as a violation of "only recompute if changed," since the force-refetch is what constitutes the change here).
- **Pass — earnings**: monkeypatch `fetch_earnings_dates` to assert-fail if called. Confirm it is NOT called during the forced refresh, since earnings' 24h TTL is independent of the `force` flag. (This exact behavior was directly verified via a spy in this session's own testing — this test formalizes that check.)

## Running this plan

Given the number of real bugs this session found by direct reproduction rather than code reading, prefer executing each test against the real local `webapp/app_data.db` (or a disposable copy) with real `sqlite3`/`pandas` objects, not mocks of `db.py` itself — the actual bugs found here (the `UPDATE`-vs-`INSERT` gap, the nested-exception fault isolation, the restart-staleness gate) were all things that only surfaced under real execution, not from reading the code or from over-mocked unit tests.

Yahoo network calls should still be mocked/monkeypatched (`yf.download`, `fetch_earnings_dates`) to keep the suite fast and independent of rate-limiting — but the SQLite layer, the in-memory cache dicts, and the actual `compute_all`/`warm_cache`/`_on_startup` logic should run for real against a real (throwaway) DB file.

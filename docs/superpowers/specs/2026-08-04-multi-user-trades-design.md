# Multi-user trades — design (future work, not scheduled)

## Question being answered

How hard is it to turn KOIL from a single-user, whole-app-shares-one-set-
of-trades tool into one where multiple people can log in and each sees only
their own Trades tab, while the screener/strategy universe (candidate
tickers, computed VEXH/VCP/VCPO results, price bars, earnings dates) stays
exactly as it is today — one shared, global dataset every user reads from.

**Short answer: moderate, not trivial.** No auth infrastructure exists
today (confirmed — zero references to session/login/JWT/OAuth anywhere in
`backend/app.py` or the frontend). This is a genuine greenfield addition,
not a toggle. But the blast radius is well-contained: exactly 5 tables
touch trades; everything else (bars, computed_results, candidate_tickers,
earnings_dates, universe_meta, background_loop_meta) is already correctly
global and needs zero changes. Realistic estimate: a few focused days of
work, not a rewrite.

## What must become user-scoped vs. what stays global

**Stays global (no changes)**: `bars`, `fetch_meta`, `computed_results`,
`earnings_dates`, `universe_meta`, `candidate_tickers`,
`background_loop_meta`. These already have no per-user concept and none is
needed — the screener/strategy computation is explicitly meant to be
shared infrastructure everyone reads from.

**Becomes user-scoped** — every table that currently has a real per-trade
row:

| Table | Change |
|---|---|
| `positions` | add `user_id INTEGER NOT NULL`, FK to a new `users` table |
| `position_fills` | no direct `user_id` needed — always reached via `position_id`, inherits scoping through the position it belongs to |
| `trade_daily_marks` | same — reached via `position_id`, no direct column needed |
| `notifications` | add `user_id INTEGER` (nullable, matching `position_id`'s existing nullable-for-strategy-alerts state from 2026-08-04) — a `tp_progress`/`stop_progress` alert is scoped to whoever owns the position (derived from the position's own `user_id`, not stored redundantly). A `strategy_state` alert becomes per-recipient FANNED OUT (one row per user whose saved filter/watchlist matched), not a single shared row — see "Strategy alerts become per-user" below. |
| `push_subscriptions` | add `user_id INTEGER NOT NULL` — this table's own comment already flags it: *"No user/account table exists in this single-user app, so subscriptions aren't scoped to a user id -- every stored subscription receives every push"* — that line is the whole reason this migration is needed here |
| `watchlist_tickers` | **Decided: becomes per-user.** Add `user_id INTEGER NOT NULL` (composite key `(user_id, ticker)`, was just `ticker PRIMARY KEY`). Two separate concerns currently live under this one table/name and need to be pulled apart: (a) the ACTUAL watchlist a user curates (today, confusingly, this isn't even backed by this table at all — real watchlists live entirely in browser localStorage per `useWatchlists.ts`; this becomes a real per-user DB table for the first time), and (b) the background-fetch liveness signal (`_active_tickers()`'s `db.get_watchlist_tickers()` call, which today receives a flattened, deduped, ALREADY-anonymous ticker list via `POST /api/watchlist-tickers` — see `frontend/src/api/tickers.ts`'s own comment: *"watchlists themselves stay in the browser's localStorage -- this just tells the background fetch/compute loop which extra tickers to keep alive"*). Once watchlists are real per-user DB rows, (b) can simply be derived by unioning across all users' watchlist rows server-side instead of the client re-POSTing a flattened list — removes a whole client round-trip, not just an add-on. |

**New table**: `users` — `id`, `email` (or username), `password_hash`,
`created_at`, minimally. If using an OAuth-style external provider instead
of a homegrown password system (see auth approach below), this instead
holds `id`, `email`, `provider`, `provider_user_id`, `created_at`.

**New table**: `user_filter_prefs` (or similar) — per-user saved default
filter, one row per user:

```sql
CREATE TABLE user_filter_prefs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    min_cap INTEGER,          -- NULL = use the app-wide BUILD_UNIVERSE_MIN_CAP default
    min_vol INTEGER,
    price_min INTEGER,
    price_max INTEGER,
    match_mode TEXT,          -- 'and' | 'or', NULL = use app-wide default
    updated_at TEXT NOT NULL
);
```

These mirror `build_universe.py`'s existing `DEFAULT_MIN_CAP`/
`DEFAULT_MIN_VOL`/`DEFAULT_PRICE_MIN`/`DEFAULT_PRICE_MAX`/
`DEFAULT_MATCH_MODE` env-var-driven globals — today those are ONE shared,
app-wide setting; this makes them a per-user override on top of the same
app-wide defaults (NULL columns fall back to the existing env-var value, so
a user who never saves a preference gets exactly today's behavior).

**Scope note**: this only needs to parameterize
`passes_technical_filters()`'s existing knobs (`min_cap`/`min_vol`/
`price_range`/`match_mode`, all of which the function already accepts as
parameters today, just always called with the env-var defaults) — it does
NOT require the dashboard's own displayed ticker list to become
per-user-filterable too (a separate, larger feature: every user seeing a
DIFFERENT computed universe, not just different alert gating on the same
shared universe). Confirming that's out of scope here — this design only
threads the saved filter through the alert-firing decision (see below),
the dashboard itself keeps showing the one shared app-wide filtered view
to everyone, same as today.

## Auth approach — 3 realistic options

1. **Cookie-based sessions, homegrown** (`fastapi-users` or hand-rolled
   with `itsdangerous`/`passlib`). Full control, no third-party dependency
   at runtime, but you own password reset flows, session expiry, etc.
   Fits a small self-hosted app well.
2. **JWT bearer tokens** (`python-jose` or `PyJWT` + `passlib` for hashing).
   Stateless, natural fit if the API is ever consumed by something other
   than this one frontend (e.g. a future mobile app). Slightly more
   frontend work (attach `Authorization` header everywhere via the
   existing `apiGet`/`apiPost` wrapper in `frontend/src/api/client.ts` —
   one central place, not scattered).
3. **Reverse-proxy auth** (e.g. Cloudflare Access, since the deployment
   already runs behind Cloudflare Tunnel per the existing
   `docker-compose.yml`/deployment setup) — Cloudflare authenticates the
   user before the request ever reaches the container, and passes an
   identity header (`Cf-Access-Authenticated-User-Email`) the backend
   trusts. Zero new Python auth packages, zero login UI to build, but
   ties the user set to Cloudflare Access's own user management (invite-
   only, no self-serve signup) and only works when accessed through that
   tunnel, not a bare `localhost:3006`/direct IP path if one is ever used.

Given this is a small, currently-single-user, self-hosted app already
behind Cloudflare Tunnel, **option 3 is the lowest-effort real answer** if
the user set is small/known in advance (you + a few people you invite) —
no new packages, no password storage/reset flow to build or secure. Options
1/2 make sense if self-serve signup or non-Cloudflare access is wanted.

## New Python packages (if NOT using reverse-proxy auth)

```
passlib[bcrypt]     # password hashing
python-jose[cryptography]   # JWT, if bearer-token approach
# or, for cookie-sessions:
itsdangerous         # signed session cookies (already a FastAPI/Starlette dependency, may not need adding explicitly)
```

No new package needed at all under the Cloudflare Access approach — the
identity header is just a plain HTTP header FastAPI reads like any other.

## Schema migration shape

Same pattern already used repeatedly in `backend/db.py` this session
(`_migrate_position_fills_price_nullable`,
`_migrate_notifications_position_id_nullable`) — SQLite has no `ALTER
TABLE ... ADD COLUMN ... REFERENCES`, so:

1. Add `users` table fresh (no migration needed, brand new).
2. For `positions`/`push_subscriptions`: since this app currently has
   exactly one implicit "user" (whoever's been using it), the migration
   creates a single default user row and backfills every existing
   `positions`/`push_subscriptions` row with that user's id — not a
   destructive migration, existing trade history is preserved and simply
   becomes "owned by" the person who was already using the app.
3. Same create-copy-drop-rename dance for adding the NOT NULL `user_id`
   column to `positions` (a nullable add-then-backfill-then-tighten
   sequence is simpler here than the copy-table dance, if SQLite version
   support allows `ALTER TABLE ADD COLUMN` — it does, SQLite's supported
   this since 3.x; only dropping/renaming/changing an existing column
   needs the full copy dance, adding a new nullable column doesn't).

## Every backend endpoint that needs a scoping change

All of `backend/app.py`'s position/fill/notification/push endpoints need
the current authenticated user injected and filtered on — via FastAPI's
`Depends()` mechanism, a `current_user: User = Depends(get_current_user)`
parameter added to each:

- `create_position`, `add_fill`, `list_positions`, `get_position`,
  `cancel_position`, `update_position`, `update_fill`, `delete_fill` — all
  currently take no user context; each needs its DB query to add `WHERE
  user_id = ?` (`db.py`'s corresponding functions gain a `user_id`
  parameter).
- `notifications`, `read_notification`, `read_all_notifications` — same,
  scoped to the requesting user's own notifications (except possibly
  `strategy_state` kind, per the open question above).
- `push_subscribe`/`push_unsubscribe` — scoped so a push fan-out
  (`push.send_push_to_all`) becomes per-user, not global; needs a new
  `push.send_push_to_user(user_id, ...)` alongside (or replacing) the
  current all-subscribers version. The actual TP/stop alert engine
  (`_update_trade_marks_and_alerts`/`_fire_threshold_alerts`) then needs
  to look up the position's owning user before firing push, not just
  broadcast to everyone.
- **Strategy alerts become per-user (decided).** Today's
  `compute_all()`-level gate (`tk in passes_default_filter`, the single
  app-wide `passes_technical_filters()` result) is replaced by a per-user
  gate: for each user, re-evaluate `passes_technical_filters(bars, ...)`
  with THAT user's saved `user_filter_prefs` overrides (falling back to the
  app-wide default for any NULL column), and only fire/insert/push the
  alert for users whose own filter currently matches this ticker. Concrete
  shape: the existing single `_fire_strategy_state_alert(ticker, strat_key,
  new_state, now_iso)` call becomes a loop over users --
  `for user in users_with_matching_filter(ticker, bars):
  _fire_strategy_state_alert_for_user(user.id, ticker, strat_key,
  new_state, now_iso)` -- inserting one `notifications` row per matching
  user (not one shared row), and pushing only to that user's own
  `push_subscriptions` rows (`push.send_push_to_user`, not
  `send_push_to_all`). Real-trade `tp_progress`/`stop_progress` alerts
  don't need this loop at all -- they already have exactly one owning user
  (the position's `user_id`), so it's a single lookup, not a fan-out.

## Frontend changes

- A login page/flow (new route, e.g. `/login`), and a route guard around
  the existing `AppShell`/router tree so an unauthenticated visitor is
  redirected there.
- `frontend/src/api/client.ts`'s `apiGet`/`apiPost` wrappers need to attach
  auth (cookie is automatic if using cookie-sessions; a bearer token needs
  an explicit header added here, one central change reused by every
  existing API call site).
- Logout button/flow somewhere in `AppShell`.
- If going the JWT route: token storage (httpOnly cookie strongly
  preferred over localStorage for XSS resistance) and refresh handling.
- Under Cloudflare Access: potentially near-zero frontend change — the
  browser never even reaches the app without already being authenticated
  by Cloudflare's own login page, so the SPA itself might not need a login
  UI at all, just trusting the identity header is present.

## Decided

- **Watchlists become per-user** — real DB-backed (`watchlist_tickers`
  gains `user_id`), not localStorage-only. See table above.
- **Strategy alerts (`strategy_state`) become per-user**, gated by each
  user's own saved default filter (`user_filter_prefs`), not the single
  shared app-wide filter. One notification row per matching user, pushed
  only to that user's own subscriptions.

## Open questions for whenever this is picked up

1. **Auth approach** — Cloudflare Access (near-zero build cost, ties to
   existing infra, invite-only) vs. homegrown (more work, more control,
   supports self-serve signup if ever wanted)?
2. **Compute cost of per-user filter fan-out**: today's alert check runs
   `passes_technical_filters()` ONCE per ticker per pass (already computed
   as part of `filtered_tickers`). Per-user filters mean this potentially
   reruns per (ticker × user with a non-default saved filter) — for a
   small user count this is trivially cheap, but worth deciding whether to
   cache/dedupe identical filter configs across users who happen to save
   the same values, or just accept the redundant recompute at this app's
   realistic scale (a handful of users, not hundreds).
3. Does a user WITHOUT a saved filter preference (all-NULL row, or no row
   at all) get strategy alerts using the app-wide default filter (implicit
   opt-in, matches today's single-shared-filter behavior for anyone who
   hasn't customized anything), or does having no saved preference mean NO
   strategy alerts until they explicitly save one (opt-in required)?
   Leaning toward the former (default filter = today's behavior, saving a
   custom one only narrows/widens from there) but flagging as a real
   product decision, not just implementation detail.
4. Multi-tenant single SQLite file is fine at this app's realistic scale
   (a handful of users) — not raising this as something that needs
   solving, just confirming no one expects this to need a "real" database
   migration (Postgres etc.) as part of this work; SQLite handles this
   scale without issue.

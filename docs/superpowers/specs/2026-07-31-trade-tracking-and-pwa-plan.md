# Implementation Plan — Trade Tracking, then PWA/Push

Date: 2026-07-31
Status: Planning

## Sequencing decision: Trade Tracking first, PWA second

[PWA & Push Notifications](2026-07-31-pwa-push-design.md) is explicitly scoped as depending on
the `notifications` table and alert engine from
[Trade Tracking & TP/Stop Notifications](2026-07-31-trade-tracking-design.md) — it only adds a
delivery mechanism (installable app + push) on top of notifications that must already exist.
Building PWA first would mean standing up service worker/manifest/VAPID plumbing with nothing
real to push yet. Trade Tracking also delivers standalone value (confirm trades, track to
exit, history, in-app alerts) with no dependency on PWA at all.

**Order: Trade Tracking end-to-end (including in-app notifications) → PWA/Push.**

## Phase 1 — Trade Tracking & TP/Stop Notifications

Ref: [2026-07-31-trade-tracking-design.md](2026-07-31-trade-tracking-design.md)

### 1a. Data layer — DONE
- [x] Add `taken_trades` table to `webapp/db.py` (schema per design doc, incl. `entry_date`,
      `confirmed_at`, option-only nullable fields, `last_alert_tp_pct`/`last_alert_stop_pct`)
- [x] Add unique constraint / dedup handling on `(ticker, strategy_key, signal_date)`
- [x] Add `trade_daily_marks` table with unique constraint on `(trade_id, mark_date)`
- [x] Add `notifications` table
- [x] Write upsert helper for `trade_daily_marks` (`INSERT ... ON CONFLICT DO UPDATE`)

### 1b. Confirm-trade API — DONE
- [x] `POST /api/trades` — validate instrument-specific required fields, insert row
- [x] Backfill logic in `POST /api/trades`: if `entry_date` is in the past, populate
      `trade_daily_marks` for each trading day from `entry_date` to today using
      `data.get_bars()` (cached, no new fetch)
- [x] `GET /api/trades?status=open|closed` — list endpoint
- [x] `GET /api/trades/{id}` — single-trade fetch (added during testing; not in original plan)
- [x] `PATCH /api/trades/{id}` — edit TP/stop/notes
- [x] `POST /api/trades/{id}/close` — manual exit recording

### 1c. Daily mark capture + alert engine — DONE
- [x] Hook into `refresh_and_compute()`, after `compute_all()`
- [x] For each open trade: upsert today's `trade_daily_marks` row from `_computed` price/date
- [x] Compute `pct_to_tp` / `pct_to_stop` (sign-aware for long/short)
- [x] Threshold-crossing check against `[30, 50, 70, 80, 90, 95]`, using
      `last_alert_tp_pct`/`last_alert_stop_pct` to fire each band once
- [x] Insert `notifications` row on new threshold crossed
- [x] Confirmed same logic runs on manual `GET /api/tickers?refresh=1` path, not just the
      scheduled 2-hour cycle — verified live via a real HTTP-triggered refresh

### 1d. Analytics/history API — DONE
- [x] `GET /api/trades/summary` — open/closed counts, win rate, avg return
- [x] `GET /api/trades/{id}/marks` — daily series for one trade
- [x] `GET /api/trades/analytics?strategy=&ticker=&from=&to=` — aggregate series across filtered
      trades
- [x] `GET /api/notifications?unread=1` + `POST /api/notifications/{id}/read`
- [x] Fixed a route-ordering bug found during testing: `/api/trades/summary` and
      `/api/trades/analytics` were declared after `/api/trades/{trade_id}`, so FastAPI matched
      them as `trade_id="summary"`/`"analytics"` first. Reordered so the static routes are
      registered before the dynamic one.

### 1e. Frontend — TRADE button & confirm form — DONE
- [x] Add TRADE button to strategy modal (`webapp/static/index.html` — `strategyModal()`,
      next to Estimate Entry, not a separate card-level button)
- [x] Confirm form: instrument toggle, entry date (default today, editable), entry price
      (prefilled from `open_position.entry_price` or current price), TP price (prefilled from
      `open_position.target` when available), stop price
- [x] Option-instrument fields: side/type/strike/premium/contracts/expiry — reuses the
      `.plcalc`/`.optfields`/`.formrow`/`toggleGrpHtml()` styling and helpers already
      established by the P/L Calculator, not new CSS
- [x] Wire to `POST /api/trades`; 409 dup and other API errors surface inline in the form
- [x] Verified end-to-end with Playwright (real browser, not just static checks): opened a
      strategy modal, clicked TRADE, submitted both a spot trade and an option trade, confirmed
      correct persistence via the API and zero console errors on both paths; also confirmed the
      409-dedup path renders its error message correctly in the form

### 1f. Frontend — active trades, history, badges — DONE (revised scope, see notes)
- [x] New `webapp/static/trades.html` page, linked from `index.html`'s header nav (next to
      Watchlists) — follows the existing watchlist.html pattern (separate static page) per
      explicit direction, not a tab inside index.html
- [x] Active tab: open-trade cards with entry/TP/stop, live return vs. entry, and a per-trade
      daily-close sparkline (SVG, reuses the app's own line-chart conventions)
- [x] "Close trade" action + manual exit form inline on each card → `POST /api/trades/{id}/close`
- [x] Cohort chart: avg % return from entry indexed by days held, across trades filtered by
      strategy and/or ticker (dropdowns) — combines "aggregate chart" + "strategy/ticker
      filters" from the original two checklist items into one control
- [x] History tab: closed-trades table + summary stat boxes (open/closed counts, win rate, avg
      return) shared across both tabs
- [x] Verified end-to-end with Playwright: seeded 3 trades (2 open incl. one option, 1 closed)
      via the API, confirmed summary math, card rendering, cohort chart trade-count response to
      both filters, and the full close-trade UI flow (card moves from Active to History,
      summary updates) — zero console errors throughout
- Scope note: the original checklist had "visual badge on cards" and "dashboard filter toggle:
  active trades only" as separate index.html changes, plus a distinct "date-range filter" for
  the cohort chart. Implemented instead as: the Trades page itself *is* the active-trades view
  (no need to also filter the main dashboard), and strategy/ticker filters ship now with
  date-range filtering deferred (the `/api/trades/analytics?from=&to=` params already exist
  server-side per 1d, just not wired to page controls yet) since strategy/ticker cover the
  requested "for each trade or all, or by strategy" analytics need without it.

### 1g. Frontend — in-app notifications — DONE
- [x] Notifications modal (not a separate page, per direction): unread/read list, unread rows
      highlighted, click-to-mark-read
- [x] Bell icon + unread-count badge in `index.html`'s header, polled on the same
      `BACKGROUND_WATCH_MS` (20s) cadence as the existing background-refresh watcher
- [x] Verified end-to-end with Playwright: generated a real threshold-crossing notification,
      confirmed the bell badge shows the correct count, the modal lists it as unread, clicking
      marks it read, and the badge correctly disappears once nothing is unread — zero console
      errors

### 1h. Verify — DONE (covered inline during 1b/1c/1f, not a separate pass)
- [x] Confirm a spot trade, verify daily mark appears after a fetch cycle — verified in 1c via
      both a direct call and a live HTTP-triggered manual refresh
- [x] Confirm a late-logged trade (past entry date), verify backfilled marks — verified in 1b/1e
      (9 trading days correctly backfilled from `entry_date` to the day before today)
- [x] Cross a TP threshold band, verify single notification fires (not repeated every cycle) —
      verified in 1c (30% band fired once, did not refire on a second live refresh cycle with
      unchanged price) and again in 1g against the notifications UI
- [x] Close a trade, verify it disappears from the active view and appears in history — verified
      in 1b (API-level) and 1f (UI-level via Playwright: card moved from Active tab to History
      tab, summary stats updated)

## Phase 2 — PWA & Push Notifications — DONE (implementation), pending manual verification

Ref: [2026-07-31-pwa-push-design.md](2026-07-31-pwa-push-design.md)

Implemented against the React SPA (`frontend/`), not the legacy `webapp/static/` this plan
was originally written against — the repo was restructured (`webapp/` → `backend/` +
`frontend/`) and the SPA rewrite landed in between this plan being written and Phase 2
starting. All file paths below reflect where things actually ended up, not the original plan.

### 2a. Installability — DONE
- [x] Create `frontend/public/manifest.json` (name, icons, `start_url`, `display: standalone`,
      theme/background colors)
- [x] Produce app icon set at required sizes (`frontend/public/icons/`: 192, 512,
      apple-touch-icon — rasterized programmatically from the KOIL K-mark SVG, not a
      hand-designed icon set; fine functionally, worth a real design pass later if the
      install/home-screen experience matters)
- [x] Add `<link rel="manifest">` + registration script (`index.html` + `main.tsx` →
      `frontend/src/lib/serviceWorker.ts`)
- [x] Create `frontend/public/service-worker.js` (install/activate lifecycle)
- [x] HTTPS available in the deployment target — already true, Cloudflare Tunnel terminates
      TLS in front of the existing deploy (confirmed via `deploy.sh`'s `cloudflared` restart)

### 2b. Push subscription plumbing — DONE
- [x] Generate VAPID key pair; stored as env vars (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`,
      `VAPID_SUBJECT`) in `.env` (gitignored, never committed). **`VAPID_SUBJECT` is still the
      placeholder `mailto:replace-me@example.com` — replace with a real contact (mailto: or
      URL) before relying on push in production; the Web Push spec requires push services be
      able to reach the sender.**
- [x] Add `push_subscriptions` table to `backend/db.py`
- [x] `GET /api/push/vapid-public-key`
- [x] `POST /api/push/subscribe` (upsert on `endpoint`)
- [x] `POST /api/push/unsubscribe`
- [x] Frontend: toggle "Push notifications" → `Notification.requestPermission()` →
      `pushManager.subscribe()` → `POST /api/push/subscribe` (`frontend/src/hooks/usePush.ts`).
      Lives inside the notification bell's dropdown (`NotificationPanel`), not a dedicated
      Settings page — none existed yet; worth carving out `/settings` if more toggles show up
      later.

### 2c. Push send path — DONE
- [x] Add `pywebpush` dependency (`requirements.txt`)
- [x] Extend alert engine (`_fire_threshold_alerts` in `backend/app.py`) to also send a push
      payload per subscription when a `notifications` row is inserted — `backend/push.py`
- [x] Handle expired/invalid subscriptions (410/404) by deleting the stale row; other failures
      (timeout, 5xx, network errors) leave the subscription in place to retry next alert
- [x] Service worker: handle `push` event → `showNotification`
- [x] Service worker: handle `notificationclick` → focus an existing tab (or open one),
      deep-link to `/trades/:positionId`

### 2d. Verify
- [x] Manifest, icons, service-worker file all serve correctly and the SW registers in a real
      browser — verified live via Playwright against the running backend
- [x] `tsc`, full frontend test suite (115 tests), and backend import all pass clean
- [x] Push send path exercised against a real (fake-endpoint) subscription: fails gracefully,
      doesn't crash the alert loop, leaves the subscription in place on a non-410/404 failure
- [ ] Install PWA on a real desktop browser, confirm standalone launch — not yet done, needs a
      human with a real browser (Playwright doesn't exercise the install prompt)
- [ ] Subscribe to push for real, trigger a real threshold crossing, confirm an OS notification
      appears with the app fully closed — not yet done, needs `VAPID_SUBJECT` fixed first and a
      real device/browser
- [ ] Click a real push notification, confirm it opens/focuses the app at the relevant trade —
      not yet done
- [ ] Test on iOS Safari (installed) per the design doc's platform caveat — not yet done

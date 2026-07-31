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

### 1a. Data layer
- [ ] Add `taken_trades` table to `webapp/db.py` (schema per design doc, incl. `entry_date`,
      `confirmed_at`, option-only nullable fields, `last_alert_tp_pct`/`last_alert_stop_pct`)
- [ ] Add unique constraint / dedup handling on `(ticker, strategy_key, signal_date)`
- [ ] Add `trade_daily_marks` table with unique constraint on `(trade_id, mark_date)`
- [ ] Add `notifications` table
- [ ] Write upsert helper for `trade_daily_marks` (`INSERT ... ON CONFLICT DO UPDATE`)

### 1b. Confirm-trade API
- [ ] `POST /api/trades` — validate instrument-specific required fields, insert row
- [ ] Backfill logic in `POST /api/trades`: if `entry_date` is in the past, populate
      `trade_daily_marks` for each trading day from `entry_date` to today using
      `data.get_bars()` (cached, no new fetch)
- [ ] `GET /api/trades?status=open|closed` — list endpoint
- [ ] `PATCH /api/trades/{id}` — edit TP/stop/notes
- [ ] `POST /api/trades/{id}/close` — manual exit recording

### 1c. Daily mark capture + alert engine
- [ ] Hook into `app.py:_on_startup` / `refresh_and_compute()`, after `compute_all()`
- [ ] For each open trade: upsert today's `trade_daily_marks` row from `_computed` price/date
- [ ] Compute `pct_to_tp` / `pct_to_stop` (sign-aware for long/short)
- [ ] Threshold-crossing check against `[30, 50, 70, 80, 90, 95]`, using
      `last_alert_tp_pct`/`last_alert_stop_pct` to fire each band once
- [ ] Insert `notifications` row on new threshold crossed
- [ ] Confirm same logic runs on manual `GET /api/tickers?refresh=1` path, not just the
      scheduled 2-hour cycle

### 1d. Analytics/history API
- [ ] `GET /api/trades/summary` — open/closed counts, win rate, avg return
- [ ] `GET /api/trades/{id}/marks` — daily series for one trade
- [ ] `GET /api/trades/analytics?strategy=&ticker=&from=&to=` — aggregate series across filtered
      trades
- [ ] `GET /api/notifications?unread=1` + `POST /api/notifications/{id}/read`

### 1e. Frontend — TRADE button & confirm form
- [ ] Add TRADE button to ticker/strategy card (`webapp/static/index.html`)
- [ ] Confirm form: instrument toggle, entry date (default today, editable), entry price
      (prefilled from current price), TP price, stop price
- [ ] Option-instrument fields: strike/premium/contracts/expiry/side/type (reuse field patterns
      from `optionsFormHtml()`)
- [ ] Wire to `POST /api/trades`

### 1f. Frontend — active trades, history, badges
- [ ] Visual badge on cards with an open `taken_trades` row
- [ ] Dashboard filter toggle: "active trades only"
- [ ] "Close trade" action + manual exit form → `POST /api/trades/{id}/close`
- [ ] New Active Trades page: per-trade daily-close chart with entry/TP/stop reference lines
- [ ] Active Trades page: aggregate/cohort chart with strategy/ticker/date-range filters
- [ ] History view: closed-trades table + summary stats

### 1g. Frontend — in-app notifications
- [ ] Notifications page: unread/read list
- [ ] Bell/badge indicator with unread count, polled alongside existing dashboard refresh

### 1h. Verify
- [ ] Manual test: confirm a spot trade, verify daily mark appears after next fetch cycle
- [ ] Manual test: confirm a late-logged trade (past entry date), verify backfilled marks
- [ ] Manual test: cross a TP threshold band, verify single notification fires (not repeated
      every cycle)
- [ ] Manual test: close a trade, verify it disappears from active filter and appears in history

## Phase 2 — PWA & Push Notifications

Ref: [2026-07-31-pwa-push-design.md](2026-07-31-pwa-push-design.md)

Do not start until Phase 1's alert engine (1c) is live and producing real `notifications` rows
— PWA has nothing meaningful to push otherwise.

### 2a. Installability
- [ ] Create `webapp/static/manifest.json` (name, icons, `start_url`, `display: standalone`,
      theme/background colors)
- [ ] Produce app icon set at required sizes
- [ ] Add `<link rel="manifest">` + registration script to `index.html`
- [ ] Create `webapp/static/service-worker.js` (install/activate lifecycle)
- [ ] Confirm HTTPS is available in the deployment target (hard prerequisite — flag/resolve
      before continuing; push does not work over plain HTTP except localhost)

### 2b. Push subscription plumbing
- [ ] Generate VAPID key pair; store as env vars (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`,
      `VAPID_SUBJECT`), never committed
- [ ] Add `push_subscriptions` table to `webapp/db.py`
- [ ] `GET /api/push/vapid-public-key`
- [ ] `POST /api/push/subscribe` (upsert on `endpoint`)
- [ ] `POST /api/push/unsubscribe`
- [ ] Frontend: Settings toggle "Enable push notifications" → `Notification.requestPermission()`
      → `pushManager.subscribe()` → `POST /api/push/subscribe`

### 2c. Push send path
- [ ] Add `pywebpush` dependency
- [ ] Extend alert engine (Phase 1c) to also send a push payload per subscription when a
      `notifications` row is inserted
- [ ] Handle expired/invalid subscriptions (410/404) by deleting the stale row
- [ ] Service worker: handle `push` event → `showNotification`
- [ ] Service worker: handle `notificationclick` → focus/open app, deep-link to the trade

### 2d. Verify
- [ ] Install PWA on desktop browser, confirm standalone launch
- [ ] Subscribe to push, trigger a test threshold crossing, confirm OS notification appears
      with app closed
- [ ] Click notification, confirm it opens/focuses app at the relevant trade
- [ ] Test on iOS Safari (installed) per the design doc's platform caveat

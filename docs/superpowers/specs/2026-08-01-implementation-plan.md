# Implementation Plan — Spot/Option P&L Fix + Add Trade for Untracked Ticker

Date: 2026-08-01
Status: Planning

Covers two finalized designs:
- [2026-08-01-separate-spot-option-pnl-design.md](2026-08-01-separate-spot-option-pnl-design.md) — no open questions remain
- [2026-08-01-add-trade-untracked-ticker-design.md](2026-08-01-add-trade-untracked-ticker-design.md) — no open questions remain

## Sequencing decision: P&L fix first, then Add Trade

The Add Trade feature's whole job is routing a user into `TradeConfirmModal`/`AddFillModal` with
correct prefill data. If it ships before the P&L fix, every option trade created through the new
entry point inherits the current bug (stock-price TP/stop compared against premium avg_cost) —
building on top of code about to change. The P&L fix touches `TradeConfirmModal.tsx` (removing
the option-instrument Entry Price field) directly, which Add Trade's flow also renders — doing
it second would mean touching the same component twice, once broken-consistent, once fixed.

**Order: P&L fix (Phase 1) → Add Trade for Untracked Ticker (Phase 2).**

No shared backend endpoints between the two — they touch overlapping *files*
(`TradeConfirmModal.tsx`, `app.py`) but not overlapping *logic*, so Phase 2 can start as soon as
Phase 1's frontend changes land, without waiting on Phase 1's backend migration to be deployed.

---

## Phase 1 — Separate Spot/Option P&L, Fix Alert Math, Tiered Risk-Free Rate

Ref: [2026-08-01-separate-spot-option-pnl-design.md](2026-08-01-separate-spot-option-pnl-design.md)

### 1a. Schema

- [ ] `backend/db.py`: `position_fills.price` becomes nullable (`REAL` not `REAL NOT NULL`).
      Confirmed no existing trades in prod — plain schema change, not a data-preserving
      migration. Since `CREATE TABLE IF NOT EXISTS` is a no-op against an already-existing table
      (same gotcha `db.py`'s own migration comments already document elsewhere), this needs an
      explicit one-time migration function even though there's no data to preserve — SQLite
      still needs the standard create-new/copy/drop/rename dance to change a column's
      nullability, matching the existing pattern (`_migrate_universe_meta_date_to_epoch` et al.)
      for structure, just with an empty/trivial copy step.
- [ ] Confirm `position_fills` has zero rows before running this locally/in any shared
      environment, as a sanity check (`SELECT COUNT(*) FROM position_fills`) — belt-and-suspenders
      given "destructive migration" was explicitly waved off based on that assumption.

### 1b. Backend — fill validation

- [ ] `app.py`: `_FILL_SPOT_REQUIRED` unchanged. `_FILL_OPTION_REQUIRED` drops `"price"`, keeps
      `["units", "strategy_key", "signal_date", "fill_date", "opt_side", "opt_type", "strike",
      "expiry_date"]` (no change here — `premium` handling is next).
- [ ] `_validate_fill_body`: `premium` becomes required for `instrument == "option"` regardless
      of `kind` (currently required implicitly only via `_FILL_OPTION_REQUIRED`'s membership,
      which already includes it for entry — confirm it's also enforced for exit fills, since
      exit fills previously relied on `price`, not `premium`, being required).
- [ ] `_build_fill`: no change needed — `fill["price"]` already reads `body.get("price")` via
      whatever the existing pattern is; confirm during implementation it tolerates `price` being
      absent from the body for option fills (i.e. doesn't `KeyError` on a missing key vs. a
      present-but-null one — check exact current implementation, since the design doc flagged
      `_build_fill` uses `body["price"]` direct-index in some places per the original bug
      writeup).

### 1c. Backend — alert engine fix (the actual bug)

- [ ] `app.py`, `_update_trade_marks_and_alerts`: branch on `instrument`.
      - Spot: unchanged — `pct_to_tp`/`pct_to_stop` computed from `current_price`/`avg_cost`/
        `tp`/`stop`, all stock prices, exactly as today.
      - Option: compute `current_option_value` (reuse `_with_option_values`'s per-lot blended
        Black-Scholes calc — either call that function's logic directly or factor its per-mark
        pricing loop into a shared helper callable from both places, since duplicating the
        blend-across-open-lots logic inline here would be the wrong move). Compute
        `tp_option_value`/`stop_option_value` via `options_pricing.option_price(opt_type, tp,
        strike, T, iv_at_entry, r)` and `(..., stop, ...)` — `T` measured from **today**, not
        entry (see design doc's decay note), blended across open lots the same way as
        `current_option_value` if more than one lot is open. Then:
        `pct_to_tp = (current_option_value - avg_cost) / (tp_option_value - avg_cost) * 100`,
        `pct_to_stop` mirrored.
- [ ] Extract a shared helper if `_with_option_values`'s per-lot blend logic is reused here
      verbatim (recommended, per design doc's "reuse, don't duplicate" framing) — e.g.
      `_blended_option_value(open_lots, S, mark_date_or_today, r_fn) -> float`, called from both
      `_with_option_values` (per historical mark) and `_update_trade_marks_and_alerts` (today,
      three times: current/tp-hypothetical/stop-hypothetical).
- [ ] Verify `TP_STOP_ALERT_THRESHOLDS` and `_fire_threshold_alerts` need zero changes (confirmed
      in design doc — only the values fed in change, not the threshold list or formula shape).

### 1d. Backend — tiered risk-free rate

- [ ] `backend/options_pricing.py`: add `RISK_FREE_RATE_TIERS` (the 6-tier table) and
      `risk_free_rate_for(days_to_expiry: float) -> float`. Keep `RISK_FREE_RATE = 0.045` as the
      beyond-2-years fallback (per design doc — not deleted, repurposed as the tail case).
- [ ] Every existing `option_price(...)` call site (`_with_option_values` in `app.py`, plus the
      new alert-engine calls from 1c) computes `r = risk_free_rate_for(days_to_expiry)` and
      passes it explicitly instead of relying on the default.
- [ ] Decide during implementation: pass `T` (years) or raw days into `risk_free_rate_for` — the
      function's job is a days-based lookup, so either convert internally
      (`risk_free_rate_for(T_years * 365)`) or have call sites pass days directly if that's
      already sitting in a local variable at the call site (`_with_option_values` already has
      `expiry`/`mark_date` as `date` objects before computing `T` — grab days-to-expiry from that
      subtraction directly rather than re-deriving from `T`).

### 1e. Frontend — pricing lib parity

- [ ] `frontend/src/lib/blackScholes.ts`: mirror `RISK_FREE_RATE_TIERS` +
      `riskFreeRateFor(daysToExpiry: number): number`, numerically identical table to the
      backend (same duplication convention already used for the Black-Scholes math itself).
- [ ] `frontend/src/lib/plCalc.ts`: both `blackScholes(...)` call sites (lines ~76, ~81 per
      design doc) compute `r` via `riskFreeRateFor` instead of relying on the default parameter.
- [ ] New/updated unit tests: `frontend/src/lib/blackScholes.test.ts` gets cases for
      `riskFreeRateFor` at each tier boundary (29/30/31 days, etc. — off-by-one boundary
      conditions matter here, test them explicitly) and the 2-year-ceiling fallback.

### 1f. Frontend — form changes

- [ ] `TradeConfirmModal.tsx`: remove the Entry Price field from the option-instrument branch
      (`instrument === "option"` JSX path) — keep it for spot, unchanged. TP/Stop fields:
      **no change** (confirmed in design doc's revised §4 — they stay stock-price inputs for
      both instruments).
- [ ] `AddFillForm.tsx`: the generic "Price" field (currently always shown, always required)
      needs to become conditional:
      - `kind === "entry"`, `instrument === "spot"`: Price field shown, required (unchanged).
      - `kind === "entry"`, `instrument === "option"`: Price field **removed** — `premium`
        (already shown for options) is the only value needed.
      - `kind === "exit"`, `instrument === "spot"`: Price field shown, required, means exit
        stock price (unchanged).
      - `kind === "exit"`, `instrument === "option"`: Price field **removed**; `premium` field
        (currently only conditionally rendered as part of the `isOption` entry-fields block —
        confirm it renders and is required for the exit case too, not just entry) becomes "exit
        option price."
- [ ] `PositionsTable.tsx`'s inline exit form (`submitExit`): currently a single generic
      `exitPrice` field submitted as `price` regardless of instrument. For an option position,
      needs to submit as `premium` instead of `price` — either add a second field shown only for
      `isOption` (mirroring `AddFillForm`'s pattern) or relabel/repurpose the existing field
      conditionally. Trace `onExit` prop through to `TradesPage.tsx`'s `handleExit` (currently
      hardcodes `price` in the `addFill` call body, `TradesPage.tsx:79`) — needs to send
      `premium` instead of `price` for option positions.
- [ ] Field labels: "Price" → conditionally "Exit Price" (spot) / "Exit Option Price" (option)
      wherever the exit forms render it, so the UI names match what's actually being recorded.

### 1g. Backend/frontend type updates

- [ ] `frontend/src/api/types.ts`: `Fill`'s `price` field becomes `price: number | null` (or
      optional) to match the now-nullable DB column, if typed as required today.
- [ ] Confirm no other frontend code assumes `fill.price` is always non-null for an option fill
      (grep `\.price\b` usages against `Fill`-typed values during implementation).

### 1h. Verify

- [ ] Unit tests for the alert-engine option branch: a synthetic open option position, mock
      `_computed`/marks, assert `pct_to_tp`/`pct_to_stop` are now computed from decayed option
      values, not raw stock price vs. premium (this is the regression test for the actual bug —
      should have existed as a failing test before the fix, if time allows write it that way).
- [ ] Confirm `_with_option_values`'s existing behavior (daily option-value marks/chart) is
      unchanged by 1d's rate-tiering — same shape, only the `r` input source changes from a flat
      constant to a lookup; spot-check a known IV/strike/expiry combination produces the same
      option value as before *if* the days-to-expiry happens to fall in whichever tier contains
      0.045 today (it doesn't exactly — the tiers are new real values, so values will shift
      slightly; confirm the shift is small/expected, not a sign of a wiring bug).
- [ ] Manual end-to-end: open a new option trade via `TradeConfirmModal` (confirm no Entry Price
      field appears, form submits with just premium/strike/etc.), confirm it appears correctly
      on Trades page and position detail page (unrealized P&L still correct — should be, since
      display logic wasn't touched), exit it via both `PositionsTable`'s inline form and
      `AddFillForm` on the position detail page (confirm both now ask for exit option price, not
      stock price), confirm TP/stop notifications fire (or don't) based on plausible option-price
      math — hard to fully verify without waiting for a real threshold cross, but at minimum
      confirm the alert-engine code path doesn't throw for an open option position on the next
      refresh cycle.
- [ ] `tsc -b --noEmit`, full `vitest run`, backend `import backend.app` sanity check — standard
      verification bar for every prior change in this project.

---

## Phase 2 — Add Trade for an Untracked Ticker

Ref: [2026-08-01-add-trade-untracked-ticker-design.md](2026-08-01-add-trade-untracked-ticker-design.md)

### 2a. Backend

- [ ] `app.py`: new `POST /api/tickers/fetch-one` endpoint per the design doc's exact
      implementation sketch — `data.warm_cache([ticker], force=True)` +
      `_compute_one(ticker)` + persist via `db.upsert_computed` + splice into `_computed`.
      404/422 on failure with yfinance's real error string as `detail`.
- [ ] Confirm `strategy_key` accepting `"manual"` needs zero backend validation changes (design
      doc confirmed `_validate_fill_body` already accepts any string — verify still true after
      Phase 1's changes to that function).

### 2b. Frontend — types & constants

- [ ] `frontend/src/api/types.ts`: widen `StrategyKey`-adjacent unions that include
      `strategy_key` to accept `"manual"` (Position/Fill types, wherever currently a 3-value
      literal union).
- [ ] Display-label fallback for `strategy_key === "manual"` wherever `ADV_STRAT_KEY`/similar
      maps are used to render a human label (Trades page table, position detail page) — add a
      `"Manual"` fallback for values outside the 3-entry map rather than extending
      `ADV_STRATEGIES` itself (design doc explicit: not a 4th filter option).

### 2c. Frontend — API client

- [ ] `frontend/src/api/tickers.ts`: add `fetchOneTicker(ticker: string)` per the design doc's
      signature.

### 2d. Frontend — new component

- [ ] New `AddTradeTickerModal` component (structurally alongside `WatchlistPickerModal`/
      `ExportPickerModal`) implementing the idle → fetching → {success|error} state machine from
      the design doc, including the post-fetch `GET /api/positions?status=open` branch that
      routes to either the existing Add Fill flow or a fresh `TradeConfirmModal` with
      `stratKey: "manual"`.
- [ ] `DashboardPage.tsx`: new "+ Add Trade" button, before Select All, always visible (outside
      the `selected.size === 0` conditional block). Wires to open `AddTradeTickerModal`; its
      success callback constructs `tradeFlow`/`existingPosition` state exactly as
      `openTradeFlow()` does today, per the design doc's two branches.

### 2e. Verify

- [ ] `tsc -b --noEmit`, full `vitest run`.
- [ ] Manual end-to-end: click "+ Add Trade", enter a ticker not in the current universe, confirm
      Fetch & Compute succeeds and opens `TradeConfirmModal` with `stratKey: "manual"`, no
      Estimate Entry section, correct current price prefilled. Submit a trade, confirm it
      persists and the ticker keeps showing up on future refreshes (via the existing
      `_active_tickers()` open-position rule — no new code, just confirm the existing mechanism
      actually covers it).
- [ ] Manual: enter a ticker that already has an open position, confirm it routes to Add Fill
      instead of the new-trade form.
- [ ] Manual: enter a bad/nonexistent ticker, confirm the inline error blocks progress and the
      user cannot reach a trade form from that state.

---

## Cross-cutting notes

- Both phases touch `TradeConfirmModal.tsx` — Phase 1 removes the option Entry Price field,
  Phase 2 adds the `stratKey: "manual"` case. No direct conflict (different conditions), but
  implement Phase 1's change first and confirm it's merged/stable before starting Phase 2's edits
  to the same file, per the sequencing decision above.
- Neither phase touches `Dockerfile`/`docker-compose.yml`/`deploy.sh` — pure application-code
  changes, existing deploy pipeline covers both once merged.
- Remember the recurring stale-build gotcha from this session: `backend/static_frontend/` is a
  prebuilt bundle — rebuild it (`npm run build` + recopy) before manually testing either phase
  against the backend directly on port 8123, or just use `npm run dev` (Vite) for iteration,
  which always reflects current source.

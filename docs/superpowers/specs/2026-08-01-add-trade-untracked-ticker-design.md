# Add Trade for an Untracked Ticker — Design

Date: 2026-08-01
Status: Design, not implemented

## Background

The TRADE button today only exists inside a ticker's strategy detail modal on the Dashboard
(`StrategyDetailModal` → `onTrade` → `openTradeFlow(ticker, stratKey)` in `DashboardPage.tsx`).
That flow hard-requires a `TickerPayload` row already present in `useTickers()`'s data — it
reads `row.price` and `row[stratKey].open_position` to prefill the form
(`DashboardPage.tsx:129-143`). A ticker the screener never picked up (outside the configured
cap/volume/price/exchange filters, or just not in the ~2000-ticker universe at all) has no such
row, so there's currently no way to log a trade on it without it first passing the screener.

This design adds a second entry point: a "+ Add Trade" button in the dashboard header, next to
Select All, that lets the user type any ticker, fetch+compute it on demand, then falls through
into the *same* `TradeConfirmModal` used everywhere else — no parallel trade-entry UI to
maintain.

## Explicit non-goals

- This does **not** add the ticker to the screened universe permanently. It's fetched/computed
  once so the trade form has real data to prefill from; whether it keeps getting refreshed after
  that is governed entirely by the existing "does it have an open position" rule (see below), not
  by anything new.
- This does not change `TradeConfirmModal` itself — it's reused as-is. The only new UI is the
  ticker-entry step that runs *before* it.
- This does not attempt to validate the ticker is investable (i.e. run it through
  `passes_technical_filters`) — a manual trade is explicitly a user override; the whole point is
  to accept tickers that fail or were never subject to the automatic screen.

## The strategy_key question

Every existing fill row (`position_fills.strategy_key`) is one of `vexh`/`vcp`/`vcpo`, and
`POST /api/positions` currently makes `body["strategy_key"]` a **hard required key** (raw
`KeyError` if absent — see `_build_fill()`, `app.py:777-794`). A trade on an untracked ticker has
no strategy signal behind it; it's the user manually saying "I'm trading this," not the app
saying "VEXH says take this."

Two options considered:

1. **Reuse an existing strategy_key as a label of convenience.** E.g. always tag manual trades
   `vexh`. Cheapest to build, but semantically wrong — every downstream read of `strategy_key`
   (the Trades page's per-strategy filter, `/api/positions/analytics?strategy=`, the position
   detail page's fills table) would misrepresent a manual trade as a VEXH signal that never
   fired. Rejected.
2. **Add `"manual"` as a fourth valid `strategy_key` value.** Requires touching every place that
   enumerates the three strategy keys as a closed set (there are several — see Backend changes
   below) but keeps the data honest: a manual trade is visibly and queryably distinct from a
   strategy-triggered one everywhere it shows up.

**Decision: option 2.** The alternative (silently mislabeling data) causes worse problems later
than the one-time cost of threading a fourth key through now.

## Flow

```mermaid
flowchart TD
    A["+ Add Trade button\n(dashboard header, before Select All,\nalways visible)"] --> B[Modal opens: ticker input\n+ Fetch & Compute button]
    B --> C{User types ticker,\nclicks Fetch & Compute}
    C --> D["POST /api/tickers/fetch-one\n{ticker}"]
    D --> E{Fetch + compute\nsucceeded?}
    E -->|No: bad ticker,\nno data, etc| F["Inline error in the modal.\nUser CANNOT proceed to the trade\nform from this state -- must fix\nthe ticker and retry, or close."]
    E -->|Yes| G{Does this ticker already\nhave an open position?\nGET /api/positions?status=open}
    G -->|Yes -- proves it already\nhas data/was tracked before| H["Existing Add Fill flow opens\n(same as the dashboard-card TRADE\nbutton's existing behavior),\nnot the new-position form"]
    G -->|No| I["Same TradeConfirmModal opens,\nstratKey='manual', prefilled from\nthe fetched price -- no open_position\nprefill since there isn't one yet"]
    I --> J[User fills TP/stop/units/option fields,\nclicks Confirm Trade]
    J --> K["POST /api/positions\nstrategy_key: 'manual'"]
    K --> L[Position created --\nticker now has an open position,\nso _active_tickers() keeps it\nfetched/computed going forward\nwith zero new code]
```

The last step is the reason this design is cheap: `_active_tickers()` in `app.py` already unions
`candidate_tickers` (the screener's list) with every ticker that has an open position
(`app.py:188-204`). Once the manual trade is confirmed, the ticker is "traded" and therefore
already covered by that existing rule — no new persistence needed to keep it warm. The *only* new
problem to solve is the one-time fetch+compute *before* a position exists, so the form has a real
price to show.

## Backend changes

### 1. New endpoint: `POST /api/tickers/fetch-one`

```json
// request
{ "ticker": "ABCD" }

// response (success)
{
  "ticker": "ABCD",
  "price": 42.10,
  "date": "2026-08-01",
  "found": true
}

// response (failure) -- 422, not 200-with-error-field, so the frontend's existing
// apiPost error handling (ApiError) works unchanged
{ "detail": "no data" }
```

Implementation: a thin wrapper around the pipeline that already exists, run synchronously
in-request (this is explicitly a one-off, user-initiated, single-ticker action — not the bulk
background loop, so blocking the request while it runs is correct and simple, matching how
`POST /api/tickers?refresh=1` already blocks for the bulk case):

```python
@app.post("/api/tickers/fetch-one")
def fetch_one_ticker(body: dict):
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    data.warm_cache([ticker], force=True)          # fetch full history, bypasses the
                                                      # whole-batch freshness early-exit
                                                      # since this ticker was never fetched
    _, payload, err, checksum = _compute_one(ticker)
    if payload is None:
        raise HTTPException(status_code=422, detail=err or "no data")
    # Persist like any other computed ticker so it survives a restart and shows up in
    # _computed immediately -- same call compute_all() itself makes per ticker.
    now = time.time()
    db.upsert_computed(ticker, payload, payload["date"], now, None)
    with _compute_lock:
        _computed[:] = [p for p in _computed if p["ticker"] != ticker] + [payload]
    return {"ticker": ticker, "price": payload["price"], "date": payload["date"], "found": True}
```

Notes on reuse vs. new code:
- `data.warm_cache()` and `_compute_one()` are used completely unchanged — this endpoint is
  purely new orchestration around two functions that already do exactly what's needed.
- `warm_cache([ticker], force=True)` fetches the same `HISTORY_START` window a brand-new ticker
  in the normal pipeline would get (see `data.py`'s `HISTORY_START` docstring) — no special-cased
  shorter window, so the returned strategy signals (if the ticker happens to still pass the
  technical filter) are computed on the same amount of history as everywhere else.
- Does **not** call `passes_technical_filters()` or add the ticker to `candidate_tickers` — it's
  intentionally outside the screener, per Non-goals above.
- A ticker that legitimately has no data (typo, delisted, wrong exchange) surfaces yfinance's
  actual error string via `err`, shown inline in the modal — no special-casing of failure modes
  beyond what `_compute_one` already returns.

### 2. `strategy_key` becomes a 4-value enum: `vexh | vcp | vcpo | manual`

Places that currently treat the 3 strategy keys as a closed set and need to accept `"manual"`
too:

- `_validate_fill_body`'s implicit trust of `body["strategy_key"]` (currently doesn't validate
  the value at all, just requires it be present) — no change needed here, it already accepts any
  string; flagging it only so whoever implements this confirms that's still true after this
  design's other changes.
- `ADV_STRAT_KEY` / `ADV_STRATEGIES` (`frontend/src/constants/strategy.ts`) — the frontend's
  fixed 3-entry strategy list used by the Advance Filter, Trade-on filter, and the strategy
  detail modal's 3-column layout. **Not extended to include `"manual"`** — those are about
  filtering/comparing the screener's live signals, and "manual" isn't a signal to filter on. A
  manual position still needs to *display* correctly wherever `strategy_key` is shown as a label
  (Trades page table, position detail page) — those just need a display-label fallback
  (`"Manual"`) for a `strategy_key` value outside the 3-entry map, not a 4th filter option.
- `Position`/`Fill` TypeScript types (`frontend/src/api/types.ts`) — wherever `strategy_key` is
  typed as a union of the 3 literal strings, widen to include `"manual"`.
- Backend equivalent, if `strategy_key` is typed anywhere as a `Literal` (grep for it during
  implementation — the design doc author did not find one, `db.py`/`app.py` appear to treat it as
  a plain `str` throughout, so this may be a no-op).

### 3. `TradeConfirmModal` gets a `stratKey: StrategyKey | "manual"` case

The component itself needs minimal change: everywhere it currently reads
`openPosition`/`avgMaeWinsPct` to decide whether to show the "Estimate Entry" section
(`showEstimate = !!(op && avgMaeWinsPct != null)`), those stay `null`/`undefined` for the manual
flow — there's no `open_position` and no historical MAE stats for a strategy that never fired, so
the Estimate Entry section simply doesn't render (it's already conditional on `showEstimate`,
this is the existing "no signal yet" path, not new logic).

## Frontend changes

### 1. New button: dashboard header, before Select All

Confirmed: placed *before* Select All in reading order, always visible regardless of
`selected.size` (dashboard header currently renders Select All / Clear / Add / Export / Refresh
as a single conditional block — `DashboardPage.tsx:174-195`) — "+ Add Trade" is unrelated to the
multi-select flow and sits outside that conditional block entirely.

### 2. New component: `AddTradeTickerModal`

A small modal, structurally similar to `WatchlistPickerModal`/`ExportPickerModal` (the existing
small single-purpose modals), not `TradeConfirmModal` — this is step 1, not the trade form
itself:

```
┌─────────────────────────────────┐
│  Add Trade                       │
├─────────────────────────────────┤
│  TICKER                          │
│  [___________]  [Fetch & Compute]│
│                                   │
│  (idle / loading / error states  │
│   render here inline)            │
└─────────────────────────────────┘
```

State machine: `idle → fetching → { success → closes itself, hands off to either the
existing Add Fill flow or a fresh TradeConfirmModal (see below) | error → shows message inline,
stays open, Fetch & Compute stays the only actionable control -- there is no path from an error
state into the trade form. The user must fix the ticker and retry, or close the modal. }`.

On a successful fetch, before deciding which modal to hand off to, the component does the same
`GET /api/positions?status=open` lookup `openTradeFlow()` already does for the existing
dashboard-card flow, keyed on the fetched ticker:

- **Ticker already has an open position** (confirmed: this is possible and expected — it proves
  the ticker already has data/was tracked before, e.g. it fell out of the screener's current
  filters after the trade was opened). Skip the new-position form entirely; open the existing Add
  Fill modal against that position, identical to what happens today when a dashboard card's TRADE
  button is clicked for an already-open position.
- **No open position.** Proceed to `TradeConfirmModal` as originally designed, `stratKey:
  "manual"`.

```ts
// No open position for this ticker -- new-position flow, same shape openTradeFlow() already
// constructs, just sourced from the fetch response instead of an existing TickerPayload row.
setTradeFlow({
  ticker,
  stratKey: "manual",
  signalDate: todayIsoDate(),   // no strategy signal date exists; today is the only
                                  // meaningful default for a manual entry
  currentPrice: response.price,
});

// Open position already exists for this ticker -- reuse the existing Add Fill path exactly as
// openTradeFlow() does today (setExistingPosition(...) then the AddFillModal branch already
// present in DashboardPage's render).
```

`tradeRow`/`tradeStrategy` (currently derived by looking up `data?.tickers.find(...)`,
`DashboardPage.tsx:166-167`) will be `undefined`/`null` for a manual-flow ticker not in that
list — `TradeConfirmModal` already handles `openPosition ?? null` /
`avgMaeWinsPct ?? null` as its no-signal case (see Backend §3), so this falls through correctly
with no new branching in `DashboardPage` beyond constructing `tradeFlow` from a different source.

### 3. New API client function

`frontend/src/api/tickers.ts` gets one addition alongside the existing `getTickers`/`getMeta`:

```ts
export function fetchOneTicker(ticker: string): Promise<{ ticker: string; price: number; date: string; found: true }> {
  return apiPost("/api/tickers/fetch-one", { ticker });
}
```

## Error handling / edge cases

All decisions below confirmed by the user; no open questions remain.

- **Ticker already has an open position.** Not an error case at all — confirmed this means the
  ticker already has data (was tracked before, e.g. fell out of the screener's current filters
  after the trade opened). The modal detects this via `GET /api/positions?status=open` right
  after a successful fetch and routes to the existing Add Fill flow instead of the new-position
  form (see Frontend §2). `POST /api/positions`'s existing 409 (`app.py:854-855`) becomes a
  true "shouldn't normally happen" backstop rather than the primary handling path for this case —
  it'd only fire if a position were opened concurrently between the lookup and submit, which
  `TradeConfirmModal`'s existing error display already handles with no new code.
- **Ticker doesn't exist / yfinance has nothing for it.** Surfaced as the fetch-one endpoint's 422
  with yfinance's real error text, shown inline in `AddTradeTickerModal`. Confirmed: this blocks
  progress entirely — there is no path from this error state into the trade form. Fetch & Compute
  is the only actionable control; the user must fix the ticker and retry, or close the modal.
- **Rate limiting.** `warm_cache`'s existing `YFRateLimitError` handling
  (`data.py`, sentinel-based early-exit) applies unchanged — a single-ticker call is far less
  likely to trip it than the bulk path, but if it does, the same error propagates through
  `_compute_one`'s `err`, surfaces as the same blocking inline error described above.
- **Client-side ticker format validation.** Confirmed: none — round-tripping through the
  fetch-one endpoint and showing its error is sufficient. No pre-flight validation before the
  network call.
- **User types a ticker that's already in the loaded universe (but has no open position).** Works
  fine, just redundant — the fetch-one call still runs (accepted inefficiency; detecting
  "already loaded, skip straight to the trade form" is a nice-to-have, not required for
  correctness, and a candidate for a later optimization, not part of this design's critical
  path).

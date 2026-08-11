# Dashboard Markdown Export — Replaces PDF/CSV

Status: implementation doc, not yet built. Written per explicit instruction to not implement
yet.

## What's changing

The dashboard's current Export feature (`GET`/checkbox-select → PDF or CSV, via
`ExportPickerModal.tsx` → `POST /api/export/pdf` / `POST /api/export/csv`) is **replaced
entirely** by a single Markdown export, copy-to-clipboard in a modal (same UX pattern as the
Trades page's `TradesExportModal.tsx` -- no file download).

Two scope changes at once:
1. **Format**: PDF and CSV are removed. Markdown only.
2. **Content**: no longer a user-selected subset of tickers (the checkbox-selection UI goes
   away for this feature). The export always contains the full current set of **pending
   signals** (fresh TAKE, not yet entered) and **open signals** (a strategy's own simulated
   backtest is IN TRADE) that fired within the **last 5 days** -- both concepts already exist,
   built for the daily review chatbot's snapshot (`build_daily_snapshot()` in `app.py`), reused
   here with a wider day cutoff (the review's own `open_signals` caps at 3 days; this export
   caps at 5, as its own independent constant -- the review's 3-day cap is NOT changed).

## Confirmed decisions (from clarifying questions)

- **Data source**: same `pending_signals`/`open_signals` concept and computation the daily
  review already uses -- same quality-bar gate (`_passes_alert_quality_bar`), same entry-plan
  estimate (`entry_estimate.estimate_entry`, support/resistance-based). Widened day cutoff (5,
  not 3) is local to this new code path, not a shared constant change.
- **Replace, not add**: PDF/CSV export removed from the dashboard entirely. No third option,
  no ticker-selection dependency.
- **Server-side computation**: a new backend endpoint reuses the exact Python logic already in
  `build_daily_snapshot()`'s pending/open-signal blocks (not re-implemented in TypeScript) --
  avoids a second, potentially-drifting implementation of the quality-bar filter and
  support/resistance-based entry-plan math.

## Backend changes

**New shared helper** (or refactor point): `build_daily_snapshot()`'s `pending_signals` block
(`app.py` ~line 1657-1694) and `open_signals` block (~line 1696-1744) are each a self-contained
loop over `_computed` with no dependency on anything else in `build_daily_snapshot()` besides
`open_positions` (used only to exclude tickers already actually held, for `open_signals`). Both
should be factored into standalone functions, e.g.:

```python
def _pending_signals(computed_snapshot: list[dict]) -> list[dict]:
    ...  # exact body of the current inline loop, unchanged

def _open_signals(computed_snapshot: list[dict], open_position_tickers: set[str], max_days: int) -> list[dict]:
    ...  # exact body of the current inline loop, but days_held cutoff becomes the max_days param
```

`build_daily_snapshot()` calls these with `max_days=3` (unchanged behavior); the new export
endpoint calls `_open_signals` with `max_days=5`. `_pending_signals` needs no day-cutoff param at
all (a pending signal is inherently "fired today," no accumulated age to cap).

**New endpoint**: `GET /api/export/dashboard-md` (or `POST`, if any request body ends up needed
-- likely not, since this isn't ticker-scoped anymore). Returns `{"markdown": "..."}` (matching
the pattern of returning a string for the frontend to render in a copy modal, not a file
download/attachment header like the PDF/CSV endpoints use).

**Markdown structure** (draft, subject to review once built as a sample per this project's usual
"show a sample before implementing" convention):

```markdown
# Dashboard Export — 2026-08-11

## Pending Signals (fresh TAKE, not yet entered)

| Ticker | Strategy | Score | Trades | Win Rate | PF | Current Price | Entry Plan |
|---|---|---|---|---|---|---|---|
| BKR | VCPO | 8 | 22 | 77% | 2.8 | $61.20 | Limit $60.10, cancel by 10:30am |

## Open Signals (strategy's own simulated trade, not yet entered by you)

| Ticker | Strategy | Score | Days Since Signal | Entry (signal) | Unrealized % if entered | Entry Plan |
|---|---|---|---|---|---|---|
| ELF | VEXH | 9 | 2 | $88.10 (2026-08-09) | +3.5% | Limit $87.00, cancel by 10:30am |

*No pending signals right now.* / *No open signals within 5 days.* -- empty-state text per
section, matching this project's existing export empty-state convention (`tradesExport.ts`'s
`*No open positions.*` etc.).
```

Column set drawn directly from what `pending_signals`/`open_signals` dicts already carry
(`ticker`, `strategy`, `score`, `n_trades`, `win_rate`, `profit_factor`, `current_price`,
`entry_plan`, plus `open_signals`-only fields `days_since_signal`, `signal_entry_date`,
`signal_entry_price`, `unrealized_pct_if_entered`) -- no new data computed, just rendered as
Markdown instead of JSON. `entry_plan` itself is a nested dict (`entry_estimate.estimate_entry`'s
return shape) -- needs its own one-line rendering, e.g. `"Limit ${recommended_limit}, cancel by
10:30am"` for spot or whatever `order_method()` returns for options; confirm exact wording
against `entry_estimate.py`'s real return fields before building (see Open Questions).

## Frontend changes

**Removed:**
- `frontend/src/components/molecules/ExportPickerModal.tsx` (or repurposed if nothing else uses
  the PDF/CSV picker shell -- check for other callers before deleting).
- `exportCsv`/`exportPdf` from `frontend/src/api/plCalc.ts` (check for other callers first).
- The `selected` checkbox-selection state's role in export specifically (the checkboxes
  themselves may still exist for other dashboard features -- confirm before removing the
  underlying selection mechanism itself, only its use in `runExport`).
- `runExport()`'s `format: "pdf" | "csv"` branching in `DashboardPage.tsx`.

**Added:**
- A new API function, e.g. `getDashboardExportMarkdown(): Promise<string>` in
  `frontend/src/api/plCalc.ts` (or a new `api/dashboardExport.ts`), calling the new backend
  endpoint.
- Reuse `TradesExportModal.tsx`'s pattern directly -- either generalize it to accept any
  `markdown` string (it already does, `{ markdown, onClose }`) and reuse it as-is for the
  dashboard too, or copy it into a `DashboardExportModal.tsx` if the two ever need to diverge
  (title text differs: "Export Trades (Markdown)" vs. something like "Export Dashboard
  (Markdown)" -- likely just needs a `title` prop added to make the existing component reusable
  rather than duplicated).
- `DashboardPage.tsx`'s Export button: `onClick={() => setModal({ kind: "export" })}` stays, but
  the handler becomes a simple fetch-then-open-modal (no format branching, no `selected` tickers
  in the request) instead of `runExport(format)`.

## Testing plan

- Backend: unit tests for `_pending_signals`/`_open_signals` (once factored out) -- reuse
  existing coverage of the inline logic if any exists in `build_daily_snapshot()`'s test suite
  (check first), otherwise new tests covering the quality-bar gate, the `max_days` cutoff
  boundary (exactly `max_days` days old = included; `max_days + 1` = excluded), and the
  `open_position_tickers` exclusion (a real open position's own strategy is never double-listed
  as an "open signal").
- Backend: a test for the new endpoint asserting the returned markdown contains expected
  sections/empty-state text for known fixture data.
- Frontend: no client-side computation to test (server returns final markdown), but a smoke
  test that the export button calls the endpoint and renders the modal with returned text.
- Manual verification: trigger the real endpoint against live prod-like data, confirm the
  pending/open signal counts and fields match what the daily review chatbot's own "Take — Enter
  Tomorrow" section already shows for the same day (same underlying computation, just a
  different rendering and a wider day cutoff for open signals) -- cross-check, don't just trust
  the new code path blindly.

## Open questions (need answers before implementing)

1. **Exact Markdown table columns.** The draft above is a first guess at which fields to
   surface -- confirm the real desired column set (e.g. include `first_trade_date`? Drop `PF`?
   Include the ticker's `prebreak` state, matching the new Pre-Breakout Summary pattern from the
   Trades export?) before building. This project's convention is to build a sample and get it
   validated before writing the real implementation -- follow that here too, same as the Trades
   MD export was built.
2. **`entry_plan` rendering.** Need the exact field names on `entry_estimate.estimate_entry()`'s
   return dict and `order_method()`'s return value to render a correct one-line order
   description -- read `entry_estimate.py` directly before implementing, don't guess the shape.
3. **Endpoint method/auth.** `GET` with no body seems right (no ticker selection needed
   anymore), but confirm this doesn't need `_require_daily_review_enabled()`-style gating or any
   other guard the existing PDF/CSV endpoints don't have (they're unguarded except for the
   strategy-key validation) -- likely none needed, but worth a explicit no before implementing.
4. **Selection checkboxes' other uses.** RESOLVED by inspection -- `selected` (the ticker
   checkbox state, `DashboardPage.tsx` ~line 65) is also used for bulk watchlist add (~line 273,
   `for (const tk of selected) addToList(name, tk)`), not export-only. The selection UI itself
   stays; only its wiring into the Export button/`runExport()` changes -- the checkboxes remain
   for the watchlist-add feature.

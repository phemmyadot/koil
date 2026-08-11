# Pre-Breakout Summary — Strategy Modal + Trades Export

Status: implementation doc, not yet built. Written per explicit instruction to not implement
yet.

## What's being added

Two separate surfaces get a new pre-breakout summary line, both reading from the SAME
underlying data (`PrebreakResult`, already computed per ticker by `backend/prebreak.py` and
already rendered visually today by `frontend/src/components/molecules/PrebreakChips.tsx`). This
doc adds a **text** (comma-separated) rendering of the same six fields `PrebreakChips` already
shows as colored chips — no new backend computation, no new data source.

1. **Strategy modal** (`StrategyDetailModal.tsx`) — used from both the Dashboard and the Trades
   page (via `StrategyCellLink.tsx`) — gets a new section above the existing trade-history
   rows (Trades/Profit factor/Win rate/Since/...).
2. **Markdown trades export** (`tradesExport.ts` / `TradesPage.tsx`) — gets a new section below
   the existing Open/Closed Exits tables, listing one pre-breakout summary line per unique
   ticker currently active or closed today.

## The comma-separated format

Matches the user's corrected example exactly -- a single fixed `"Pre-Breakout:"` label prefix,
NOT a title-cased restatement of the state (that redundant first segment from the original
example is dropped):

```
Pre-Breakout: PRE-BREAKOUT (4), COMPRESSED, DRY, COILING, BULLISH, 42 Bars
```

Field-by-field mapping, in order, from `PrebreakResult` (see `PrebreakChips.tsx` for the exact
existing boolean→word mapping this reuses verbatim, just as comma-separated text instead of
colored chips):

| Segment | Source | Notes |
|---|---|---|
| `Pre-Breakout:` | Fixed label prefix | Literal string, not derived from `state` -- same prefix on every line regardless of the ticker's actual state. |
| `PRE-BREAKOUT (4)` | `state` + `score`, verbatim | Exactly matches `PrebreakChips`' first chip: `` `${pb.state} (${pb.score})` `` |
| `COMPRESSED` | `bb_squeeze` | `true` → `"COMPRESSED"`, `false` → `"EXPANDED"` (matches `PrebreakChips` exactly) |
| `DRY` | `vol_dry_up` | `true` → `"DRY"`, `false` → `"NORMAL/HIGH"` |
| `COILING` | `near_resistance` | `true` → `"COILING"`, `false` → `"CLEAR"` |
| `BULLISH` | `is_bullish_trend` | `true` → `"BULLISH"`, `false` → `"BEARISH"` |
| `42 Bars` | `squeeze_counter` | `` `${squeeze_counter} Bars` `` |

A single pure function, e.g. `prebreakSummaryLine(pb: PrebreakResult): string`, produces this
whole string (including the `"Pre-Breakout: "` prefix). Lives in `frontend/src/lib/format.ts`
(alongside the other small formatting helpers like `exitLabel`) so both the modal and the export
import the same one implementation — no duplicated word-mapping logic. This removes Open
Question 1 from the original draft entirely (no state-label casing decision needed since the
prefix is now fixed text, not derived from `state`).

## 1. Strategy modal section

**Files touched:**
- `frontend/src/components/molecules/StrategyDetailModal.tsx` — new prop `prebreak: PrebreakResult | null`, new section rendered right after the `<Modal>` opens, before the existing `<ModalRow label="Trades" ...>` block ("above trades section" per the request).
- `frontend/src/components/molecules/StrategyCellLink.tsx` — already has `row` (the full `TickerPayload`, via `useTickers()`) in scope where `StrategyDetailModal` is rendered (line ~38). Pass `prebreak={row?.prebreak ?? null}`.
- `frontend/src/pages/DashboardPage.tsx` — already has `row` in scope at its own `StrategyDetailModal` render site (line ~253-262, same pattern). Pass `prebreak={row?.prebreak ?? null}`.

**Rendering:** if `prebreak` is `null` (insufficient history for this ticker, same condition
`PrebreakChips` already handles by not rendering), skip the new section entirely — no empty
row, no placeholder. If present, render one line (plain text or a small `<div>`/`<ModalRow>`-style
row) containing the full `prebreakSummaryLine(prebreak)` output, followed by a `<div
className="modal-sep" />` before the existing Trades row, matching the modal's existing visual
separator convention between sections.

No new data fetching — `prebreak` already arrives with every `TickerPayload` from the existing
`useTickers()` call both call sites already make.

## 2. Trades export section

**Files touched:**
- `frontend/src/lib/tradesExport.ts` — `buildTradesExportMarkdown()` gains a new parameter,
  e.g. `prebreakByTicker: Record<string, PrebreakResult | null>`, and a new section appended
  after the existing four tables (Spot Open/Closed Exits, Options Open/Closed Exits).
- `frontend/src/pages/TradesPage.tsx` — the caller. Needs to:
  1. Call `useTickers()` (already used elsewhere in the app, e.g. `StrategyCellLink`/
     `DashboardPage` — React-Query cached under `["tickers"]`, so this doesn't trigger a new
     fetch if the dashboard was visited this session; if the user goes straight to Trades first,
     this fetches the full ticker universe fresh -- confirmed acceptable, see Resolved Questions
     above).
  2. Build the deduplicated ticker set: every `Position.ticker` where `units_remaining > 0`
     (across both spot and options, from the already-loaded `spotPositions`/`optionsPositions`),
     UNION every `Position.ticker` where `closed_at` falls on today's date (same "today" boundary
     the backend's `closed_today_*` snapshot logic uses — see the remaining Open Question below,
     since frontend "today" and the backend's trading-day boundary can differ).
  3. Build `prebreakByTicker` by looking up each of those tickers in the `useTickers()` response
     (`data.tickers.find(t => t.ticker === ticker)?.prebreak ?? null`).
  4. Pass `prebreakByTicker` into `buildTradesExportMarkdown()`.

**New section shape** (appended at the end of the generated markdown):

```markdown
## Pre-Breakout Summary

| Ticker | Pre-Breakout |
|---|---|
| BKR | Pre-Breakout: PRE-BREAKOUT (4), COMPRESSED, DRY, COILING, BULLISH, 42 Bars |
| ELF | Pre-Breakout: BULLISH (1), EXPANDED, NORMAL/HIGH, CLEAR, BULLISH, 0 Bars |
```

One row per unique ticker (alphabetical, matching the existing tables' apparent ticker
ordering convention — confirm), using the same `prebreakSummaryLine()` function as the modal. A
ticker with no `prebreak` data (`null`) gets a row with `—` in the Pre-Breakout column rather
than being silently dropped, so the user can see every active/closed-today ticker was at least
considered.

If the combined ticker set is empty (no open positions and nothing closed today), render
`*No active or today-closed tickers.*` instead of an empty table, matching this file's existing
empty-state convention (`*No open positions.*`, `*No closed exits yet.*`).

## Shared helper

```typescript
// frontend/src/lib/format.ts (new export, alongside exitLabel)

export function prebreakSummaryLine(pb: PrebreakResult): string {
  return (
    "Pre-Breakout: " +
    [
      `${pb.state} (${pb.score})`,
      pb.bb_squeeze ? "COMPRESSED" : "EXPANDED",
      pb.vol_dry_up ? "DRY" : "NORMAL/HIGH",
      pb.near_resistance ? "COILING" : "CLEAR",
      pb.is_bullish_trend ? "BULLISH" : "BEARISH",
      `${pb.squeeze_counter} Bars`,
    ].join(", ")
  );
}
```

(Sketch only — not final code. `PrebreakResult` needs importing from `../api/types`.)

## Testing plan

- New `prebreakSummaryLine` unit tests in a new or existing `format.test.ts` covering: the fixed
  `"Pre-Breakout: "` prefix appears on every output regardless of state, both branches of each
  boolean field, and the full comma-separated output matching the user's corrected example
  string exactly.
- `tradesExport.test.ts` — new test(s) asserting the Pre-Breakout Summary section appears with
  correct rows for a mix of open + closed-today + neither tickers, correct dedup when a ticker
  has both an open position AND closed today (shouldn't appear twice), and the empty-state
  message when there's nothing to list.
- Manual verification: open the strategy modal from both Dashboard and Trades page for a real
  ticker with known prebreak state, confirm the line matches what `PrebreakChips` shows visually
  for that same ticker. Generate a real export with at least one open position and one
  today-closed position, confirm the new section lists both correctly.

## Resolved questions

1. **`useTickers()` fetch cost on the Trades page.** RESOLVED — acceptable for the Trades page
   to trigger its own `useTickers()` fetch (the full ticker universe) even if the user hasn't
   visited the Dashboard first this session; no lazy/scoped-fetch alternative needed. Call
   `useTickers()` directly in `TradesPage.tsx` same as `StrategyCellLink`/`DashboardPage` already
   do -- React Query dedupes it against any other in-flight/cached `["tickers"]` query
   automatically either way.

2. **"Today" boundary.** RESOLVED — "closed today" means closed today in the user's browser
   LOCAL time, not the backend's trading-day boundary (`market_hours.most_recent_close_boundary()`,
   used by `closed_today_*` in `build_daily_snapshot()` for the chatbot -- deliberately not
   reused here). `closed_at` is stored as a UTC timestamp, so the comparison must convert to a
   local calendar date on both sides, NOT compare raw UTC date strings (`.slice(0,10)` on the
   ISO string alone would compare UTC dates, which is wrong for a user in any timezone behind
   UTC where evening trades still show yesterday's UTC date):

   ```typescript
   function isClosedToday(closedAt: string): boolean {
     const closedLocal = new Date(closedAt);
     const now = new Date();
     return (
       closedLocal.getFullYear() === now.getFullYear() &&
       closedLocal.getMonth() === now.getMonth() &&
       closedLocal.getDate() === now.getDate()
     );
   }
   ```

   Lives alongside the other small date helpers in `frontend/src/lib/dates.ts` (already home to
   `todayIsoDate()`, used elsewhere in `TradesPage.tsx`).

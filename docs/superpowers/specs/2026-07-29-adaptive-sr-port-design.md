# Adaptive SR Levels — Python Port for Estimate Entry

Date: 2026-07-29
Status: Design, not implemented

## Background

`webapp/entry_estimate.py`'s `estimate_entry()` (see
`docs/superpowers/specs/2026-07-29-estimate-entry-design.md`) takes `support_levels: list[float]`
as an argument but currently has no source for it — the design deliberately deferred
support-level detection, shipping a manual text-input field instead ("wrong support levels
produce wrong limits, which is worse than no automation").

`pines/sr.pine` ("Adaptive SR Levels") is a from-scratch pivot-clustering indicator that
computes exactly this: swing-high/low pivots, merged into tolerance-banded zones, with the
nearest zones on each side of price selected for display. It has no TradingView-only
primitives in its actual math (`ta.pivotlow`/`ta.pivothigh` are simple rolling-window
extrema, not `security()`/multi-symbol/repainting-only calls), so it ports to pandas/numpy
directly. This spec replaces the manual input with this port, per explicit instruction:
"no manual support or resistance input."

## What gets removed

- The `support_levels` free-text field in the Estimate modal (`webapp/static/index.html`'s
  `showEstimateModal()`) — deleted, not kept as an override alongside the computed value.
- The `/api/estimate_entry` request body's `support_levels` field, previously typed by the
  user — replaced by levels the backend computes itself from stored bars.

`entry_estimate.py`'s `estimate_entry()` function itself is unchanged — it already accepts
`support_levels: list[float]`; only what feeds that argument changes.

## Algorithm (ported from `pines/sr.pine`)

This dashboard is daily-bars-only (`webapp/data.py`'s `HISTORY_START`/`interval="1d"`), so
the Pine indicator's `autoStrength` branch always resolves to `timeframe.isdaily` →
`pivotStrength = 10`. The Python port hardcodes `strength = 10` — the auto-adapt branching
for intraday timeframes doesn't apply and isn't ported.

1. **Pivot detection.** For each bar `i` (needs `strength` bars on both sides, so only bars
   `[strength, len(df) - strength - 1]` are checked): it's a pivot low if `low[i]` is the
   strict minimum over `low[i-strength : i+strength+1]`; pivot high is the mirror on `high`
   being the strict maximum. This matches `ta.pivotlow`/`ta.pivothigh(src, strength, strength)`.

2. **Zone clustering.** Walk pivots in chronological order. For each pivot price `p` (using
   that bar's ATR(14), Wilder-smoothed to match Pine's `ta.atr`, and that bar's close):
   `tol = max(0.75 * atr, 0.005 * close)`. If an existing zone is within `tol` of `p`, merge
   by touch-count-weighted average (`new_price = (zone_price * touches + p) / (touches + 1)`,
   `touches += 1`) and update the zone's last-touched bar index. Otherwise start a new zone
   with `touches = 1`. Cap at 60 zones total, FIFO-evicting the oldest when exceeded (matches
   `SR_MAX_ZONES = 60`).

3. **Selection.** At the *last* bar only (current price = latest close): split zones into
   `support` (`zone_price < current_price`) and `resistance` (`zone_price > current_price`).
   Sort support descending (nearest-to-price first) and take the top 3; sort resistance
   ascending and take the top 3. This is behaviorally equivalent to Pine's outward-walk
   selection loop (`beyond`/`closer` checks each iteration) — confirmed by tracing it: the
   loop has no touch-count tiebreak in selection, so a plain price sort produces the same
   3 levels Pine draws. `SR_LEVELS_EACH_SIDE = 3` is hardcoded to match the Pine default;
   not user-configurable here (no UI control for it exists or is planned).

## Module: `webapp/support_resistance.py`

```python
def compute_sr_levels(bars: pd.DataFrame) -> dict:
    """Returns {"support": [float, ...], "resistance": [float, ...]}, each up to 3 levels,
    nearest-to-current-price first. bars: standard OHLCV DataFrame (webapp/data.py's shape),
    at least ~2*strength+1 rows -- fewer returns empty lists for both, not an error out of
    a strategy's normal min_bars gate (matches sr.pine's own "if array.size > 0" no-op)."""
```

Pure function over an already-fetched `bars` DataFrame — no I/O, no network, no DB access,
matching `entry_estimate.py`'s existing style. Internals: an ATR(14) helper for the
tolerance band — `build_universe.py:111` already has `_wilder_atr(high, low, close, length)`
with the exact Wilder-smoothing formula needed, but it's underscore-prefixed (module-private
by convention, not meant for cross-module import). Rather than import a private helper across
modules, `support_resistance.py` gets its own copy of the same 2-line rolling-TR/EWM formula
— duplication is preferable here to weakening `build_universe.py`'s privacy convention for a
one-function dependency. The rest (pivot scan + zone merge/evict + final sort) is new.

## Integration point

`webapp/app.py`'s `/api/estimate_entry` endpoint (already reads `payload["price"]` and
`open_position` for the given ticker+strategy) additionally calls `data.get_bars(ticker)`
(the same in-memory bars every other compute path reads — no new fetch) and
`support_resistance.compute_sr_levels(bars)`, passing `levels["support"]` as
`entry_estimate.estimate_entry()`'s `support_levels` argument. The request body's
`support_levels` field is removed; the endpoint takes no support-related input from the
client at all now.

Response gains one field: `sr_levels_considered: [float, ...]` (the up-to-3 support levels
that were candidates, for the "levels considered" transparency the design calls for) — the
existing `support_used` field (which of those, if any, was actually the max below current
price) is unchanged.

## Frontend changes

`webapp/static/index.html`'s `showEstimateModal()`/`calcEstimateEntry()`:

- Remove the `#eeSupport` input and its `formrow`.
- `calcEstimateEntry()` no longer reads/sends `support_levels` in the POST body — the
  endpoint computes it server-side now.
- Render `sr_levels_considered` as a small inline list (e.g. "Support levels considered:
  $58.50, $54.20, $49.75") above the `mae_floor`/`support_used`/`recommended_limit` rows,
  so the user can see what fed the number instead of it being an opaque black box replacing
  what used to be their own chart read.
- No change to the options section (`eeOptionsForm`/`updateEeOptions()`) — untouched by
  this port, still typed fresh per the original design.

## Validation

Since there's no way to run the actual Pine indicator from this codebase, validation is by
spot-check: pick 2-3 tickers already in the DB, manually verify (by eye, against a plotted
chart of `Low`/`High` with the computed zones overlaid) that the detected pivots and merged
zones look like plausible swing points, not by trying to bit-for-bit match TradingView's
rendered lines (no automated oracle for that exists here). This is the same validation rigor
`build_universe.py`'s existing technical filters got — visually-checked, not
numerically-proven against a reference implementation.

## Out of scope

- Resistance levels are computed (needed for the merge/pivot logic to make sense as a direct
  port) but not consumed anywhere yet — `estimate_entry()` only uses supports. Exposed on the
  response for potential future use, not wired into any calculation now.
- Intraday timeframe support (the `autoStrength` branch for `timeframe.isintraday`) — not
  applicable, this dashboard is daily-bars-only.
- Any UI to reconfigure `pivotStrength`/`SR_LEVELS_EACH_SIDE`/tolerance constants — all
  hardcoded to the Pine indicator's own defaults, matching how the rest of `webapp/`'s
  strategy modules hardcode their own indicator parameters rather than exposing them.

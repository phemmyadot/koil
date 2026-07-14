# Design Spec: Approach C — Volatility Contraction Pattern (VCP) Breakout

## Goal
Depart from the pullback-buy thesis entirely and implement a Minervini-style
Volatility Contraction Pattern breakout system: identify stocks whose pullbacks
are shrinking in an established uptrend, then enter on a volume-confirmed
breakout above the contraction high. Research shows VCP breakouts have a
historically strong (60-70%) follow-through rate when volume-confirmed, making
this the highest-upside, highest-complexity of the three approaches to test.

## Scope
- Long only, daily chart, one symbol per chart, sub-$50 optionable names loaded
  manually.
- New file: `strategy_c_vcp_breakout.pine`.
- Shares the regime gate and position-sizing/ATR-stop skeleton with Approaches
  A/B, but the entry detection logic is structurally new (pattern detection over
  a rolling window of swing pullbacks, not a single-bar RSI/MA touch).

## Architecture

### 1. Regime gate (shared with A/B)
- `adx/plusDI/minusDI = ta.dmi(14, 14)`, `regimeOk = adx > 20 and plusDI > minusDI`
- `kama = ta.kama(close, 10, 2, 30)` as the trend backbone (price must be above
  KAMA for a VCP setup to qualify at all).

### 2. Swing pullback detection
- Use `ta.pivothigh`/`ta.pivotlow` with a fixed lookback (`pivotLen = 3`, hardcoded
  — standard swing-detection width) to identify the sequence of recent swing highs
  and swing lows.
- For the last 3 completed swings, compute each pullback's depth as
  `(swingHigh - swingLow) / swingHigh` (percentage range).
- `contracting = depth[0] < depth[1] and depth[1] < depth[2]` — each pullback
  strictly shallower than the one before it (the "VCP" condition).
- `contractionHigh = swingHigh[0]` (the most recent swing high — the breakout
  trigger level) and `contractionLow = swingLow[0]`.
- Setup validity window: pattern must have formed within the last `N` bars
  (hardcoded, e.g. 60 trading days ~ 3 months) to avoid triggering on ancient,
  stale swing points.

### 3. Entry trigger — volume-confirmed breakout
- `volAvg = ta.sma(volume, 50)`
- `volumeConfirmed = volume >= 1.4 * volAvg`
- `breakout = ta.crossover(close, contractionHigh)`
- `longSignal = regimeOk and contracting and breakout and volumeConfirmed`
- Fill at next bar's open (`process_orders_on_close = false`).
- Note on lookahead: `contractionHigh` is fixed once the pivot is confirmed
  (`ta.pivothigh` requires `pivotLen` bars of confirmation to its right), so by
  the time it's used as a breakout level it is not repainting — the same
  guarantee Pine's pivot functions give by construction.

### 4. Exit logic (shared skeleton with A/B, no KAMA trend-break exit)
- Initial stop: `entryPrice - 1.5 * atr` (same as A/B)
- Chandelier trail: `highestHighSinceEntry - 3.0 * atr` (fixed multiplier, as in
  Approach A — this spec doesn't combine VCP with ADX-scaled trail to keep the
  three approaches testing one variable each; that combination can be a later
  iteration if C tests well)
- Trend-break exit: close below `contractionLow` (the pattern's own invalidation
  level) rather than below KAMA — a breakout that falls back into its own base is
  a failed pattern regardless of where KAMA sits.

### 5. Position sizing
Identical to A/B: `riskPct` input (default 1.0%),
`qty = (strategy.equity * riskPct/100) / (1.5 * atr)`.

## Exposed inputs (target: 2)
1. `volMultiplier` (breakout volume threshold, default 1.4 — the one number in
   this spec most likely to warrant per-symbol tuning, since volume profiles vary
   more across tickers than trend/momentum indicators do)
2. `riskPct` (account risk per trade, default 1.0%)

Pivot lookback (3), swing-count (3), pattern staleness window (60 bars), ADX
length/threshold, KAMA constants, and ATR/stop/trail parameters are hardcoded.

## Data flow
Bar close → update pivot high/low series → on each new confirmed pivot,
recompute the last-3-swing depth sequence → `contracting` flag updates → breakout
+ volume + regime checked every bar → entry submitted, fills next open → exit
logic (ATR stop, Chandelier trail, contraction-low break) evaluated every bar
while in position.

## Edge cases
- Fewer than 3 confirmed swings on the chart yet (early in a symbol's data
  history): `contracting` naturally evaluates false (comparisons against `na`
  swing depths fail) — no explicit guard needed.
- A stock that's been trending too smoothly to form distinct swings (rare pivots)
  may never generate a signal — this is an expected trade-off of the pattern
  approach and should be visible in the backtest's trade count.
- Gap-up breakouts (open already above `contractionHigh`) : `ta.crossover` on
  daily bars will still fire since close crosses in the same direction; fill at
  next bar's open means entry could be well above the breakout level on a gappy
  stock — acceptable for v1, flagged here rather than silently ignored.

## Testing plan
- Same tickers/timeframe as A/B, run independently in Strategy Tester.
- Because this is the structurally different approach, pay particular attention
  to trade count (likely lowest of the three, per the "rare pivots" edge case)
  and compare win rate/PF against A and B to see whether the VCP thesis actually
  outperforms the pullback thesis for this universe, or just trades less often.

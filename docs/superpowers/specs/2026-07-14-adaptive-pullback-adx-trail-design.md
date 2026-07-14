# Design Spec: Approach B — Adaptive Pullback, ADX-Scaled Trail

## Goal
Same adaptive entry redesign as Approach A, plus one additional mechanism aimed
directly at the user's stated priority (profit factor over win rate): the
Chandelier trail multiplier scales with ADX strength, so trades in strongly
trending regimes get more room to run (bigger winners) while trades in
borderline-trending regimes get reined in faster (smaller losers/give-backs).

## Scope
- Long only, daily chart, one symbol per chart, sub-$50 optionable names loaded
  manually.
- New file: `strategy_b_adx_scaled_trail.pine`.
- Shares its regime gate, KAMA reference, entry trigger, and position sizing with
  Approach A verbatim — see [2026-07-14-adaptive-pullback-fixed-trail-design.md](2026-07-14-adaptive-pullback-fixed-trail-design.md)
  sections 1-3 and 5. Only the trail mechanics (section 4) differ, described below.

## Architecture

### 1-3. Regime gate, KAMA reference, entry trigger
Identical to Approach A:
- `adx/plusDI/minusDI = ta.dmi(14, 14)`, `regimeOk = adx > 20 and plusDI > minusDI`
- `kama = ta.kama(close, erPeriod, 2, 30)`, `erPeriod` exposed (default 10)
- `pulledBack = low <= kama and close > kama - 0.5*atr`
- `rsi = ta.rsi(close, 14)`, `rsiSignal = ta.sma(rsi, 9)`,
  `rsiResume = ta.crossover(rsi, rsiSignal)`
- `longSignal = regimeOk and pulledBack and rsiResume`
- Fill at next bar's open (`process_orders_on_close = false`, the default)

### 4. Exit logic — ADX-scaled Chandelier trail
- Initial stop: `entryPrice - 1.5 * atr` (unchanged from Approach A)
- **Trail multiplier becomes a function of current ADX reading**, recalculated
  each bar while the trail is active:
  `trailMult = math.max(2.5, math.min(4.5, 2.5 + (adx - 20) / 20))`
  - At `adx = 20` (minimum regime-qualifying strength): `trailMult = 2.5` (tighter
    than Approach A's fixed 3.0 — cut marginal trends loose sooner)
  - At `adx = 40`: `trailMult = 3.5`
  - At `adx >= 60`: clamped at `4.5` (very strong trend gets maximum room)
- Chandelier trail: `highestHighSinceEntry - trailMult * atr`, activates once
  trade is `>= 1.0 * atr` in profit, ratchets up only (same activation rule as A).
- Trend-break exit: close below KAMA (same as A).
- Rationale: the fixed 3x multiplier in the original strategy and in Approach A
  applies the same trail width regardless of how strong the underlying trend is.
  Scaling it with ADX means winners in the strongest trends are given
  proportionally more room before being stopped out — directly targeting a higher
  profit factor (bigger average winner) at the probable cost of a slightly lower
  win rate (more give-back in weaker trends), consistent with the user's stated
  "profit factor first" priority.

### 5. Position sizing
Identical to Approach A: `riskPct` input (default 1.0%),
`qty = (strategy.equity * riskPct/100) / (1.5 * atr)`.

## Exposed inputs (target: 2)
1. `erPeriod` (KAMA efficiency-ratio period, default 10)
2. `riskPct` (account risk per trade, default 1.0%)

ADX length/threshold, KAMA constants, RSI length/signal length, ATR length, initial
stop multiplier, trail activation threshold, and the ADX-to-trailMult mapping
constants (2.5 floor, 4.5 ceiling, /20 scaling) are all hardcoded per the research
above.

## Data flow
Identical to Approach A, with one addition: on every bar where the trail is
active, `trailMult` is recomputed from the current ADX reading before the
Chandelier level is calculated, so the trail width can change bar-to-bar as trend
strength evolves during the trade (it can only make the stop price move up,
never down — the `math.max(stopPrice, chandelier)` ratchet from the current code
is preserved).

## Edge cases
- Same `na`-propagation guards as Approach A for warm-up bars.
- If ADX weakens sharply mid-trade (trend decaying), `trailMult` shrinks toward
  2.5, tightening the trail faster than Approach A would — this is intentional
  (a decaying trend should give back less), but worth watching in backtests for
  whipsaw-driven premature exits versus Approach A.

## Testing plan
- Same tickers/timeframe as Approach A, run side by side in Strategy Tester.
- Specifically compare: average winning trade size, average losing trade size,
  and overall profit factor between A and B to confirm the ADX-scaling actually
  moves PF in the intended direction rather than just adding noise.

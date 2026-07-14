# Design Spec: Approach A — Adaptive Pullback, Fixed Trail

## Goal
Redesign the pullback-in-trend strategy from `strategy.pine` to minimize manually-tuned
inputs by replacing fixed EMA/RSI thresholds with self-adjusting equivalents, while
keeping the exit mechanics (ATR stop + Chandelier trail) unchanged from the current
implementation. This is the "adaptive, but otherwise conservative" variant of the
redesign — a control against which Approach B (ADX-scaled trail) can be compared.

## Scope
- Long only, daily chart, one symbol per chart (Pine strategy convention), sub-$50
  optionable names loaded manually by the user.
- New file: `strategy_a_adaptive_pullback.pine`. Existing `strategy.pine` is left
  untouched as a reference/baseline.
- No position-sizing or multi-symbol scanning beyond what's specified below.

## Architecture

### 1. Regime gate (replaces 3-part EMA check)
- `adx = ta.dmi(14, 14)` (Wilder ADX/DI, length 14, hardcoded — standard default,
  not exposed as an input)
- `regimeOk = adx > 20 and plusDI > minusDI`
- Rationale: a single, well-established trend-strength/direction test replaces the
  current `ema200 rising` + `close > ema200` + `ema50 > ema200` triple check. Research
  (see conversation) shows ADX(14) > 20-25 is the standard threshold for "trending vs
  chop"; 20 is used here as the more permissive standard bound so trade frequency
  doesn't collapse further than the current strategy's already-low signal rate.

### 2. Trend/pullback reference (replaces EMA20/50/200 stack)
- `kama = ta.kama(close, erPeriod, 2, 30)` — Kaufman's Adaptive Moving Average.
  `erPeriod` is the only exposed length input (default 10 — the standard Kaufman
  default). The fast (2) and slow (30) smoothing constants are hardcoded textbook
  constants; they very rarely need retuning per symbol per the research reviewed.
- KAMA replaces both the EMA20 "pullback level" and (combined with the ADX regime
  gate) the EMA50/EMA200 role — a single adaptive line does the job of three fixed
  ones because it inherently slows down in choppy stretches and speeds up in clean
  trends.

### 3. Entry trigger
- `pulledBack = low <= kama and close > kama - trailBufferAtr` where
  `trailBufferAtr = atr * 0.5` — pullback must not undercut KAMA by more than half an
  ATR, keeping the "shallow rest" character from the original plan without a second
  fixed EMA (EMA50) as the floor.
- `rsi = ta.rsi(close, 14)`, `rsiSignal = ta.sma(rsi, 9)`
- `rsiResume = ta.crossover(rsi, rsiSignal)` — replaces the fixed "RSI crosses above
  45" rule. Using RSI's own moving average as the trigger line makes the threshold
  self-adjusting to the symbol's own momentum regime instead of a single hardcoded
  number.
- `longSignal = regimeOk and pulledBack and rsiResume`
- Order placed with `process_orders_on_close = false` (default) so the fill happens
  at the next bar's open — this corrects the process_orders_on_close=true
  inconsistency flagged in the current `strategy.pine` review.

### 4. Exit logic (unchanged mechanics from current strategy.pine)
- Initial stop: `entryPrice - 1.5 * atr` (ATR at entry, `atrLen = 14` hardcoded)
- Chandelier trail: `highestHighSinceEntry - 3.0 * atr`, activates once trade is
  `>= 1.0 * atr` in profit, ratchets up only
- Trend-break exit: close below KAMA (replaces close below EMA50)

### 5. Position sizing
- `riskPct` input (default 1.0%), the only account-specific input that can't be
  hardcoded: `qty = (strategy.equity * riskPct/100) / (1.5 * atr)`
- Replaces the current `default_qty_type = strategy.percent_of_equity,
  default_qty_value = 100` (which doesn't account for stop distance at all).

## Exposed inputs (target: 2)
1. `erPeriod` (KAMA efficiency-ratio period, default 10)
2. `riskPct` (account risk per trade, default 1.0%)

Everything else (ADX length/threshold, KAMA fast/slow constants, RSI length, ATR
length, stop/trail multipliers, trail activation threshold) is hardcoded to the
defaults derived from the research above.

## Data flow
Bar close → compute ADX/KAMA/RSI/ATR (all confirmed, no repainting) → evaluate
regime + pullback + RSI-resume on bar close → `strategy.entry` submitted, fills at
next bar open → on each bar while in position, update stop/trail/trend-break exit →
`strategy.exit`/`strategy.close` evaluated every bar.

## Edge cases
- First `erPeriod`/ATR-length bars: KAMA/ATR are `na` — `regimeOk`/`pulledBack` will
  naturally evaluate false via Pine's `na` propagation, so no explicit guard is
  needed beyond what the current code already relies on.
- Only one position open at a time (`strategy.entry` on top of an existing "Long" is
  a no-op / pyramiding disabled by default) — matches current behavior, no change.

## Testing plan
- Load on 3-5 real sub-$50 optionable tickers on daily chart in TradingView Strategy
  Tester.
- Compare trade count, win rate, and profit factor against the current
  `strategy.pine` baseline and against Approach B's results.

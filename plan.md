# Trend Strategy Plan (v1)

## Goal
Trend-following strategy for optionable stocks under $50, targeting:
- Win rate > 70%
- Profit factor >= 2.0 (all trades)

## Scope decisions
- **Universe:** No in-script price filter. User will manually load only sub-$50 optionable tickers onto the chart when backtesting/running the strategy (Pine strategies operate on one symbol at a time).
- **Timeframe:** Daily chart, swing trades held days to a few weeks.
- **Direction:** Long only (calls). No shorts.
- **Style:** Pullback-in-trend entries (buy strength after a shallow rest in an established uptrend), not breakout or MA-crossover.

## 1. Regime filter (trade permission)
Only allow long entries when all of:
- EMA 200 is rising: `ema200 > ema200[20]`
- Price is above EMA 200
- EMA 50 is above EMA 200

This confirms both long-term and intermediate trend are aligned and pointed up.

## 2. Entry trigger (pullback + resume)
- Price pulls back to touch or dip slightly below EMA 20, while staying above EMA 50
- RSI(14) drops under 45 during the pullback, then crosses back above 45
- Entry on next bar's open after the RSI cross-up (avoids repainting/lookahead)

## 3. Exit logic
- **Initial stop:** entry price - 1.5 x ATR(14) (ATR measured at entry)
- **Trailing stop:** once trade is up >= 1x ATR in profit, switch to Chandelier-style trail:
  `highest high since entry - 3 x ATR(14)`, recalculated each bar, only moves up
- **Trend-break exit:** close below EMA 50 exits regardless of trailing stop
- No fixed profit target - let winners run, since PF depends on winner size more than win rate

## 4. Expected outcome (honest calibration)
Hitting both >70% win rate AND PF >= 2.0 simultaneously is a high bar - the two metrics
typically trade off against each other. Strategies that achieve both usually do it by
trading rarely and being highly selective.

Realistic expectation for this v1, backtested on a handful of real sub-$50 optionable
trending names on daily charts:
- Win rate: likely 55-65% out of the box
- Profit factor: likely 1.3-1.8 out of the box
- Trade frequency: low, ~3-8 signals/year per stock (strict regime + pullback + RSI reset)

We do not expect to hit 70%/2.0 on the first pass. Plan is to build this basic version,
backtest it in TradingView's Strategy Tester across real sub-$50 optionable tickers,
review the actual trade list, and iteratively tighten the rules (candidates for later
iterations: ADX trend-strength filter, relative-volume confirmation on the resume bar,
minimum pullback depth, sector/market relative-strength filter) until the gap closes.

## Implementation notes for v1
- Pine Script v6 strategy
- Expose inputs for: EMA lengths (20/50/200), RSI length/threshold, ATR length,
  stop multiplier (1.5x), trail multiplier (3x), trail activation threshold (1x ATR)
- Keep it basic per user request - no position sizing / risk-% logic yet, no multi-symbol
  scanning yet. Both are candidates for future iterations, not v1.

# Volatility Exhaustion Strategy — Improvement Design

Date: 2026-07-14
Status: Approved direction — keep core, tune around it, balanced objective

## Background

Strategy D ("Hardened Exhaustion Engine", `strategy_d_volatility_exhaustion.pine`, mirrored in `p.py`)
is a long-only daily mean-reversion system: enter when close > 150 SMA (macro trend up),
close < lower Bollinger band (20, 1.75), and SMA-RSI(14) <= 40; take profit at the entry-bar
mid-band (frozen `bbBasis`); stop out on a close below the 150 SMA. 5% of equity per trade,
0.1% commission, orders fill on bar close. Python mirror is validated trade-for-trade against
TradingView (AXGN, 57 trades, 1986–2026).

Diagnosis from validated runs:

- Winners are steady +5–10% reversions (win rates 40–80% across the 10-ticker universe).
- Losses are structurally larger: the trend-break stop waits for a close below the 150 SMA,
  which from an exhaustion entry can be −20 to −40% (AXGN: −39.3%, −30.6%, −23.3% trades).
- 5% allocation makes even good years financially negligible (0.3–3% total return 2020–2026).

## Objective

Balanced improvement: better profit factor and worst-trade tail first, then larger allocation
for meaningful absolute returns. Core identity (trend filter + band exhaustion + RSI, long-only,
daily) is preserved.

## Approach

### Levers (each individually toggleable)

1. **ATR hard stop** — exit at `entry − k·ATR(14)` (Wilder/RMA ATR, matching Pine `ta.atr`).
   Grid: k ∈ {2, 3, 4}. Caps the −20/−40% collapses at a bounded loss.
2. **Time stop** — exit at bar close if the mid-band target is not reached within N bars.
   Grid: N ∈ {10, 15, 20}. Kills dead reversion theses and frees capital.
3. **SMA-distance entry filter** — require `close ≥ macroSma + d·ATR(14)` at entry.
   Grid: d ∈ {0.5, 1.0}. Skips exhaustion entries sitting on top of the macro floor,
   which are the ones that break through it.
4. **Allocation** — raised from 5% only in the final step, after the loss tail is capped.
   Candidates: 10%, 15%, 25%.

### Validation protocol

- Universe: current 10 tickers (NVTS, MX, SKYT, MRAM, ATOM, LAR, ALOY, AXGN, UFO, NEBX),
  daily bars from Yahoo, `auto_adjust=False`. Universe expansion is a later phase.
- **Train**: entries 2020-01-01 → 2023-12-31. **Test**: entries 2024-01-01 → 2026-06-01.
  Each slice gets a 150-bar warm-up lead-in so indicator values are identical to a
  continuous run.
- Step 1: baseline both slices. Step 2: sweep each lever alone on train. Step 3: combine
  survivors; confirm on test. A change ships only if it improves train AND does not
  degrade test.
- Metrics, aggregated across all tickers (no per-ticker cherry-picking): profit factor,
  total PnL, win rate, trade count, worst single trade %.

### Components

- `experiment.py` (new) — parameterized engine (`atr_stop_mult`, `time_stop_bars`,
  `min_sma_dist_atr`, `alloc`) + data loader with train/test slicing + sweep runner
  printing one results row per config. `p.py` stays the untouched TradingView mirror
  until a winner is chosen.
- Winning config is then applied to **both** `p.py` and
  `strategy_d_volatility_exhaustion.pine`, with the levers exposed as `input.*`
  parameters in Pine (settable to "off").

### Fill semantics (must match Pine exactly, as validated)

- Entry and time-stop/trend-stop exits fill at bar close (`trade_on_close=True` ≡
  `process_orders_on_close=true`).
- TP is a resting limit at the frozen mid-band (`tp=` ≡ `strategy.exit(limit=)`).
- ATR stop is a resting stop order (`sl=` ≡ `strategy.exit(stop=)`), filled at the stop
  price (or gap open).

## Risks

- **Overfitting**: 10 tickers × ~5–13 trades is thin; the train/test gate and
  aggregate-only judging mitigate but don't eliminate it. Phase 2 (wider universe)
  is the real check.
- **Data feed**: Yahoo vs TradingView disagree pre-2010 on small-caps; all tuning is
  2020+ where the feeds were verified to match to the penny.

## Success criteria

- Aggregate profit factor and worst-trade % improve on train and hold on test.
- Absolute return meaningfully above baseline at the chosen allocation without the
  max-drawdown character changing class.
- Final Pine script still validates trade-for-trade against the Python mirror.

# Crypto Multi-Asset Technical Signal Engine

## Technical & Architecture Specification --- V1

**Status:** Draft / Implementation Specification\
**Primary goal:** Build a deterministic crypto scanning and
trading-signal engine that evaluates a large pool of crypto tickers,
including large caps, mid caps, low caps, and memes, and produces
reproducible entry/exit signals that can be implemented equivalently in
TradingView Pine Script.

------------------------------------------------------------------------

## 1. Product Definition

The system is a **rule-based quantitative signal engine**, not an AI
prediction system.

Given a universe of crypto assets and OHLCV/market-data inputs, the
system:

1.  Normalizes market data.
2.  Calculates technical and market-context features.
3.  Classifies market regime.
4.  Detects candidate setups.
5.  Scores setup quality.
6.  Applies tradability/risk filters.
7.  Generates deterministic entry, stop, target, and exit signals.
8.  Records every signal and trade state transition.
9.  Backtests the exact same rules without lookahead bias.
10. Exposes the strategy in a form that can be reproduced in TradingView
    Pine Script.

### Core design principle

> **Strategy specification is the source of truth.**

Python/TypeScript implementation, backtester, live scanner, API, UI, and
Pine Script should all implement the same strategy specification.

``` text
                    Strategy Specification
                             |
              +--------------+--------------+
              |              |              |
          Backtester      Live Engine    Pine Script
              |              |              |
              +--------------+--------------+
                             |
                     Identical Signals
```

------------------------------------------------------------------------

# 2. Goals

## 2.1 Primary goals

-   Scan a configurable crypto universe.
-   Support large-, medium-, low-cap and meme assets.
-   Detect long and short setups.
-   Use normalized features instead of absolute thresholds where
    possible.
-   Detect momentum, breakout, continuation, and liquidity-sweep setups.
-   Incorporate BTC/ETH/market context.
-   Account for liquidity and execution quality.
-   Produce explainable signals.
-   Avoid lookahead bias.
-   Produce deterministic backtest results.
-   Generate signals that can be reproduced in Pine Script.
-   Support historical replay and walk-forward validation.
-   Store enough feature data to explain every trade.

## 2.2 Non-goals for V1

-   Machine-learning price prediction.
-   Automatic portfolio optimization.
-   Fully autonomous exchange execution.
-   Order-book-dependent alpha as a hard requirement.
-   Social sentiment as a required signal.
-   Fundamental token analysis.
-   On-chain analytics as a required signal.

These can be added later without changing the core architecture.

------------------------------------------------------------------------

# 3. Design Principles

## 3.1 Deterministic

Given identical:

-   market data
-   configuration
-   strategy version
-   timestamps

the engine must produce identical:

-   features
-   signals
-   entries
-   exits
-   PnL

## 3.2 No lookahead

At candle `N`, the engine may only use information available at or
before candle `N`.

Never use:

-   future candles
-   future volume
-   future highs/lows
-   unconfirmed pivots as if they were known
-   future market-cap values
-   revised data that was not available at execution time

## 3.3 Explainable

Every signal must answer:

-   Why did the signal trigger?
-   Which features contributed?
-   Which conditions failed?
-   What invalidates the trade?
-   What are the entry/exit assumptions?

## 3.4 Normalized

Avoid absolute rules such as:

``` text
volume > $10M
```

Prefer:

``` text
current_volume / rolling_average_volume > 2
```

Avoid:

``` text
price moved > 5%
```

Prefer:

``` text
return > percentile_threshold
```

Avoid:

``` text
stop = 3%
```

Prefer:

``` text
stop = structure_level ± ATR_multiple
```

This makes the strategy more portable across BTC, midcaps, low caps, and
memes.

## 3.5 Strategy and execution are separate

A chart can produce a strong technical setup while the asset is
impractical to trade.

Therefore:

``` text
Setup Quality != Execution Quality
```

------------------------------------------------------------------------

# 4. High-Level Architecture

``` text
                       DATA SOURCES
                            |
              +-------------+-------------+
              |             |             |
            OHLCV       Market Data    Reference Data
              |             |             |
              +-------------+-------------+
                            |
                            v
                    Data Normalization
                            |
                            v
                    Candle Store / Cache
                            |
                            v
                    Feature Engine
                            |
            +---------------+----------------+
            |               |                |
            v               v                v
       Market Regime   Asset Features   Liquidity Features
            |               |                |
            +---------------+----------------+
                            |
                            v
                    Setup Detection
                            |
                            v
                    Signal Evaluation
                            |
                    +-------+-------+
                    |               |
                    v               v
              Risk Engine     Execution Filter
                    |               |
                    +-------+-------+
                            |
                            v
                     Signal Engine
                            |
             +--------------+---------------+
             |              |               |
             v              v               v
          Scanner        Backtester       API/UI
             |              |               |
             +--------------+---------------+
                            |
                            v
                     Trade Analytics
```

------------------------------------------------------------------------

# 5. Recommended Technology Stack

A practical V1 stack:

## Backend

-   **Python**
-   FastAPI
-   Pandas/Polars for research and batch computation
-   NumPy
-   Pydantic
-   SQLAlchemy
-   PostgreSQL
-   Redis
-   Celery/RQ or an async job system for batch jobs

Python is preferred for the quantitative engine because the ecosystem
for time-series analysis, research, and backtesting is stronger.

## Frontend

-   React
-   TypeScript
-   Vite or Next.js
-   TradingView Lightweight Charts or equivalent charting library

## Infrastructure

-   Docker
-   PostgreSQL
-   Redis
-   Object storage for historical datasets
-   Optional Kubernetes later

## TradingView

-   Pine Script v6
-   Generated manually from the strategy specification initially
-   Automated code generation later if useful

------------------------------------------------------------------------

# 6. Domain Model

Core entities:

``` text
Asset
Market
Candle
FeatureSnapshot
MarketRegime
Setup
Signal
Position
Trade
StrategyVersion
BacktestRun
BacktestMetric
```

------------------------------------------------------------------------

# 7. Asset Model

``` python
class Asset:
    symbol: str
    exchange: str
    base_asset: str
    quote_asset: str

    market_cap: float | None
    circulating_supply: float | None

    category: str | None
    # large_cap
    # mid_cap
    # low_cap
    # meme
    # unknown

    listing_timestamp: datetime | None
    active: bool
```

Market-cap classification should not directly determine whether an asset
can generate a signal.

It should primarily influence:

-   position sizing
-   liquidity requirements
-   execution risk
-   slippage assumptions

------------------------------------------------------------------------

# 8. Market Data Model

``` python
class Candle:
    symbol: str
    exchange: str
    timeframe: str

    timestamp: datetime

    open: float
    high: float
    low: float
    close: float

    volume: float

    quote_volume: float | None

    trades: int | None
```

Use UTC internally.

Never mix local timestamps into strategy calculations.

------------------------------------------------------------------------

# 9. Timeframes

Recommended initial architecture:

``` text
Market Context: 4H / 1D
Setup:           1H
Execution:       15M
```

Optional:

``` text
Fast scanner:    5M
```

The strategy should not require every timeframe to exist simultaneously
in the same process.

Higher timeframe data can be precomputed and joined onto the execution
timeframe.

------------------------------------------------------------------------

# 10. Multi-Timeframe Data Handling

For an execution candle at `15:45`, the system must use the latest
**confirmed** 1H and 4H candle.

Example:

``` text
15:45 execution candle

1H:
15:00-15:59 candle is NOT confirmed yet

Use:
14:00-14:59 candle

4H:
12:00-15:59 candle is NOT confirmed yet

Use:
08:00-11:59 candle
```

This is essential for Pine compatibility and backtest correctness.

------------------------------------------------------------------------

# 11. Feature Engine

The feature engine calculates normalized, reusable observations.

## 11.1 Trend features

``` text
EMA20
EMA50
EMA200

EMA20 slope
EMA50 slope

price_vs_ema20
price_vs_ema50
price_vs_ema200

ema50_vs_ema200
```

Example:

``` python
price_vs_ema200 = (close - ema200) / ema200
```

------------------------------------------------------------------------

# 12. Volatility Features

Use ATR and normalized ATR.

``` text
ATR(14)
ATR%
ATR percentile
ATR expansion
ATR compression
```

Definitions:

``` python
atr_pct = atr / close

atr_expansion =
    atr_pct > rolling_mean(atr_pct, 20)
```

Prefer percentile-based thresholds where possible.

Example:

``` text
atr_percentile > 75
```

rather than:

``` text
ATR > 2%
```

------------------------------------------------------------------------

# 13. Volume Features

## 13.1 Relative volume

``` python
rvol_20 = volume / SMA(volume, 20)
```

Additional:

``` text
RVOL 20
RVOL 50
volume percentile
volume acceleration
quote-volume acceleration
```

## 13.2 Volume expansion

Example:

``` python
volume_expansion =
    rvol_20 >= 2.0
```

The exact threshold must be tested rather than assumed optimal.

------------------------------------------------------------------------

# 14. Momentum Features

Use:

``` text
ROC 15M
ROC 1H
ROC 4H
RSI 14
momentum percentile
```

Example:

``` python
roc_1h = close / close.shift(4) - 1
```

RSI should be treated as a momentum feature rather than an automatic
overbought/oversold trigger.

Avoid:

``` text
RSI < 30 => BUY
```

------------------------------------------------------------------------

# 15. Relative Strength

Relative strength is a core feature for a multi-asset scanner.

Calculate:

``` text
asset return - BTC return
asset return - ETH return
asset return - market benchmark return
```

For example:

``` python
rs_btc_1h = asset_return_1h - btc_return_1h
rs_btc_4h = asset_return_4h - btc_return_4h
```

Also calculate percentile rank across the current universe:

``` text
RS percentile
```

Example:

``` text
Asset X:
1H RS percentile = 94
4H RS percentile = 88
```

This means the asset is outperforming most assets in the scanned
universe.

------------------------------------------------------------------------

# 16. Market Context

The engine should maintain market-level features.

Minimum:

``` text
BTC trend
BTC volatility
BTC 1H return
BTC 4H return
ETH trend
ETH 1H return
ETH 4H return
```

Optional:

``` text
market breadth
percentage of assets above EMA20
percentage of assets with positive 1H return
percentage of assets with positive 4H return
average market RVOL
```

Market context should normally act as:

-   confirmation
-   risk modifier
-   position-sizing modifier

rather than an absolute requirement.

A strong altcoin can outperform during a weak market.

------------------------------------------------------------------------

# 17. Structure Engine

The structure engine detects:

-   swing highs
-   swing lows
-   break of structure
-   range highs/lows
-   local support/resistance
-   breakout
-   retest
-   higher highs
-   higher lows
-   lower highs
-   lower lows

## Important implementation rule

A swing point is only available after its confirmation criteria have
been satisfied.

For example, if using a pivot requiring `N` right-side candles:

``` text
Pivot at candle N

Known only after:

N + right_bars
```

The backtester must record the pivot's actual availability time.

------------------------------------------------------------------------

# 18. Liquidity Engine

Detect likely liquidity pools around:

``` text
equal highs
equal lows
recent swing highs
recent swing lows
range highs
range lows
previous day high
previous day low
previous week high
previous week low
```

## Liquidity sweep

Bullish sweep:

``` text
low < previous liquidity level
AND
close > liquidity level
```

Bearish sweep:

``` text
high > previous liquidity level
AND
close < liquidity level
```

The definition must be deterministic.

Avoid subjective concepts such as:

> "Looks like smart money entered."

------------------------------------------------------------------------

# 19. Setup Engines

V1 should support multiple setup types.

## 19.1 Breakout

Conditions:

``` text
range exists
AND
close > range_high
AND
RVOL >= threshold
AND
relative_strength >= threshold
AND
volatility is expanding
```

Short:

``` text
close < range_low
AND
RVOL >= threshold
AND
relative_strength bearish
AND
volatility expansion
```

------------------------------------------------------------------------

# 20. Momentum Continuation

Long:

``` text
strong positive impulse
AND
high relative volume
AND
higher high
AND
pullback
AND
higher low
AND
relative strength remains positive
```

This setup is particularly useful for low- and medium-cap assets already
attracting abnormal volume.

------------------------------------------------------------------------

# 21. Liquidity Sweep Reversal

Long:

``` text
sell-side liquidity identified
AND
low sweeps liquidity
AND
close reclaims liquidity
AND
bullish structure confirmation
```

Short:

``` text
buy-side liquidity identified
AND
high sweeps liquidity
AND
close loses liquidity
AND
bearish structure confirmation
```

------------------------------------------------------------------------

# 22. Range Breakout

Detect:

``` text
consolidation
+
repeated range interaction
+
decreasing volatility
+
breakout
+
volume expansion
```

Then optionally require a retest before entry.

------------------------------------------------------------------------

# 23. Setup Scoring

Do not collapse all logic into one opaque score.

Store individual feature contributions.

Example:

``` python
setup_score = (
    trend_score
    + structure_score
    + volume_score
    + momentum_score
    + relative_strength_score
    + volatility_score
)
```

Example component model:

``` text
Bullish trend              +2
Bullish BOS                +2
Liquidity sweep            +2
RVOL > threshold           +2
Relative strength          +2
Volatility expansion       +1
Momentum confirmation      +1
Immediate resistance       -2
Weak liquidity             -2
```

The exact weights must be empirically validated.

Do not assume a score of 8 means an 80% probability of success.

------------------------------------------------------------------------

# 24. Setup Score vs Execution Score

Maintain two independent scores.

## Setup score

Measures technical alignment.

``` text
structure
momentum
volume
relative strength
volatility
market context
```

## Execution score

Measures tradability.

``` text
liquidity
spread
slippage
market depth
market-cap
quote volume
```

Example:

``` json
{
  "setup_score": 9,
  "execution_score": 6,
  "setup_type": "MOMENTUM_CONTINUATION"
}
```

------------------------------------------------------------------------

# 25. Liquidity / Execution Gate

The engine should calculate:

``` text
24h quote volume
volume / market cap
spread
estimated slippage
available depth
```

If order-book data is unavailable, use conservative proxy metrics.

Example:

``` python
liquidity_ratio = quote_volume_24h / market_cap
```

This is only a proxy and should not be treated as actual executable
liquidity.

------------------------------------------------------------------------

# 26. Meme / Low-Cap Handling

Do not automatically reject low-cap assets.

Instead:

``` text
Technical setup
       |
       v
Liquidity assessment
       |
       +---- acceptable --> signal
       |
       +---- poor -------> signal blocked or reduced risk
```

Market-cap can modify:

-   maximum position size
-   minimum volume requirement
-   maximum acceptable spread
-   maximum slippage
-   stop distance
-   confidence in backtest execution

The same technical setup should not imply the same position size across
BTC and a \$3M meme.

------------------------------------------------------------------------

# 27. Entry Model

V1 should support two entry modes.

## Close confirmation

Signal is generated at candle close.

Execution assumption:

``` text
entry_price = next_candle_open
```

This is the safest backtest assumption if the signal cannot execute at
the exact closing price.

## Breakout entry

An optional intrabar model can trigger when price crosses a defined
level.

If used, the backtester must model:

-   whether the level was touched
-   order priority
-   spread
-   slippage
-   candle ambiguity

Do not mix close-based and intrabar assumptions without explicitly
labeling them.

------------------------------------------------------------------------

# 28. Stop-Loss Model

Prefer structural stops.

Example long:

``` text
stop = sweep_low - ATR * buffer
```

or:

``` text
stop = recent_swing_low - ATR * buffer
```

The stop should represent **thesis invalidation**, not an arbitrary
percentage.

------------------------------------------------------------------------

# 29. Take-Profit Model

Targets can be based on:

``` text
previous high
liquidity pool
range high
ATR multiple
risk multiple
```

Example:

``` text
TP1 = 1R
TP2 = 2R
TP3 = next major liquidity
```

A strategy configuration should determine whether the position:

-   exits entirely
-   scales out
-   trails remaining quantity

------------------------------------------------------------------------

# 30. Position Management

Example V1:

``` text
Entry
 |
 +--> TP1 at 1R
 |      |
 |      +--> close 25-50%
 |      +--> move stop toward breakeven
 |
 +--> TP2 at 2R
 |      |
 |      +--> close additional portion
 |
 +--> remaining position
        |
        +--> ATR/swing trailing stop
```

The exact percentages should be configuration parameters and tested
independently.

------------------------------------------------------------------------

# 31. Trade State Machine

Use an explicit state machine.

``` text
FLAT
 |
 v
SIGNAL_DETECTED
 |
 v
ENTRY_PENDING
 |
 v
OPEN
 |
 +----> TP1
 |        |
 |        v
 |     MANAGING
 |
 +----> STOP
 |
 +----> INVALIDATED
 |
 v
CLOSED
```

Every transition must be persisted.

------------------------------------------------------------------------

# 32. Trade Object

``` python
class Trade:
    id: UUID

    strategy_version: str

    symbol: str
    exchange: str

    timeframe: str
    setup_type: str

    side: Literal["LONG", "SHORT"]

    signal_time: datetime

    entry_time: datetime | None
    entry_price: float | None

    stop_price: float

    tp1_price: float | None
    tp2_price: float | None
    tp3_price: float | None

    exit_time: datetime | None
    exit_price: float | None

    exit_reason: str | None

    quantity: float

    pnl_absolute: float | None
    pnl_percent: float | None
    pnl_r: float | None

    setup_score: float
    execution_score: float

    feature_snapshot_id: UUID
```

------------------------------------------------------------------------

# 33. Feature Snapshot

Every signal should store the exact inputs used.

``` python
class FeatureSnapshot:
    symbol: str
    timestamp: datetime

    close: float

    ema20: float
    ema50: float
    ema200: float

    atr: float
    atr_pct: float

    rsi: float

    rvol20: float

    roc_1h: float
    roc_4h: float

    rs_btc_1h: float
    rs_btc_4h: float

    swing_high: float | None
    swing_low: float | None

    bullish_bos: bool
    bearish_bos: bool

    sell_side_sweep: bool
    buy_side_sweep: bool

    market_regime: str
```

This makes every signal auditable.

------------------------------------------------------------------------

# 34. Strategy Configuration

Do not hardcode parameters.

Example:

``` yaml
strategy_version: "1.0.0"

timeframes:
  market: "4h"
  setup: "1h"
  execution: "15m"

trend:
  ema_fast: 20
  ema_medium: 50
  ema_slow: 200

volume:
  rvol_period: 20
  minimum_rvol: 2.0

volatility:
  atr_period: 14
  expansion_percentile: 75

structure:
  breakout_lookback: 20
  swing_left: 3
  swing_right: 3

relative_strength:
  lookback_1h: 4
  lookback_4h: 4
  minimum_percentile: 70

risk:
  stop_atr_buffer: 0.25
  tp1_r: 1.0
  tp2_r: 2.0

filters:
  minimum_setup_score: 7
  minimum_execution_score: 5
```

Every backtest must store the complete configuration.

------------------------------------------------------------------------

# 35. Strategy Versioning

Every signal must contain:

``` text
strategy_name
strategy_version
configuration_hash
feature_version
```

Example:

``` text
strategy = crypto_momentum_liquidity
version = 1.2.0
config_hash = abc123
feature_version = 2.0
```

Never compare results from different strategy versions without recording
the difference.

------------------------------------------------------------------------

# 36. Backtesting Engine

The backtester should operate sequentially.

``` python
for candle in chronological_candles:

    update_confirmed_features(candle)

    update_market_context()

    detect_setups()

    evaluate_signals()

    update_open_positions()

    process_stops_and_targets()

    persist_state()
```

No future dataset access should influence the current decision.

------------------------------------------------------------------------

# 37. Intrabar Ambiguity

Suppose a candle has:

``` text
Open = 100
High = 110
Low = 90
Close = 105
```

and both:

``` text
Stop = 95
TP = 108
```

were active.

OHLC alone does not tell you whether:

``` text
TP happened first
```

or:

``` text
STOP happened first
```

The backtester must choose and document a conservative policy.

Possible options:

1.  Lower-timeframe replay.
2.  Conservative assumption.
3.  Exchange tick data.
4.  Explicit ambiguous-trade classification.

V1 should preferably use lower-timeframe replay when available.

------------------------------------------------------------------------

# 38. Transaction Costs

Backtests must model:

``` text
maker/taker fees
spread
slippage
funding
```

For perpetual futures:

``` text
funding payments
```

must be included when holding across funding intervals.

Do not evaluate a strategy solely on gross PnL.

------------------------------------------------------------------------

# 39. Backtest Metrics

Minimum:

``` text
Total trades
Win rate
Loss rate
Gross profit
Gross loss
Net PnL
Profit factor
Expectancy
Average R
Median R
Max drawdown
Average drawdown
Longest losing streak
Average trade duration
```

Also:

``` text
Sharpe
Sortino
Calmar
```

when statistically appropriate.

------------------------------------------------------------------------

# 40. Crypto-Specific Metrics

Track:

``` text
performance by market cap
performance by setup type
performance by market regime
performance by volatility regime
performance by symbol
performance by month
performance by day of week
performance by holding duration
```

Especially important:

``` text
large caps
mid caps
low caps
memes
```

A strategy that performs well only in one category should make that
visible.

------------------------------------------------------------------------

# 41. Avoid Survivorship Bias

The historical universe must include assets that:

-   existed at the time
-   later became inactive
-   were delisted
-   failed
-   migrated
-   were renamed

Do not backtest only today's surviving tickers.

Otherwise the results can be materially biased.

------------------------------------------------------------------------

# 42. Avoid Selection Bias

Do not manually choose the coins that produced good results.

The scanner should operate against a defined historical universe.

Example:

``` text
At timestamp T:

Universe = all eligible assets known at T

Run scanner

Record candidates

Do not add assets using information from T + 1
```

------------------------------------------------------------------------

# 43. Walk-Forward Testing

Use:

``` text
Training / calibration period
        |
        v
Validation period
        |
        v
Out-of-sample period
```

Example:

``` text
6 months calibration
2 months validation
2 months out-of-sample
```

Then roll forward.

Avoid optimizing parameters against the entire historical dataset.

------------------------------------------------------------------------

# 44. Parameter Optimization

Do not optimize every parameter simultaneously.

Test logical groups:

### Group A

Structure

### Group B

Volume

### Group C

Relative strength

### Group D

Risk management

### Group E

Execution filters

Use parameter stability rather than selecting a single best historical
value.

A strategy that only works at:

``` text
RVOL = 2.137
```

but fails at:

``` text
RVOL = 2.0
RVOL = 2.25
```

is a warning sign for overfitting.

------------------------------------------------------------------------

# 45. Data Leakage Controls

Create explicit safeguards.

``` python
assert feature_timestamp <= decision_timestamp
```

For every feature.

Also validate:

``` text
No future joins
No future market-cap values
No future universe membership
No future pivot confirmation
No future normalization statistics
```

Rolling statistics must use:

``` python
rolling(window).mean()
```

rather than full-dataset means.

------------------------------------------------------------------------

# 46. Universe Scanner

The scanner should process assets in stages.

## Stage 1 --- Cheap filter

``` text
minimum data quality
minimum quote volume
active market
valid candles
```

## Stage 2 --- Feature computation

Calculate:

``` text
momentum
RVOL
ATR
relative strength
structure
```

## Stage 3 --- Candidate detection

Find:

``` text
breakouts
sweeps
momentum continuation
range breaks
```

## Stage 4 --- expensive filters

Calculate/use:

``` text
liquidity
spread
order book depth
slippage
```

## Stage 5 --- signal generation

Return top candidates without ranking them as an investment
recommendation.

------------------------------------------------------------------------

# 47. API Design

Example:

``` http
GET /api/v1/scanner/signals
```

Parameters:

``` text
timeframe=15m
side=long
setup=all
min_setup_score=7
min_execution_score=5
```

Response:

``` json
{
  "strategy_version": "1.0.0",
  "timestamp": "...",
  "signals": [
    {
      "symbol": "XYZUSDT",
      "side": "LONG",
      "setup": "MOMENTUM_CONTINUATION",
      "entry": 1.234,
      "stop": 1.180,
      "tp1": 1.342,
      "tp2": 1.450,
      "setup_score": 9,
      "execution_score": 7
    }
  ]
}
```

------------------------------------------------------------------------

# 48. Scanner UI

Recommended columns:

``` text
Symbol
Price
24H %
Market Cap
24H Volume
RVOL
RS vs BTC
ATR %
Setup
Setup Score
Execution Score
Entry
Stop
TP1
TP2
R:R
```

Add filters:

``` text
Market cap
Volume
RVOL
Setup type
Long / Short
Market regime
Minimum score
```

------------------------------------------------------------------------

# 49. Signal Detail UI

Clicking a signal should show:

``` text
SYMBOL
SETUP TYPE
LONG / SHORT

ENTRY
STOP
TP1
TP2

RISK/REWARD

WHY THE SIGNAL TRIGGERED
------------------------
✓ Relative strength
✓ Volume expansion
✓ Structure breakout
✓ Volatility expansion
✓ Market context

RISKS
------------------------
⚠ Low liquidity
⚠ High ATR
⚠ Nearby resistance
```

Also display the chart with:

-   entry
-   stop
-   targets
-   liquidity level
-   breakout level
-   relevant EMA
-   signal timestamp

------------------------------------------------------------------------

# 50. Signal Lifecycle

``` text
DETECTED
    |
    v
CONFIRMED
    |
    v
ENTRY
    |
    v
OPEN
    |
    +--> TP1
    |
    +--> TP2
    |
    +--> STOP
    |
    +--> INVALIDATED
    |
    v
CLOSED
```

Never mutate historical signals silently.

If a signal changes because the strategy changes, create a new strategy
version.

------------------------------------------------------------------------

# 51. Database Schema

Recommended PostgreSQL tables:

``` text
assets
markets
candles
feature_snapshots
market_context
setups
signals
positions
trades
strategy_versions
backtest_runs
backtest_trades
backtest_metrics
```

Important indexes:

``` text
candles(symbol, timeframe, timestamp)
feature_snapshots(symbol, timeframe, timestamp)
signals(timestamp)
signals(symbol, timestamp)
trades(symbol, entry_time)
```

For very large candle history, consider TimescaleDB or partitioning by
timeframe/date.

------------------------------------------------------------------------

# 52. Caching

Redis can cache:

``` text
latest candles
latest features
latest market context
active signals
active positions
scanner results
```

Historical truth should remain in PostgreSQL/object storage.

Redis should not be the authoritative historical data store.

------------------------------------------------------------------------

# 53. Event-Driven Live Architecture

For live scanning:

``` text
Exchange/WebSocket
       |
       v
Market Data Adapter
       |
       v
Candle Builder
       |
       v
Feature Engine
       |
       v
Signal Engine
       |
       +----> PostgreSQL
       |
       +----> Redis
       |
       +----> WebSocket API
       |
       v
Frontend
```

Only process a candle as "confirmed" when the exchange/provider confirms
its close.

------------------------------------------------------------------------

# 54. Data Provider Abstraction

Do not couple the strategy to one exchange.

``` python
class MarketDataProvider(Protocol):

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime
    ) -> list[Candle]:
        ...

    def get_market_metadata(self, symbol: str):
        ...

    def get_order_book(self, symbol: str):
        ...
```

Implement adapters:

``` text
Binance
Coinbase
Bybit
OKX
...
```

The strategy sees normalized data only.

------------------------------------------------------------------------

# 55. Pine Script Compatibility

Every strategy feature should have a Pine-compatible definition.

Maintain a mapping document:

  Engine Feature   Pine Equivalent
  ---------------- ----------------------
  EMA              ta.ema
  ATR              ta.atr
  RSI              ta.rsi
  SMA volume       ta.sma(volume, n)
  Highest high     ta.highest
  Lowest low       ta.lowest
  ROC              custom arithmetic
  Market return    request.security
  HTF data         request.security
  Structure        custom state machine

Do not use backend-only features in the core strategy unless a Pine
equivalent exists.

------------------------------------------------------------------------

# 56. Pine Multi-Timeframe Rules

Use confirmed higher-timeframe values.

Conceptually:

``` text
request.security(...)
```

must be configured to avoid future leakage/repainting.

The backend and Pine implementation must agree on:

-   candle close timing
-   timezone
-   confirmation
-   gap handling
-   missing candles

------------------------------------------------------------------------

# 57. Reconciliation Testing

For a fixed historical period:

``` text
Backend strategy
        |
        v
Expected signal timestamps

TradingView Pine strategy
        |
        v
Actual signal timestamps
```

Compare:

``` text
symbol
timestamp
side
entry
stop
target
exit
```

Set an acceptable numerical tolerance for floating-point differences.

Any unexplained discrepancy is a test failure.

------------------------------------------------------------------------

# 58. Unit Tests

Test each feature independently.

Examples:

``` text
EMA
ATR
RSI
RVOL
ROC
relative strength
swing detection
BOS
liquidity sweep
breakout
retest
position sizing
stop calculation
TP calculation
```

Use small synthetic candle sequences where the expected answer is known.

------------------------------------------------------------------------

# 59. Property Tests

Examples:

### Long/short symmetry

If OHLC data is inverted around a reference price, long and short logic
should behave symmetrically where intended.

### No future access

A feature calculated at candle N must remain unchanged when future
candles are appended, provided the feature was already confirmed.

### Determinism

Same input → same output.

------------------------------------------------------------------------

# 60. Backtest Reproducibility

Every backtest should produce:

``` text
run_id
strategy_version
config_hash
data_version
universe_version
start_time
end_time
fee_model
slippage_model
execution_model
```

Example:

``` json
{
  "run_id": "bt_2026_00142",
  "strategy_version": "1.0.0",
  "config_hash": "8c31...",
  "data_version": "exchange-data-2026-09-01",
  "execution_model": "next_open",
  "fee_model": "taker",
  "slippage_model": "atr_liquidity"
}
```

------------------------------------------------------------------------

# 61. Risk Engine

Position sizing should be risk-based.

Example:

``` text
Account equity = $10,000
Risk per trade = 0.5%
Maximum loss = $50
```

If:

``` text
Entry = $100
Stop = $95
Risk/unit = $5
```

Then:

``` text
quantity = $50 / $5
         = 10 units
```

For low-cap assets, apply additional liquidity constraints.

Final position size:

``` text
min(
    risk_based_size,
    liquidity_based_size,
    market_cap_based_limit,
    strategy_max_position
)
```

------------------------------------------------------------------------

# 62. Risk Limits

V1 should support:

``` text
max risk per trade
max total open risk
max correlated exposure
max positions
max daily loss
max consecutive losses
max low-liquidity exposure
```

These belong to the risk engine, not the signal engine.

------------------------------------------------------------------------

# 63. Correlation / Cluster Risk

If the scanner generates:

``` text
LONG DOGE
LONG PEPE
LONG SHIB
LONG BONK
```

these may represent substantially overlapping exposure to a meme/altcoin
risk factor.

The system should optionally calculate:

``` text
rolling correlation
sector/category
beta to BTC
beta to ETH
```

and prevent excessive aggregate exposure.

------------------------------------------------------------------------

# 64. Alerting

Signal events:

``` text
NEW_SIGNAL
ENTRY_TRIGGERED
TP1_HIT
TP2_HIT
STOP_HIT
SIGNAL_INVALIDATED
```

Delivery options:

``` text
WebSocket
Email
Discord
Telegram
Push notification
```

Alert payload should contain the full signal explanation.

------------------------------------------------------------------------

# 65. Observability

Track:

``` text
data latency
candle processing latency
feature calculation latency
signals/minute
errors
missing candles
duplicate candles
provider failures
```

Metrics:

``` text
data_freshness_seconds
feature_latency_ms
signal_latency_ms
provider_error_rate
```

------------------------------------------------------------------------

# 66. Failure Handling

If market data is stale:

``` text
DO NOT GENERATE NEW SIGNALS
```

If higher-timeframe data is missing:

``` text
DO NOT FALL BACK TO FUTURE DATA
```

If liquidity information is unavailable:

``` text
mark execution score as unknown
```

Do not silently substitute bad data.

------------------------------------------------------------------------

# 67. Strategy V1 Specification

Initial strategy hypothesis:

## Long

### Market context

``` text
BTC not in severe bearish momentum
```

### Higher timeframe

``` text
4H structure bullish OR neutral
```

### Asset relative strength

``` text
1H RS percentile >= threshold
```

### Setup

One of:

``` text
BREAKOUT
MOMENTUM_CONTINUATION
LIQUIDITY_SWEEP_REVERSAL
RANGE_BREAKOUT
```

### Confirmation

At least:

``` text
RVOL expansion
AND
structure confirmation
```

### Execution

Require:

``` text
acceptable liquidity
AND
sufficient reward to nearby resistance/liquidity
```

### Entry

``` text
next candle open
```

### Stop

``` text
structural invalidation ± ATR buffer
```

### Exit

``` text
TP1
TP2
trailing stop
hard invalidation
```

Short logic is the mirrored implementation.

------------------------------------------------------------------------

# 68. Important: Do Not Optimize the First Version

V1 should be treated as a **testable hypothesis**, not a finished
profitable strategy.

First establish:

``` text
Data correctness
        ↓
Feature correctness
        ↓
Signal correctness
        ↓
Backtest correctness
        ↓
Pine equivalence
        ↓
Performance analysis
        ↓
Parameter research
```

Do not optimize before proving the pipeline is correct.

------------------------------------------------------------------------

# 69. Research Workflow

Recommended process:

``` text
1. Define strategy hypothesis
2. Implement features
3. Build deterministic backtester
4. Test on BTC/ETH
5. Test on midcaps
6. Test on lowcaps
7. Test on memes
8. Test different market regimes
9. Add realistic fees/slippage
10. Walk-forward validation
11. Out-of-sample test
12. Pine reconciliation
13. Paper trading
14. Live monitoring
```

------------------------------------------------------------------------

# 70. Performance Analysis by Segment

Never report only:

``` text
Overall PnL
```

Break it down by:

``` text
Market cap
Setup type
Market regime
Volatility regime
Asset
Exchange
Timeframe
Long/short
Holding duration
RVOL bucket
RS bucket
```

Example:

``` text
Setup                Trades   PF     Avg R
------------------------------------------------
Breakout              412    ...
Momentum continuation 287    ...
Sweep reversal        193    ...
Range breakout        156    ...
```

The purpose is diagnostic, not to select a "winner" based solely on
historical performance.

------------------------------------------------------------------------

# 71. Strategy Research Questions

The backtesting framework should answer questions such as:

### Volume

``` text
Does RVOL > 1.5 outperform RVOL > 2?
```

### Relative strength

``` text
Does requiring top 20% RS improve expectancy?
```

### Retest

``` text
Does waiting for a retest improve risk-adjusted performance?
```

### Market context

``` text
How does the strategy behave during BTC drawdowns?
```

### Market cap

``` text
Does the setup behave differently across market-cap buckets?
```

### Memes

``` text
Does abnormal volume + relative strength remain useful for meme assets?
```

These are empirical questions.

------------------------------------------------------------------------

# 72. Recommended Project Structure

``` text
crypto-signal-engine/
│
├── apps/
│   ├── api/
│   ├── scanner/
│   ├── backtester/
│   └── web/
│
├── strategy/
│   ├── specification/
│   │   ├── strategy.yaml
│   │   ├── features.yaml
│   │   └── rules.yaml
│   │
│   ├── features/
│   │   ├── trend.py
│   │   ├── momentum.py
│   │   ├── volume.py
│   │   ├── volatility.py
│   │   ├── structure.py
│   │   ├── liquidity.py
│   │   └── relative_strength.py
│   │
│   ├── setups/
│   │   ├── breakout.py
│   │   ├── continuation.py
│   │   ├── sweep.py
│   │   └── range.py
│   │
│   ├── risk/
│   │   ├── sizing.py
│   │   ├── stops.py
│   │   └── targets.py
│   │
│   └── engine.py
│
├── data/
│   ├── providers/
│   ├── normalization/
│   ├── ingestion/
│   └── storage/
│
├── backtesting/
│   ├── engine.py
│   ├── execution.py
│   ├── fees.py
│   ├── slippage.py
│   └── metrics.py
│
├── pine/
│   ├── generated/
│   └── reconciliation/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── property/
│   └── reconciliation/
│
├── migrations/
│
├── docker/
│
└── docs/
    ├── architecture.md
    ├── strategy.md
    ├── backtesting.md
    └── pine-compatibility.md
```

------------------------------------------------------------------------

# 73. Recommended Implementation Order

## Phase 1 --- Data

Build:

``` text
OHLCV ingestion
normalization
PostgreSQL storage
timeframe aggregation
data validation
```

## Phase 2 --- Feature Engine

Implement:

``` text
EMA
ATR
RSI
ROC
RVOL
relative strength
swing points
BOS
range detection
liquidity sweeps
```

## Phase 3 --- Backtester

Implement:

``` text
sequential event loop
entries
stops
targets
fees
slippage
trade ledger
metrics
```

## Phase 4 --- Strategy

Implement:

``` text
breakout
momentum continuation
sweep reversal
range breakout
```

## Phase 5 --- Scanner

Implement:

``` text
universe management
candidate filtering
signal API
real-time processing
```

## Phase 6 --- UI

Implement:

``` text
scanner
signal detail
chart
trade history
backtest dashboard
```

## Phase 7 --- Pine

Implement the exact same strategy specification in Pine.

## Phase 8 --- Validation

Run:

``` text
backend vs Pine
historical
walk-forward
out-of-sample
paper trading
```

------------------------------------------------------------------------

# 74. V1 Acceptance Criteria

The system is ready for serious strategy research when:

-   [ ] Same input produces same output.
-   [ ] No feature uses future information.
-   [ ] Higher-timeframe values are confirmed correctly.
-   [ ] Historical universe is survivorship-aware.
-   [ ] Fees are modeled.
-   [ ] Slippage is modeled.
-   [ ] Intrabar ambiguity is handled.
-   [ ] Every signal has an explanation.
-   [ ] Every trade has a feature snapshot.
-   [ ] Strategy versions are immutable.
-   [ ] Backtest configuration is reproducible.
-   [ ] Large/mid/low-cap assets can all be processed.
-   [ ] Meme assets can be processed without special-case technical
    logic.
-   [ ] Liquidity affects execution/risk rather than automatically
    eliminating the asset.
-   [ ] Backend signals reconcile with Pine within defined tolerances.
-   [ ] Walk-forward/out-of-sample tests exist.
-   [ ] Paper-trading monitoring exists.

------------------------------------------------------------------------

# 75. Core Principle

The application should ultimately answer:

> **"Which assets currently exhibit a statistically testable combination
> of abnormal participation, relative strength, volatility expansion,
> and confirmed price structure --- and what would the exact trade
> parameters be if the setup is acted upon?"**

It should **not** attempt to answer:

> "Which coin will go up?"

The first question is measurable, testable, reproducible, and suitable
for a deterministic trading engine.

------------------------------------------------------------------------

# 76. Future Extensions

Potential V2/V3 features:

``` text
Order-book imbalance
Funding rates
Open interest
Liquidation data
On-chain flows
DEX liquidity
Token holder concentration
Social velocity
News/event detection
ML ranking
Regime classification
Portfolio optimization
Automatic execution
```

These should be added as independent feature providers rather than
tightly coupling them to the core strategy.

------------------------------------------------------------------------

# 77. Final Architecture

The intended final system is:

``` text
                 ┌─────────────────────────┐
                 │     MARKET DATA          │
                 │ OHLCV / Volume / Depth   │
                 └────────────┬────────────┘
                              │
                              v
                 ┌─────────────────────────┐
                 │     DATA NORMALIZER      │
                 └────────────┬────────────┘
                              │
                              v
                 ┌─────────────────────────┐
                 │      FEATURE ENGINE      │
                 │                         │
                 │ Trend                   │
                 │ Structure               │
                 │ Volume                  │
                 │ Momentum                │
                 │ Volatility              │
                 │ Relative Strength       │
                 │ Liquidity               │
                 │ Market Context          │
                 └────────────┬────────────┘
                              │
                              v
                 ┌─────────────────────────┐
                 │      SETUP ENGINE        │
                 │                         │
                 │ Breakout                │
                 │ Continuation            │
                 │ Liquidity Sweep         │
                 │ Range Breakout          │
                 └────────────┬────────────┘
                              │
                              v
                 ┌─────────────────────────┐
                 │      SIGNAL ENGINE       │
                 │                         │
                 │ Setup Score             │
                 │ Execution Score         │
                 │ Entry                   │
                 │ Stop                    │
                 │ Targets                 │
                 └────────────┬────────────┘
                              │
              +---------------+----------------+
              |               |                |
              v               v                v
          LIVE SCANNER    BACKTESTER      PINE SCRIPT
              |               |                |
              +---------------+----------------+
                              |
                              v
                 ┌─────────────────────────┐
                 │      TRADE ANALYTICS     │
                 │                         │
                 │ Expectancy              │
                 │ Profit Factor           │
                 │ Drawdown                │
                 │ Regime Analysis         │
                 │ Market-Cap Analysis     │
                 │ Setup Analysis           │
                 └─────────────────────────┘
```

**The critical architectural decision is to make the strategy
specification independent from any particular runtime.** That gives you
one canonical definition that can drive the scanner, historical
backtester, live signal engine, and TradingView implementation without
creating four subtly different strategies.

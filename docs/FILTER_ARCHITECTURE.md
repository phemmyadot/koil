# Dashboard filter architecture

Settled design for the toolbar filters in `webapp/static/index.html`, replacing
the old checkbox/dropdown-based filters. All sliders/radios/switches are
**minimum-threshold** controls: the selected position matches that value and
everything above it, and the leftmost/off position is always the default (no
filtering) so the unfiltered view is reproduced by "everything at default."

## Removed

- **"Min score" dropdown** — superseded by the Pre-Breakout panel's Phase
  slider.
- **Sort dropdown** — the ticker list is now permanently sorted by days-in-trade,
  newest first (smallest `days_held` across any strategy's open position;
  tickers with no open trade anywhere sort last). No user-facing sort control.

## Advance Filter panel

Flat, not per-strategy `<details>` sections, not vertically stacked
(`.advmetric` reverts to a horizontal layout for this panel specifically).
Base/Training/Holdout sub-views are gone — training/holdout is deprecated
app-wide, so this only ever reads a strategy's baseline stats.

```
┌─ Strategy ─────────────────────────────┐
│  ○────────●────────○                   │
│ VEXH      VCP      VCPO                │
└──────────────────────────────────────────┘

┌─ Win Rate ────────────────┐   AND   ┌─ Profit ───────────────────┐
│  ●────────○────────○      │         │  ●────────○────────○       │
│  0%      50%      75%     │         │  1       1.5       2       │
└────────────────────────────┘         └─────────────────────────────┘
```

- **Strategy** radio: VEXH / VCP / VCPO (ADX/VCPF included too when
  `SHOW_ADX_VCPF` is on, same gating as elsewhere). Picks which strategy's
  stats the two sliders below read.
- **Win Rate** steps: `0%` (default) / `50%` / `75%`.
- **Profit** steps: `1` (default) / `1.5` / `2`.
- Win Rate AND Profit combine; both at default = no filtering.

## Trade On panel

Unchanged. Vertical checkbox stack, OR within the group, ANDs against every
other panel.

```
☐ ADX
☐ VCP
☐ VCPO
☐ VCPF
☐ VEXH
```

## Pre-Breakout panel

New. Replaces the old 5-checkbox state filter entirely.

```
┌─ Phase ──────────────────────────────────────────────┐
│  ○──────●──────○──────○──────○──────○                │
│ BEARISH NEUTRAL BULLISH COILING PRE-BREAKOUT BREAKOUT │
│         (default)                                     │
└────────────────────────────────────────────────────────┘

  Volatility Squeeze          Volume Dry-up
  [ ○ ] off      switch       [ ○ ] off      switch

  Clustering At Ceiling       Trend Filter
  [ ○ ] off      switch       [ ○ ] off      switch

┌─ Coil Energy Build-up ────────────────────────────┐
│  ●──────○──────○──────○──────○                    │
│  0(off) 5+     8+     12+    15+                   │
└──────────────────────────────────────────────────────┘
```

- **Phase** (6 positions, default **NEUTRAL**): minimum on the score ladder
  BEARISH(-2) < NEUTRAL(0) < BULLISH(1) < COILING(2) < PRE-BREAKOUT(4) <
  BREAKOUT(5). Default hides BEARISH tickers (a deliberate change from the
  old "all visible" default).
- **Squeeze / Volume / Resistance / Trend**: 4 independent switches, each
  off by default (no filter), on = require that condition true
  (`bb_squeeze`/`vol_dry_up`/`near_resistance`/`is_bullish_trend`).
- **Coil Energy** (5 positions): `0` (default/off) / `5+` / `8+` / `12+` /
  `15+`, minimum `squeeze_counter` bars.
- All 6 controls AND together; a ticker must clear every non-default one.

## Combination rules (all panels)

- Every panel's result ANDs against every other panel's result, and against
  ticker search / min trades.
- Within Trade On only: multiple checked boxes OR together.
- Within Advance Filter: Win Rate AND Profit (for the selected Strategy).
- Within Pre-Breakout: Phase AND Coil Energy AND all 4 switches.
- Any control left at its default contributes no filtering.

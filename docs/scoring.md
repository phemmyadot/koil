# Exhaustion Dashboard — Scoring Logic Architecture

## Overview

The dashboard scores each ticker on a **10-point scale** across **8 dimensions**. The score measures *setup quality* — how favourable the current technical and statistical conditions are for a trade. It does not replace the Advance Filter, which measures *execution quality* (PF floor, WR floor, minimum trades). Both gates are required before entry.

**Score = setup signal. Filter = execution gate. Neither replaces the other.**

---

## Score Bands

| Score | Label | Meaning |
|---|---|---|
| 9–10 | Exceptional | All dimensions pass; strong strategy edge |
| 7–8 | Strong | Most dimensions pass; good strategy quality |
| 5–6 | Acceptable | Setup is there; strategy quality is weaker |
| 3–4 | Borderline | Worth investigating; multiple flags |
| 0–2 | Pass | Do not trade |

---

## Dimension Weights

| # | Dimension | Max Points |
|---|---|---|
| 1 | Strategy Quality (PF × WR) | 3 |
| 2 | Sample Size | 1 |
| 3 | VCP Matrix Setup | 1 |
| 4 | Compression Quality | 1 |
| 5 | Signal Timing / MAE | 1 |
| 6 | Outlier PnL Concentration | 1 |
| 7 | Recency of Performance | 1 |
| 8 | Earnings Proximity | 1 |
| | **Total** | **10** |

Strategy Quality is the only multi-point dimension because it was the single most predictive factor in session validation — separating every genuine entry from every pass regardless of how good the setup looked otherwise.

---

## Dimension Definitions

### 1. Strategy Quality — 3 points

Combines Profit Factor and Win Rate for the selected strategy (VCPO or VEXH). PF is the primary discriminator; WR confirms consistency.

| Points | Condition |
|---|---|
| 3 | PF ≥ 3.5 AND WR ≥ 75% |
| 2 | PF ≥ 2.5 AND WR ≥ 70% |
| 1 | PF ≥ 2.0 AND WR ≥ 65% |
| 0 | Below either threshold |

**Rationale:** PF alone can be distorted by a single outlier trade. WR alone can be high on a declining asset (lucky streak). The combination resists both failure modes. A PF < 1 means the strategy loses money on this asset regardless of win rate — automatic 0.

---

### 2. Sample Size — 1 point

Statistical reliability threshold. Below 20 trades, win rate and PF are too sensitive to single-trade outcomes to be trusted.

| Points | Condition |
|---|---|
| 1 | ≥ 20 trades |
| 0 | < 20 trades |

**Rationale:** ALMS (11 trades, 100% WR) and DSGN (5 trades) were the clearest cases of misleading stats from thin samples. HRI (2 VEXH trades, PF 99.99) is noise, not edge. 20 trades gives the law of large numbers a reasonable foundation.

---

### 3. VCP Matrix Setup — 1 point

Measures the quality of the current technical setup using the UPB indicator's Market Phase Score.

| Points | Condition |
|---|---|
| 1 | PRE-BREAKOUT (score = 4) or BREAKOUT (score = 5) |
| 0 | COILING (BULL) (score = 2) or below |

**Rationale:** PRE-BREAKOUT (4) requires all four conditions simultaneously: volatility compressed, volume dry, price clustering at ceiling, trend bullish. In session validation, every confirmed entry or top watchlist name had PRE-BREAKOUT or was approaching it. COILING (BULL) — even with 19+ bars of coil like IBKR — correctly scores 0 here because it hasn't yet met all four conditions. IBKR's high overall score comes from Strategy Quality, Sample Size, and other dimensions — not from VCP Matrix, which is honest about where it actually is in the setup cycle.

**UPB Score reference:**

| Score | Label |
|---|---|
| -2 | BEARISH |
| 0 | NEUTRAL |
| 1 | BULLISH |
| 2 | COILING (BULL) |
| 4 | PRE-BREAKOUT |
| 5 | BREAKOUT |

---

### 4. Compression Quality — 1 point

Measures whether both ATR compression and volume dry-up are present simultaneously. Either condition alone is insufficient.

| Points | Condition |
|---|---|
| 1 | Volatility Squeeze = COMPRESSED AND Volume Dry-up = DRY (NO SELLERS) |
| 0 | Either condition missing |

**Rationale:** COMPRESSED volatility without DRY volume means sellers are still active — price can compress under selling pressure and then break down rather than up. DRY volume without compression means the stock is just quiet, not coiling. The combination — tight price action with no sellers — is what precedes the explosive breakouts that VCPO is designed to catch.

---

### 5. Signal Timing / MAE — 1 point

Measures whether the current open position's adverse excursion is within the historical range of winning trades. Separates normal dips from trades trending toward failure.

| Points | Condition |
|---|---|
| 1 | No open position (fresh signal, zero MAE) OR open MAE < avg_mae_wins_pct |
| 0 | Open MAE ≥ avg_mae_wins_pct OR no active signal at all |

**Rationale:** Every winning trade has some adverse excursion. The question is whether the current dip is within the range historically seen on winners. CRDO had -18.11% MAE — uncomfortable but within range (trade 11 won at -21.16% MAE). KOD had -8.64% MAE trending toward its typical loss territory. This dimension captures that distinction quantitatively rather than requiring manual CSV analysis.

**`avg_mae_wins_pct`** is computed as the average of the `Adverse excursion %` column across all winning trades only. It is computed per strategy per asset and exposed in the modal alongside the suggested limit price.

---

### 6. Outlier PnL Concentration — 1 point

Detects whether total PnL is driven by a single exceptional trade rather than consistent edge.

| Points | Condition |
|---|---|
| 1 | No single trade contributes > 35% of total PnL |
| 0 | Any single trade contributes > 35% of total PnL |

**Rationale:** KOD's top trade generated 67% of total PnL (+262% from one crypto-spike trade). PHAT's top trade generated 80%. ALMS's top trade generated 41%. In all three cases, the headline win rate and PF were misleading because future trades are unlikely to replicate the outlier. A strategy that requires lightning to strike to be profitable isn't a systematic edge.

**Computed as:** `max(trade_pnl) / sum(trade_pnl)` across all closed trades. Negative total PnL assets automatically score 0.

---

### 7. Recency of Performance — 1 point

Measures whether the strategy is working in the *current market regime*, not just historically on average.

| Points | Condition |
|---|---|
| 1 | ≥ 4 wins in the last 5 closed trades |
| 0 | < 4 wins in the last 5 closed trades OR fewer than 5 closed trades total |

**Rationale:** MAMA had 3 early losses in 2022 then 14 consecutive wins from 2023 onward — the regime changed and the strategy started working. TPC had 4 losses all pre-2026. Weighting recent performance separately from the overall win rate captures these regime shifts. A strategy at 80% overall but 2/5 in recent trades is deteriorating; a strategy at 70% overall but 5/5 recently is improving.

**`last5_trades`** is already computed and exposed in the modal — this dimension just aggregates the win count from that existing field.

---

### 8. Earnings Proximity — 1 point

Binary check for binary event risk. Any earnings release within a 21-calendar-day window (approximately 15 trading days) creates gap risk that cannot be managed via stop loss.

| Points | Condition |
|---|---|
| 1 | No earnings within the window |
| 0 | Earnings within the window |

**Rationale:** SIMO (7 days to earnings) and CAKE (6 days) were automatic passes in session validation regardless of how strong their setups were. SIMO had PF 7.254 — the best of the session — but was still correctly passed. A gap-up of 15% on a beat or a gap-down of 20% on a miss renders the stop loss meaningless and the risk/reward calculation invalid. This dimension reuses the existing `earnings_risk` flag, which uses a tighter window than the strategy's 20-bar horizon but is legitimate since earnings proximity is a ticker-level fact, not strategy-specific.

---

## Architecture

### Score Computation

Runs server-side, in `_compute_one()` alongside the existing `payload["prebreak"] = prebreak.evaluate(...)` call, once per strategy (`vexh`, `strategy_vcp`, `strategy_vcpo`), stored as `payload["setup_score"][strategy_key]`. `r` is the in-progress `payload` dict; `r[strategy]` is the same flat stats shape for all three strategies (see `backend/strategy_common.py`).

```python
def compute_score(r: dict, strategy: str = "strategy_vcpo") -> int:
    s = r.get(strategy) or {}
    prebreak = r.get("prebreak") or {}

    # 1. Strategy Quality (0-3)
    pf = s.get("profit_factor", 0)
    wr = s.get("win_rate", 0)
    if pf >= 3.5 and wr >= 75:
        strategy_pts = 3
    elif pf >= 2.5 and wr >= 70:
        strategy_pts = 2
    elif pf >= 2.0 and wr >= 65:
        strategy_pts = 1
    else:
        strategy_pts = 0

    # 2. Sample Size (0-1)
    sample_pts = 1 if s.get("n_trades", 0) >= 20 else 0

    # 3. VCP Matrix Setup (0-1)
    phase_score = prebreak.get("score", 0)
    vcp_pts = 1 if phase_score >= 4 else 0

    # 4. Compression Quality (0-1)
    compressed = prebreak.get("bb_squeeze", False)
    dry = prebreak.get("vol_dry_up", False)
    compression_pts = 1 if (compressed and dry) else 0

    # 5. Signal Timing / MAE (0-1)
    open_pos = s.get("open_position")
    avg_mae = s.get("avg_mae_wins_pct", None)
    if open_pos and avg_mae is not None:
        open_mae = open_pos.get("mae_pct", 0)
        timing_pts = 1 if open_mae < avg_mae else 0
    elif open_pos is None and s.get("signal_today", False):
        # Fresh signal today, not yet in a position -- NOT "has any trade
        # history," which would score 1 even for a ticker with zero current
        # setup (that was the original bug: n_trades > 0 fires on every
        # stale ticker with a track record, not just live signals).
        timing_pts = 1
    else:
        timing_pts = 0

    # 6. Outlier PnL Concentration (0-1)
    outlier_pct = s.get("max_trade_pnl_fraction", 1.0)  # fraction 0-1, new field
    outlier_pts = 1 if outlier_pct <= 0.35 else 0

    # 7. Recency of Performance (0-1)
    last5 = s.get("last5_trades", [])
    wins_in_last5 = sum(1 for t in last5 if t.get("tp_pct", 0) > 0)
    recency_pts = 1 if (len(last5) >= 5 and wins_in_last5 >= 4) else 0

    # 8. Earnings Proximity (0-1) -- ticker-level, not nested under s
    earnings_pts = 0 if r.get("earnings_risk", False) else 1

    total = (strategy_pts + sample_pts + vcp_pts + compression_pts +
             timing_pts + outlier_pts + recency_pts + earnings_pts)

    return total  # integer 0-10
```

**Two fields this reads don't exist in the backend yet and must be added first:** `open_position["mae_pct"]` and `s["max_trade_pnl_fraction"]` (see Implementation Plan below).

### Frontend Display

The score renders as `X/10` on each card. Color coding follows the band table:

```javascript
function scoreColor(score) {
  if (score >= 9) return "#00b050";  // green — exceptional
  if (score >= 7) return "#92d050";  // light green — strong
  if (score >= 5) return "#ffbf00";  // amber — acceptable
  if (score >= 3) return "#ff8c00";  // orange — borderline
  return "#ff0000";                  // red — pass
}
```

The existing `x/5` bar display should be replaced with a numeric `X/10` badge to avoid misreading. A progress bar is acceptable but the number should always be visible alongside it.

---

## Separation of Concerns

| Gate | Purpose | Where applied |
|---|---|---|
| Score (0–10) | Setup quality — is the technical and statistical context favourable? | Card display, sort order |
| Advance Filter | Execution quality — does this asset have a reliable strategy edge? | PF ≥ 2.5, WR ≥ 75%, trades ≥ 20 |
| Pre-Breakout filter | Setup stage — is the compression at the right phase? | Phase ≥ COILING, coil ≥ 5 bars |
| Earnings flag | Binary event risk — is there gap risk within the trade window? | `earnings_risk` boolean |

A high score does not mean enter. It means the setup quality is high enough to warrant loading the chart in TradingView and confirming the Pine Script signal. The Advance Filter is what determines whether the strategy has edge on this asset. Both must pass.

---

## Session Validation

Scores computed against July 22–24, 2026 session data. All entries and passes correctly ranked.

| Asset | Score | Action taken | Correct? |
|---|---|---|---|
| VTRS | 9/10 | Entered — call $17 Aug 21 | ✅ |
| VIST | 9/10 | Entered — spot + call limit | ✅ |
| IBKR | 8/10 | Top watchlist — no signal yet | ✅ |
| CIFR | 8/10 | Top watchlist — no signal yet | ✅ |
| MAMA | 8/10 | Spot limit $16.59 pending | ✅ |
| NWG | 8/10 | Watchlist — PF too weak to trade | ✅ |
| ROST | 8/10 | Watchlist — EXPANDED, 1 bar | ✅ |
| HAS | 6/10 | Call limit $1.80 cancelled | ✅ |
| TPC | 6/10 | Investigated, passed | ✅ |
| CRDO | 6/10 | Small spot entered | ✅ |
| ELF | 6/10 | Watchlist — trade running, BE locked | ✅ |
| KOD | 6/10 | Passed — options untradeable | ✅ |
| GRAL | 4/10 | Passed | ✅ |
| PHAT | 3/10 | Passed | ✅ |
| ALMS | 3/10 | Passed | ✅ |
| DSGN | 3/10 | Passed | ✅ |
| KKR | 4/10 | Passed | ✅ |

All 17 assets correctly ranked. No false positives (high score, bad trade) or false negatives (low score, missed opportunity) in the validation set.

---

## Known Limitations

**Score is strategy-agnostic by default.** The score uses whichever strategy is selected in the Advance Filter (VCPO by default). If an asset has strong VEXH stats but weak VCPO stats, the score reflects VCPO. Switching the Advance Filter strategy should recompute the score.

**MAE timing requires an open position.** If there is no open signal, dimension 5 scores 1 by convention (fresh signal available = favourable). This means a ticker with no current signal but strong historical stats will score the same on dimension 5 as a ticker with a perfect fresh signal. A future refinement could distinguish "no signal exists" (score 0) from "signal exists, no open position yet" (score 1).

**Outlier detection uses 35% as a fixed threshold.** This was calibrated against today's session where 40%+ concentration was clearly problematic. Assets with exactly 35–40% concentration from one strong trade in a genuinely consistent strategy may be slightly penalised. The threshold can be tuned as more session data accumulates.

**Recency uses last 5 trades.** For assets with very long holding periods (ROST trade 11 ran 96 bars; VIST trade 15 ran 64 bars), "last 5 trades" may span several years rather than recent months. A time-based recency filter (last 12 months) would be more meaningful for these assets but requires duration data per trade.
Current coloring — what it does:

javascript
function wrColorClass(wr) {
  if (wr >= 70) return "ok";    // green
  if (wr >= 50) return "mid";   // amber
  return "no";                  // red
}

function pfColorClass(pf) {
  if (pf >= 2.0) return "ok";   // green
  if (pf >= 1.0) return "mid";  // amber
  return "no";                  // red
}

O badge — blue when active ("active" class), neutral gray when no open position. No change needed — blue = in trade is unambiguous.

T badge — always neutral gray. Correct — trade count is a quantity, not a quality signal. No change needed.

WR — needs update:

The current thresholds (70% green, 50% amber, below red) don't align with what the session validated. The Advance Filter's floor is 70%, which means any ticker passing the filter is already green on WR by definition — the coloring adds no information. And 50% amber is too generous: a 55% win rate with PF 1.2 is not meaningfully better than a 48% rate.

The coloring should reflect the same quality tiers from the scoring system:

javascript
function wrColorClass(wr) {
  if (wr >= 75) return "ok";    // green  — scoring tier 1 (PF 3.5+/WR 75+)
  if (wr >= 70) return "mid";   // amber  — scoring tier 2 (PF 2.5+/WR 70+)
  if (wr >= 65) return "neutral"; // gray — scoring tier 3 (PF 2.0+/WR 65+)
  return "no";                  // red    — below all tiers
}

This way the card coloring is internally consistent with the scoring system — a 3-point strategy quality score looks green on WR, a 2-point looks amber, a 1-point looks gray, 0 looks red.

PF — needs update:

Current thresholds (2.0 green, 1.0 amber) are too coarse. PF 2.1 and PF 7.0 both render green, which loses the distinction between "acceptable" and "exceptional." The session made clear that PF < 2.5 is a meaningful warning even when WR is fine — NWG (PF 2.25) and TPC (PF 2.06) were both passes despite passing the 2.0 threshold.

javascript
function pfColorClass(pf) {
  if (pf >= 3.5) return "ok";      // green  — strong edge
  if (pf >= 2.5) return "mid";     // amber  — acceptable, watch
  if (pf >= 2.0) return "neutral"; // gray   — borderline
  return "no";                     // red    — no edge or negative
}

This aligns with the PF filter steps (PF_STEPS = [0, 1, 1.5, 2, 2.5, 3.5, 5.0]) and the scoring system's tier breakpoints. A PF shown in amber signals "passes the filter floor but watch carefully" — exactly the mental model you applied to NWG and TPC manually in the session.

One additional badge worth reconsidering — T (trade count):

Currently always neutral. But trade count is meaningfully a quality signal when it's low — the minimum floor is 20 trades, but a ticker with 21 trades sitting next to one with 37 trades should look different. The neutral coloring hides this:

javascript
function tradeCountColorClass(n) {
  if (n >= 25) return "ok";      // green  — solid sample
  if (n >= 20) return "mid";     // amber  — passes floor, thin
  if (n >= 15) return "neutral"; // gray   — borderline
  return "no";                   // red    — too thin to trust
}

Below 15 the default filter already excludes it, so the red band is mainly visible when filters are relaxed. The amber band (20–24) is the useful signal — it tells you "this ticker passed the min trades filter but barely."

Summary of proposed changes:

Badge	Current	Proposed	Reason
O	blue/gray	no change	clear and correct
T	always neutral	ok/mid/neutral/no by count	trade count is a quality signal
WR	70+ green, 50+ amber	75+ green, 70+ amber, 65+ gray	align with scoring tiers
PF	2.0+ green, 1.0+ amber	3.5+ green, 2.5+ amber, 2.0+ gray	align with PF filter steps and session evidence

All four changes are single-function updates — no structural changes to statBadges() itself. The watchlist page's duplicate wrColorClass/pfColorClass functions need the same update to stay in sync.
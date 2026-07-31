// Win-rate / profit-factor / trade-count color tiers. Ported verbatim from index.html
// (wrColorClass/pfColorClass/tradeCountColorClass) and watchlist.html's identical copy --
// watchlist.html's own comment said "Keep in sync with index.html's copy" (see
// backend/color-code.md), which is exactly the drift this rewrite removes by having one impl.

export type ColorTier = "ok" | "mid" | "neutral" | "no";

export function wrColorClass(wr: number): ColorTier {
  if (wr >= 75) return "ok"; // scoring tier 1 (PF 3.5+/WR 75+)
  if (wr >= 70) return "mid"; // scoring tier 2 (PF 2.5+/WR 70+)
  if (wr >= 65) return "neutral"; // scoring tier 3 (PF 2.0+/WR 65+)
  return "no";
}

export function pfColorClass(pf: number): ColorTier {
  if (pf >= 3.5) return "ok"; // strong edge
  if (pf >= 2.5) return "mid"; // acceptable, watch
  if (pf >= 2.0) return "neutral"; // borderline
  return "no";
}

// Below 15 the default Min Trades filter already excludes it; this tier is mostly cosmetic
// for anything that clears the filter at all.
export function tradeCountColorClass(n: number): ColorTier {
  if (n >= 25) return "ok";
  if (n >= 20) return "mid";
  if (n >= 15) return "neutral";
  return "no";
}

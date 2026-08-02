// Strategy key/label mapping. Previously defined 3 times: index.html's ADV_STRAT_KEY,
// watchlist.html's LIST_STRAT_KEY (same values, different variable name), and
// position.html's STRATEGY_LABELS (the only file that had display labels at all). One
// definition here; strategy_key stays the wire format used everywhere in the API (e.g.
// "strategy_vcpo"), STRATEGY_LABELS is display-only.

export type StrategyShortKey = "vcp" | "vcpo" | "vexh";
export type StrategyKey = "strategy_vcp" | "strategy_vcpo" | "vexh";

export const ADV_STRATEGIES: [StrategyShortKey, string][] = [
  ["vcp", "VCP"],
  ["vcpo", "VCPO"],
  ["vexh", "VEXH"],
];

export const ADV_STRAT_KEY: Record<StrategyShortKey, StrategyKey> = {
  vcp: "strategy_vcp",
  vcpo: "strategy_vcpo",
  vexh: "vexh",
};

export const STRATEGY_LABELS: Record<string, string> = {
  vexh: "VEXH",
  strategy_vcp: "VCP",
  strategy_vcpo: "VCPO",
  // Not a screener signal -- a trade added via "+ Add Trade" for a ticker outside the screened
  // universe, see docs/superpowers/specs/2026-08-01-add-trade-untracked-ticker-design.md.
  // Deliberately NOT added to ADV_STRATEGIES/ADV_STRAT_KEY -- those drive the Advance/Trade-on
  // filters, which only make sense for real strategy signals to filter on.
  manual: "Manual",
};

export function stratLabel(key: string): string {
  return STRATEGY_LABELS[key] ?? key;
}

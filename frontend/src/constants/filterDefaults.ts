// Filter step tables and defaults. Ported verbatim from index.html's Advance Filter /
// Pre-Breakout filter constants -- see webapp/FILTER_ARCHITECTURE.md for the filter-combination
// rules these feed (all filters AND together; WR/PF are a dual-slider minimum-threshold pair).

export const WR_STEPS = [0, 50, 70, 75] as const;
export const PF_STEPS = [0, 1, 1.5, 2, 2.5, 3.5, 5.0] as const;

// VCPO/WR>=70/PF>=2.5 had the strongest backtested results in practice -- not a true no-op
// "off" default.
export const ADV_STRATEGY_DEFAULT = "vcpo" as const;
export const ADV_WR_DEFAULT_INDEX = WR_STEPS.indexOf(70);
export const ADV_PF_DEFAULT_INDEX = PF_STEPS.indexOf(2.5);

// Phase's default is a real floor, not a true no-op: BULLISH/NEUTRAL/BEARISH tickers are
// hidden even with nothing else touched. COILING (BULL) (not NEUTRAL) keeps COILING setups --
// one of the best watchlist additions in practice -- while still cutting BULLISH/NEUTRAL noise
// below it.
export const PHASE_STEPS: [string, number][] = [
  ["BEARISH", -2],
  ["NEUTRAL", 0],
  ["BULLISH", 1],
  ["COILING (BULL)", 2],
  ["PRE-BREAKOUT", 4],
  ["BREAKOUT", 5],
];
export const PHASE_DEFAULT_INDEX = 3; // COILING (BULL)

export const COIL_STEPS = [0, 5, 8, 12, 15] as const;
// Default floor: 5+ bars cuts the 0-bar EXPANDED setups while keeping anything with
// meaningful compression building.
export const COIL_DEFAULT_INDEX = COIL_STEPS.indexOf(5);

export const PREBREAK_SWITCHES: [string, string, string][] = [
  ["squeeze", "Volatility Squeeze", "bb_squeeze"],
  ["volume", "Volume Dry-up", "vol_dry_up"],
  ["resistance", "Clustering At Ceiling", "near_resistance"],
  ["trend", "Trend Filter", "is_bullish_trend"],
];

export const WATCHLIST_NAMES = ["VCP List", "VEXH List", "VCPO List"] as const;

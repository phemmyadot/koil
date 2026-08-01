// Dashboard filter predicates. Ported verbatim from index.html's matchesAdvFilter/
// matchesTradeOnFilter/matchesPrebreakFilter/matchesMinTrades/activeMinTradesStrats -- see
// backend/FILTER_ARCHITECTURE.md for the filter-combination rules (all filters AND together).

import type { TickerPayload } from "../api/types";
import { ADV_STRAT_KEY, type StrategyShortKey as ShortKey } from "../constants/strategy";
import { PF_STEPS, PREBREAK_SWITCHES, WR_STEPS } from "../constants/filterDefaults";

export interface AdvFilterState {
  strategy: ShortKey;
  wrMin: number;
  pfMin: number;
}

export interface PrebreakFilterState {
  phaseMin: number;
  coilMin: number;
  switches: Record<string, boolean>;
}

function strategyResult(r: TickerPayload, strat: ShortKey) {
  return r[ADV_STRAT_KEY[strat]];
}

// True no-op when both sliders sit at their index-0 floor -- don't exclude a ticker for lacking
// data on a strategy that isn't actually being filtered on yet.
export function matchesAdvFilter(r: TickerPayload, adv: AdvFilterState): boolean {
  if (adv.wrMin <= WR_STEPS[0] && adv.pfMin <= PF_STEPS[0]) return true;
  const s = strategyResult(r, adv.strategy);
  if (!s) return false;
  return s.win_rate >= adv.wrMin && s.profit_factor >= adv.pfMin;
}

export function isStrategyActive(r: TickerPayload, key: ShortKey): boolean {
  const s = strategyResult(r, key);
  return !!(s && s.open_position);
}

// ANDed against Advance Filter; "open trade on ANY checked strategy" (OR among the checked
// strategies themselves).
export function matchesTradeOnFilter(r: TickerPayload, strats: ShortKey[]): boolean {
  return strats.length === 0 || strats.some((key) => isStrategyActive(r, key));
}

// Fail OPEN on missing prebreak data (backend couldn't evaluate) rather than hard-excluding.
export function matchesPrebreakFilter(r: TickerPayload, sel: PrebreakFilterState): boolean {
  const pb = r.prebreak;
  if (!pb) return true;
  if (pb.score < sel.phaseMin) return false;
  if (pb.squeeze_counter < sel.coilMin) return false;
  for (const [key, , field] of PREBREAK_SWITCHES) {
    if (sel.switches[key] && !(pb as unknown as Record<string, boolean>)[field]) return false;
  }
  return true;
}

export function strategyNTrades(r: TickerPayload, key: ShortKey): number | null {
  const s = strategyResult(r, key);
  return s ? s.n_trades : null;
}

// Min Trades scoped to whichever strategy is active via Trade On/Advance Filter -- otherwise
// it's dominated by whichever strategy trades most often. Falls back to all three when neither
// filter narrows to specific strategies. Advance Filter's Strategy radio only counts as
// "narrowing" once a slider has moved off its no-op default -- a radio is always selected, so
// that alone doesn't count.
export function activeMinTradesStrats(tradeOnStrats: ShortKey[], adv: AdvFilterState): ShortKey[] {
  const strats = new Set<ShortKey>(tradeOnStrats);
  if (adv.wrMin > WR_STEPS[0] || adv.pfMin > PF_STEPS[0]) strats.add(adv.strategy);
  return strats.size ? [...strats] : (["vexh", "vcp", "vcpo"] as ShortKey[]);
}

export function matchesMinTrades(r: TickerPayload, minTrades: number, strats: ShortKey[]): boolean {
  if (minTrades <= 0) return true;
  return strats.some((key) => (strategyNTrades(r, key) ?? -1) >= minTrades);
}

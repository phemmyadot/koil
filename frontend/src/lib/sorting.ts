// Dashboard 3-tier ticker sort. Ported verbatim from index.html's render(): pending signal
// first, then open trades by days-in-trade ascending (freshest first), then everything else.

import type { TickerPayload } from "../api/types";

const STRATEGY_FIELDS = ["vexh", "strategy_vcp", "strategy_vcpo"] as const;

export function maxDaysInTrade(r: TickerPayload): number | null {
  let max: number | null = null;
  for (const key of STRATEGY_FIELDS) {
    const s = r[key];
    if (s?.open_position) {
      max = max === null ? s.open_position.days_held : Math.max(max, s.open_position.days_held);
    }
  }
  return max;
}

// Checked across all 3 strategies (not scoped to Advance Filter's selection) so a card sorts
// to the front the moment any strategy has a fresh signal.
export function hasPendingSignal(r: TickerPayload): boolean {
  for (const key of STRATEGY_FIELDS) {
    const s = r[key];
    if (s?.signal_today && !s.open_position) return true;
  }
  return false;
}

export function sortTickers(rows: TickerPayload[]): TickerPayload[] {
  return rows.slice().sort((a, b) => {
    const pa = hasPendingSignal(a);
    const pb = hasPendingSignal(b);
    if (pa !== pb) return pa ? -1 : 1;
    const da = maxDaysInTrade(a);
    const db = maxDaysInTrade(b);
    if (da === null && db === null) return 0;
    if (da === null) return 1;
    if (db === null) return -1;
    return da - db;
  });
}

// Watchlist ticker ordering. Ported verbatim from watchlist.html's statsFor/sortByLatestTrade.

import type { StrategyKey, TickerPayload } from "../api/types";

export const LIST_STRAT_KEY: Record<string, StrategyKey> = {
  "VCP List": "strategy_vcp",
  "VEXH List": "vexh",
  "VCPO List": "strategy_vcpo",
};

export interface WatchlistStats {
  n_trades: number;
  win_rate: number;
  profit_factor: number;
  active: boolean;
  days: number | null;
}

export function statsFor(row: TickerPayload | undefined, listName: string): WatchlistStats | null {
  if (!row) return null;
  const key = LIST_STRAT_KEY[listName];
  const s = row[key];
  if (!s) return null;
  return {
    n_trades: s.n_trades,
    win_rate: s.win_rate,
    profit_factor: s.profit_factor,
    active: !!s.open_position,
    days: s.open_position ? s.open_position.days_held : null,
  };
}

// Active (in-trade) tickers first, sorted by days held closest to 0 (most recently entered);
// everything else follows, sorted by win rate descending. No-data tickers sort to the end.
export function sortByLatestTrade(tickers: string[], byTicker: Record<string, TickerPayload>, listName: string): string[] {
  return tickers.slice().sort((a, b) => {
    const sa = statsFor(byTicker[a], listName);
    const sb = statsFor(byTicker[b], listName);
    if (!sa && !sb) return a.localeCompare(b);
    if (!sa) return 1;
    if (!sb) return -1;
    if (sa.active !== sb.active) return sa.active ? -1 : 1;
    if (sa.active) return (sa.days ?? Infinity) - (sb.days ?? Infinity);
    return sb.win_rate - sa.win_rate;
  });
}

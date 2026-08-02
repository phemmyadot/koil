// Portfolio daily P&L series computation. Ported verbatim from trades.html's computePnlSeries/
// replayAsOf -- pure functions taking positions/fills/marks already fetched, no DOM/fetch here.

import type { DailyMark, Fill, Position } from "../api/types";

export interface ReplayState {
  units: number;
  avgCost: number | null;
  realized: number;
  multiplier: number;
}

// Replays a position's fills up to (and including) asOfDate to reconstruct units/avg-cost/
// realized as they stood on that date -- NOT the position's current live snapshot.
export function replayAsOf(fills: Fill[], asOfDate: string): ReplayState {
  const ordered = fills
    .filter((f) => f.fill_date <= asOfDate)
    .sort((a, b) => (a.fill_date === b.fill_date ? a.id - b.id : a.fill_date < b.fill_date ? -1 : 1));
  let units = 0;
  let cost = 0;
  let realized = 0;
  const multiplier = ordered.length && ordered[0].instrument === "option" ? 100 : 1;
  for (const f of ordered) {
    const value = f.instrument === "option" ? (f.premium ?? 0) : (f.price ?? 0);
    if (f.kind === "entry") {
      cost += value * f.units * multiplier;
      units += f.units;
    } else if (f.kind === "exit" && units > 0) {
      const avgCost = cost / units;
      realized += (value - avgCost) * f.units * multiplier;
      cost -= avgCost * f.units * multiplier;
      units -= f.units;
    }
  }
  return { units, avgCost: units > 0 ? cost / units : null, realized, multiplier };
}

export interface PnlSeries {
  dates: string[];
  realized: number[];
  unrealized: number[];
}

export function computePnlSeries(
  positions: Position[],
  marksByPosition: Record<number, DailyMark[]>,
  fillsByPosition: Record<number, Fill[]>,
): PnlSeries {
  const dateSet = new Set<string>();
  for (const p of positions) for (const m of marksByPosition[p.id] ?? []) dateSet.add(m.mark_date);
  for (const p of positions) for (const f of fillsByPosition[p.id] ?? []) if (f.kind === "exit") dateSet.add(f.fill_date);
  const dates = [...dateSet].sort();
  if (!dates.length) return { dates: [], realized: [], unrealized: [] };

  const realized: number[] = [];
  const unrealized: number[] = [];
  for (const date of dates) {
    let dayRealized = 0;
    let dayUnrealized = 0;
    for (const p of positions) {
      const state = replayAsOf(fillsByPosition[p.id] ?? [], date);
      // Cumulative realized P&L as of this date -- includes every partial exit along the way,
      // not just a step-change on the date the position fully closed.
      dayRealized += state.realized;

      if (state.units <= 0 || state.avgCost == null) continue;
      const marks = marksByPosition[p.id] ?? [];
      const mark = marks.find((m) => m.mark_date === date);
      if (!mark) continue;
      const value = mark.option_value != null ? mark.option_value : mark.close_price;
      dayUnrealized += (value - state.avgCost) * state.units * state.multiplier;
    }
    realized.push(dayRealized);
    unrealized.push(dayUnrealized);
  }
  return { dates, realized, unrealized };
}

// $ P&L for one position at a given mark. Positions missing avg_cost (fully flat) contribute
// nothing.
export function positionDollarUnrealized(p: Position, valueAtMark: number, unitsRemaining: number): number {
  if (p.avg_cost == null || !unitsRemaining) return 0;
  const multiplier = p.instrument === "option" ? 100 : 1;
  return (valueAtMark - p.avg_cost) * unitsRemaining * multiplier;
}

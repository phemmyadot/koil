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
      // avgCost is already multiplier-scaled (cost accumulated as value*units*multiplier on
      // entry) -- value is a raw per-share/per-contract-unit premium, so it needs the same
      // *multiplier scaling before comparing, or realized comes out ~100x wrong for options.
      // Matches the fix applied to backend/app.py's replay_fills().
      realized += (value * multiplier - avgCost) * f.units;
      cost -= avgCost * f.units;
      units -= f.units;
    }
  }
  return { units, avgCost: units > 0 ? cost / units : null, realized, multiplier };
}

export interface ExitBreakdownRow {
  fillId: number;
  fillDate: string;
  exitReason: Fill["exit_reason"];
  units: number;
  // Per-share/per-contract exit price -- premium for options, price for spot.
  exitValue: number;
  // This exit's own realized $ and % against the position's weighted-average cost basis at the
  // moment of this exit (not the position's final avg_cost -- avg_cost is fixed once units stop
  // entering, but computing it per-exit is what makes each row's numbers independently correct
  // even mid-replay).
  realizedDollar: number;
  realizedPct: number | null;
  // 1-based count of TP exits seen so far, TP exits only -- used to label "TP 1", "TP 2", etc.
  // in fill order (oldest first). Null for non-TP exits (labeled by exit_reason directly instead).
  tpIndex: number | null;
}

// One row per exit fill on a position (TP1, TP2, ..., final close/stop/manual/expired), each
// with that specific exit's own realized $/% -- for the closed-positions table's "show every
// exit, not just the aggregate" view. Entry-only fills produce no rows (nothing was realized
// yet). Oldest exit first, matching fill order.
export function exitBreakdown(fills: Fill[]): ExitBreakdownRow[] {
  const ordered = fills
    .slice()
    .sort((a, b) => (a.fill_date === b.fill_date ? a.id - b.id : a.fill_date < b.fill_date ? -1 : 1));
  const multiplier = ordered.length && ordered[0].instrument === "option" ? 100 : 1;
  let units = 0;
  let cost = 0;
  let tpCount = 0;
  const rows: ExitBreakdownRow[] = [];
  for (const f of ordered) {
    const value = f.instrument === "option" ? (f.premium ?? 0) : (f.price ?? 0);
    if (f.kind === "entry") {
      cost += value * f.units * multiplier;
      units += f.units;
    } else if (f.kind === "exit" && units > 0) {
      const avgCost = cost / units;
      const realizedDollar = (value * multiplier - avgCost) * f.units;
      const costBasis = avgCost * f.units;
      if (f.exit_reason === "tp") tpCount += 1;
      rows.push({
        fillId: f.id,
        fillDate: f.fill_date,
        exitReason: f.exit_reason,
        units: f.units,
        exitValue: value,
        realizedDollar,
        realizedPct: costBasis ? (realizedDollar / costBasis) * 100 : null,
        tpIndex: f.exit_reason === "tp" ? tpCount : null,
      });
      cost -= avgCost * f.units;
      units -= f.units;
    }
  }
  return rows;
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
      // state.avgCost has the multiplier baked in (cost/units, where cost already includes it) --
      // unwind to per-share before comparing against value, which is always per-share.
      const avgCostPerShare = state.avgCost / state.multiplier;
      dayUnrealized += (value - avgCostPerShare) * state.units * state.multiplier;
    }
    realized.push(dayRealized);
    unrealized.push(dayUnrealized);
  }
  return { dates, realized, unrealized };
}

// $ P&L for one position at a given mark. Positions missing avg_cost (fully flat) contribute
// nothing. valueAtMark is always per-share (close_price or option_value); p.avg_cost from the
// backend has the 100x contract multiplier already baked in for options (see replay_fills), so
// it's scaled back down to per-share before comparing against valueAtMark -- multiplying by 100
// again afterward (as this used to) double-counted the multiplier. See
// docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md.
export function positionDollarUnrealized(p: Position, valueAtMark: number, unitsRemaining: number): number {
  if (p.avg_cost == null || !unitsRemaining) return 0;
  const multiplier = p.instrument === "option" ? 100 : 1;
  const avgCostPerShare = p.instrument === "option" ? p.avg_cost / 100 : p.avg_cost;
  return (valueAtMark - avgCostPerShare) * unitsRemaining * multiplier;
}

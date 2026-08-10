// Builds the copiable Markdown trades export -- see TradesPage's Export modal. Pure function
// over already-fetched data (closedFillsByPositionId is fetched once, lazily, when the export
// modal opens -- see TradesPage.tsx), no requests made from inside this file.
import type { Fill, Position, PositionsSummary } from "../api/types";
import { stratLabel } from "../constants/strategy";
import { exitBreakdown } from "./pnlSeries";
import { exitLabel, fmtMoney, fmtPct, fmtUnits } from "./format";

function summaryLine(label: string, summary: PositionsSummary | undefined): string {
  if (!summary) return `**${label}** — no data`;
  const winRate = summary.win_rate_pct != null ? `${summary.win_rate_pct}%` : "—";
  const avgReturn = summary.avg_return_pct != null ? fmtPct(summary.avg_return_pct) : "—";
  return (
    `**${label}** — Open: ${summary.open_count} · Closed: ${summary.closed_count} · ` +
    `Win rate: ${winRate} · Avg return: ${avgReturn} · ` +
    `Realized: ${fmtMoney(summary.total_realized_pnl)} · Unrealized: ${fmtMoney(summary.total_unrealized_pnl)}`
  );
}

// avg_cost already has the 100x contract multiplier baked in for options (it's the total $ cost
// per unit, not a per-share figure) -- current_price is per-share, so unrealizedPct needs the
// unwound per-share cost to compare against. Matches OptionsPositionsTable's own formulas.
function totals(p: Position, multiplier: number) {
  const perShareCost = p.avg_cost != null ? p.avg_cost / multiplier : null;
  const totalCost = p.avg_cost != null ? p.avg_cost * p.units_remaining : null;
  const currentTotal = p.current_price != null ? p.current_price * p.units_remaining * multiplier : null;
  const unrealizedDollar = totalCost != null && currentTotal != null ? currentTotal - totalCost : null;
  const unrealizedPct = p.current_price != null && perShareCost ? ((p.current_price - perShareCost) / perShareCost) * 100 : null;
  return { totalCost, currentTotal, unrealizedDollar, unrealizedPct };
}

function realizedCell(p: Position): string {
  return `${fmtMoney(p.realized_pnl)}${p.realized_pnl_pct != null ? ` / ${fmtPct(p.realized_pnl_pct)}` : ""}`;
}

// Percentage points, e.g. -18.2 -- see PositionDetailPage's own IV crush/spike thresholds (kept
// in sync manually, no shared constant since these are the only two call sites).
function ivCell(p: Position): string {
  if (p.current_iv == null || p.iv_at_entry == null) return "—";
  const changePts = (p.current_iv - p.iv_at_entry) * 100;
  const flag = changePts < -10 ? " ⚠️ crush" : changePts > 15 ? " 📈 spike" : "";
  return `${fmtPct(changePts)}${flag}`;
}

// A TP row per partial exit fill on this position, matching the live tables' own
// PartialExitRows -- no rows if the position has no exits yet, or its fills haven't been
// fetched. Column order must match openTable's header exactly: Ticker | Exit | Units |
// Strategy | Cost | Total Cost | Last | Current Total | Unrealized $ | Unrealized % [| IV Δ] |
// Realized -- an exit row has nothing to say about Strategy/Cost/Total Cost/Unrealized (those
// describe the position as a whole, not one exit), so those cells are "—"; Last/Current Total
// carry this exit's own price/total instead.
function partialExitLines(p: Position, instrument: "spot" | "option", ivColPresent: boolean, fillsByPositionId: Record<number, Fill[]>): string[] {
  if (p.units_sold <= 0) return [];
  const fills = fillsByPositionId[p.id];
  if (!fills) return [];
  const multiplier = instrument === "option" ? 100 : 1;
  const ivCellStr = ivColPresent ? "| — " : "";
  return exitBreakdown(fills).map(
    (row) =>
      `| ${p.ticker} | ${exitLabel(row.exitReason, row.tpIndex)} | ${fmtUnits(row.units)} | — | — | — | ${fmtMoney(row.exitValue)} | ` +
      `${fmtMoney(row.exitValue * row.units * multiplier)} | — | — ${ivCellStr}| ` +
      `${fmtMoney(row.realizedDollar)}${row.realizedPct != null ? ` / ${fmtPct(row.realizedPct)}` : ""} |`,
  );
}

function openTable(positions: Position[], instrument: "spot" | "option", fillsByPositionId: Record<number, Fill[]>): string {
  const rows = positions.filter((p) => p.status === "open" && p.instrument === instrument);
  const multiplier = instrument === "option" ? 100 : 1;
  const ivColPresent = instrument === "option";
  const ivCol = ivColPresent ? "| IV Δ " : "";
  const ivSep = ivColPresent ? "|---" : "";
  if (!rows.length) return "*No open positions.*";
  const header =
    `| Ticker | Exit | Units | Strategy | ${instrument === "spot" ? "Avg Cost" : "Premium"} | Total Cost | Last | Current Total | Unrealized $ | Unrealized % ${ivCol}| Realized |\n` +
    `|---|---|---|---|---|---|---|---|---|---${ivSep}|---|`;
  const lines: string[] = [];
  for (const p of rows) {
    lines.push(...partialExitLines(p, instrument, ivColPresent, fillsByPositionId));
    const { totalCost, currentTotal, unrealizedDollar, unrealizedPct } = totals(p, multiplier);
    const perShareCost = instrument === "option" && p.avg_cost != null ? p.avg_cost / multiplier : p.avg_cost;
    const ivCellStr = ivColPresent ? `| ${ivCell(p)} ` : "";
    lines.push(
      `| ${p.ticker} | — | ${fmtUnits(p.units_remaining)} | ${stratLabel(p.strategy_key)} | ${perShareCost != null ? fmtMoney(perShareCost) : "—"} | ` +
        `${totalCost != null ? fmtMoney(totalCost) : "—"} | ${p.current_price != null ? fmtMoney(p.current_price) : "—"} | ${currentTotal != null ? fmtMoney(currentTotal) : "—"} | ` +
        `${unrealizedDollar != null ? fmtMoney(unrealizedDollar) : "—"} | ${unrealizedPct != null ? fmtPct(unrealizedPct) : "—"} ${ivCellStr}| ${realizedCell(p)} |`,
    );
  }
  return [header, ...lines].join("\n");
}

// One row per exit fill (TP1, TP2, ..., the final close), matching the live tables' own
// ClosedPositionRows -- a position without fills loaded yet (shouldn't happen once
// closedFillsByPositionId has settled, but stay defensive) falls back to one aggregated row
// using the position's own totals, same shape the export used before this per-exit breakdown.
function closedTable(positions: Position[], instrument: "spot" | "option", fillsByPositionId: Record<number, Fill[]>): string {
  const positionsForInstrument = positions.filter((p) => p.status === "closed" && p.instrument === instrument);
  if (!positionsForInstrument.length) return "*No closed positions.*";
  const header =
    `| Ticker | Exit | Units | ${instrument === "spot" ? "Avg Cost" : "Premium"} | Realized $ | Realized % |\n` + `|---|---|---|---|---|---|`;
  const lines: string[] = [];
  for (const p of positionsForInstrument) {
    const fills = fillsByPositionId[p.id];
    if (!fills) {
      lines.push(
        `| ${p.ticker} | — | ${fmtUnits(p.units_sold)} | — | ${fmtMoney(p.realized_pnl)} | ${p.realized_pnl_pct != null ? fmtPct(p.realized_pnl_pct) : "—"} |`,
      );
      continue;
    }
    for (const row of exitBreakdown(fills)) {
      lines.push(
        `| ${p.ticker} | ${exitLabel(row.exitReason, row.tpIndex)} | ${fmtUnits(row.units)} | ${fmtMoney(row.exitValue)} | ` +
          `${fmtMoney(row.realizedDollar)} | ${row.realizedPct != null ? fmtPct(row.realizedPct) : "—"} |`,
      );
    }
  }
  return [header, ...lines].join("\n");
}

export function buildTradesExportMarkdown(
  spotPositions: Position[] | undefined,
  optionsPositions: Position[] | undefined,
  spotSummary: PositionsSummary | undefined,
  optionsSummary: PositionsSummary | undefined,
  // Fills for every closed position, plus every open position with a partial exit -- see
  // TradesPage.tsx's exitedPositionIds/exitedFillsByPositionId.
  fillsByPositionId: Record<number, Fill[]> = {},
): string {
  const spot = spotPositions ?? [];
  const options = optionsPositions ?? [];
  const today = new Date().toISOString().slice(0, 10);

  return [
    `# Trades Export — ${today}`,
    "",
    "## Summary",
    "",
    summaryLine("Spot", spotSummary),
    "",
    summaryLine("Options", optionsSummary),
    "",
    "## Spot — Open",
    "",
    openTable(spot, "spot", fillsByPositionId),
    "",
    "## Spot — Closed",
    "",
    closedTable(spot, "spot", fillsByPositionId),
    "",
    "## Options — Open",
    "",
    openTable(options, "option", fillsByPositionId),
    "",
    "## Options — Closed",
    "",
    closedTable(options, "option", fillsByPositionId),
    "",
  ].join("\n");
}

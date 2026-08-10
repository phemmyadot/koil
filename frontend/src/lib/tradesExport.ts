// Builds the copiable Markdown trades export -- see TradesPage's Export modal. Pure function
// over already-fetched data (closedFillsByPositionId is fetched once, lazily, when the export
// modal opens -- see TradesPage.tsx), no requests made from inside this file.
import type { Fill, Position, PositionsSummary } from "../api/types";
import { stratLabel } from "../constants/strategy";
import { exitBreakdown } from "./pnlSeries";
import { fmtMoney, fmtPct, fmtUnits } from "./format";

const EXIT_LABELS: Record<string, string> = { tp: "TP", stop: "Stop", manual: "Close", expired: "Expired" };

function exitLabel(reason: string | null, tpIndex: number | null): string {
  if (reason === "tp") return `TP ${tpIndex}`;
  return reason ? (EXIT_LABELS[reason] ?? reason) : "Close";
}

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
  const value = `${fmtMoney(p.realized_pnl)}${p.realized_pnl_pct != null ? ` / ${fmtPct(p.realized_pnl_pct)}` : ""}`;
  return p.status === "open" && p.units_sold > 0 ? `${fmtUnits(p.units_sold)} units — ${value}` : value;
}

// Percentage points, e.g. -18.2 -- see PositionDetailPage's own IV crush/spike thresholds (kept
// in sync manually, no shared constant since these are the only two call sites).
function ivCell(p: Position): string {
  if (p.current_iv == null || p.iv_at_entry == null) return "—";
  const changePts = (p.current_iv - p.iv_at_entry) * 100;
  const flag = changePts < -10 ? " ⚠️ crush" : changePts > 15 ? " 📈 spike" : "";
  return `${fmtPct(changePts)}${flag}`;
}

function openTable(positions: Position[], instrument: "spot" | "option"): string {
  const rows = positions.filter((p) => p.status === "open" && p.instrument === instrument);
  const multiplier = instrument === "option" ? 100 : 1;
  const ivCol = instrument === "option" ? "| IV Δ " : "";
  const ivSep = instrument === "option" ? "|---" : "";
  if (!rows.length) return "*No open positions.*";
  const header =
    `| Ticker | Strategy | Units | ${instrument === "spot" ? "Avg Cost" : "Premium"} | Total Cost | Last | Current Total | Unrealized $ | Unrealized % ${ivCol}| Realized |\n` +
    `|---|---|---|---|---|---|---|---|---${ivSep}|---|`;
  const lines = rows.map((p) => {
    const { totalCost, currentTotal, unrealizedDollar, unrealizedPct } = totals(p, multiplier);
    const perShareCost = instrument === "option" && p.avg_cost != null ? p.avg_cost / multiplier : p.avg_cost;
    const ivCellStr = instrument === "option" ? `| ${ivCell(p)} ` : "";
    return (
      `| ${p.ticker} | ${stratLabel(p.strategy_key)} | ${fmtUnits(p.units_remaining)} | ${perShareCost != null ? fmtMoney(perShareCost) : "—"} | ` +
      `${totalCost != null ? fmtMoney(totalCost) : "—"} | ${p.current_price != null ? fmtMoney(p.current_price) : "—"} | ${currentTotal != null ? fmtMoney(currentTotal) : "—"} | ` +
      `${unrealizedDollar != null ? fmtMoney(unrealizedDollar) : "—"} | ${unrealizedPct != null ? fmtPct(unrealizedPct) : "—"} ${ivCellStr}| ${realizedCell(p)} |`
    );
  });
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
  closedFillsByPositionId: Record<number, Fill[]> = {},
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
    openTable(spot, "spot"),
    "",
    "## Spot — Closed",
    "",
    closedTable(spot, "spot", closedFillsByPositionId),
    "",
    "## Options — Open",
    "",
    openTable(options, "option"),
    "",
    "## Options — Closed",
    "",
    closedTable(options, "option", closedFillsByPositionId),
    "",
  ].join("\n");
}

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

// Percentage points, e.g. -18.2 -- see PositionDetailPage's own IV crush/spike thresholds (kept
// in sync manually, no shared constant since these are the only two call sites).
function ivCell(p: Position): string {
  if (p.current_iv == null || p.iv_at_entry == null) return "—";
  const changePts = (p.current_iv - p.iv_at_entry) * 100;
  const flag = changePts < -10 ? " ⚠️ crush" : changePts > 15 ? " 📈 spike" : "";
  return `${fmtPct(changePts)}${flag}`;
}

// Open = still-open quantity only (units_remaining > 0), regardless of status -- a partially
// exited position (e.g. 1-of-2 sold via TP) still shows its remaining unit here; its TP row
// lives in closedTable instead. Row-level split, matching SpotPositionsTable/
// OptionsPositionsTable's own Open/Closed Exits tables.
function openTable(positions: Position[], instrument: "spot" | "option"): string {
  const rows = positions.filter((p) => p.units_remaining > 0 && p.instrument === instrument);
  const multiplier = instrument === "option" ? 100 : 1;
  const ivColPresent = instrument === "option";
  const ivCol = ivColPresent ? "| IV Δ " : "";
  const ivSep = ivColPresent ? "|---" : "";
  if (!rows.length) return "*No open positions.*";
  const header =
    `| Ticker | Strategy | Units | ${instrument === "spot" ? "Avg Cost" : "Premium"} | Total Cost | Last | Current Total | Unrealized $ | Unrealized % ${ivCol}|\n` +
    `|---|---|---|---|---|---|---|---|---${ivSep}|`;
  const lines = rows.map((p) => {
    const { totalCost, currentTotal, unrealizedDollar, unrealizedPct } = totals(p, multiplier);
    const perShareCost = instrument === "option" && p.avg_cost != null ? p.avg_cost / multiplier : p.avg_cost;
    const ivCellStr = ivColPresent ? `| ${ivCell(p)} ` : "";
    return (
      `| ${p.ticker} | ${stratLabel(p.strategy_key)} | ${fmtUnits(p.units_remaining)} | ${perShareCost != null ? fmtMoney(perShareCost) : "—"} | ` +
      `${totalCost != null ? fmtMoney(totalCost) : "—"} | ${p.current_price != null ? fmtMoney(p.current_price) : "—"} | ${currentTotal != null ? fmtMoney(currentTotal) : "—"} | ` +
      `${unrealizedDollar != null ? fmtMoney(unrealizedDollar) : "—"} | ${unrealizedPct != null ? fmtPct(unrealizedPct) : "—"} ${ivCellStr}|`
    );
  });
  return [header, ...lines].join("\n");
}

// Closed = every exit fill (TP1, TP2, Stop, Manual, Expired) across EVERY position of this
// instrument, whether that position is fully closed or still partially open -- see openTable's
// own comment. A position with no fills loaded (shouldn't happen once fillsByPositionId has
// settled for every closed/partially-exited position, but stay defensive) is silently skipped
// rather than guessed at.
function closedTable(positions: Position[], instrument: "spot" | "option", fillsByPositionId: Record<number, Fill[]>): string {
  const candidates = positions.filter((p) => p.instrument === instrument && (p.status === "closed" || p.units_sold > 0));
  const header =
    `| Ticker | Exit | Units | ${instrument === "spot" ? "Avg Cost" : "Premium"} | Realized $ | Realized % |\n` + `|---|---|---|---|---|---|`;
  const lines: string[] = [];
  for (const p of candidates) {
    const fills = fillsByPositionId[p.id];
    if (!fills) continue;
    for (const row of exitBreakdown(fills)) {
      lines.push(
        `| ${p.ticker} | ${exitLabel(row.exitReason, row.tpIndex)} | ${fmtUnits(row.units)} | ${fmtMoney(row.exitValue)} | ` +
          `${fmtMoney(row.realizedDollar)} | ${row.realizedPct != null ? fmtPct(row.realizedPct) : "—"} |`,
      );
    }
  }
  if (!lines.length) return "*No closed exits yet.*";
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
    openTable(spot, "spot"),
    "",
    "## Spot — Closed Exits",
    "",
    closedTable(spot, "spot", fillsByPositionId),
    "",
    "## Options — Open",
    "",
    openTable(options, "option"),
    "",
    "## Options — Closed Exits",
    "",
    closedTable(options, "option", fillsByPositionId),
    "",
  ].join("\n");
}

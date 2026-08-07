// Builds the copiable Markdown trades export -- see TradesPage's Export modal. Pure function
// over already-fetched data (no new requests), so it can run entirely client-side.
import type { Position, PositionsSummary } from "../api/types";
import { fmtMoney, fmtPct, fmtUnits } from "./format";

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

function openTable(positions: Position[], instrument: "spot" | "option"): string {
  const rows = positions.filter((p) => p.status === "open" && p.instrument === instrument);
  const multiplier = instrument === "option" ? 100 : 1;
  const costCol = instrument === "spot" ? "| Total Cost " : "";
  const costSep = instrument === "spot" ? "|---" : "";
  if (!rows.length) return "*No open positions.*";
  const header =
    `| Ticker | Units | ${instrument === "spot" ? "Avg Cost" : "Premium"} ${costCol}| Last | Current Total | Unrealized $ | Unrealized % | Realized |\n` +
    `|---|---|---${costSep}|---|---|---|---|---|`;
  const lines = rows.map((p) => {
    const { totalCost, currentTotal, unrealizedDollar, unrealizedPct } = totals(p, multiplier);
    const perShareCost = instrument === "option" && p.avg_cost != null ? p.avg_cost / multiplier : p.avg_cost;
    const costCell = instrument === "spot" ? `| ${totalCost != null ? fmtMoney(totalCost) : "—"} ` : "";
    return (
      `| ${p.ticker} | ${fmtUnits(p.units_remaining)} | ${perShareCost != null ? fmtMoney(perShareCost) : "—"} ${costCell}` +
      `| ${p.current_price != null ? fmtMoney(p.current_price) : "—"} | ${currentTotal != null ? fmtMoney(currentTotal) : "—"} | ` +
      `${unrealizedDollar != null ? fmtMoney(unrealizedDollar) : "—"} | ${unrealizedPct != null ? fmtPct(unrealizedPct) : "—"} | ${realizedCell(p)} |`
    );
  });
  return [header, ...lines].join("\n");
}

function closedTable(positions: Position[], instrument: "spot" | "option"): string {
  const rows = positions.filter((p) => p.status === "closed" && p.instrument === instrument);
  const multiplier = instrument === "option" ? 100 : 1;
  if (!rows.length) return "*No closed positions.*";
  const header =
    `| Ticker | Units | ${instrument === "spot" ? "Avg Cost" : "Premium"} | Realized $ | Realized % |\n` + `|---|---|---|---|---|`;
  const lines = rows.map((p) => {
    const perShareCost = instrument === "option" && p.avg_cost != null ? p.avg_cost / multiplier : p.avg_cost;
    return (
      `| ${p.ticker} | ${fmtUnits(p.units_sold)} | ${perShareCost != null ? fmtMoney(perShareCost) : "—"} | ` +
      `${fmtMoney(p.realized_pnl)} | ${p.realized_pnl_pct != null ? fmtPct(p.realized_pnl_pct) : "—"} |`
    );
  });
  return [header, ...lines].join("\n");
}

export function buildTradesExportMarkdown(
  spotPositions: Position[] | undefined,
  optionsPositions: Position[] | undefined,
  spotSummary: PositionsSummary | undefined,
  optionsSummary: PositionsSummary | undefined,
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
    closedTable(spot, "spot"),
    "",
    "## Options — Open",
    "",
    openTable(options, "option"),
    "",
    "## Options — Closed",
    "",
    closedTable(options, "option"),
    "",
  ].join("\n");
}

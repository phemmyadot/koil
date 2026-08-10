// Money/percent formatting and P&L tone. Ported from the 3 near-identical copies in
// index.html/trades.html/position.html, unified into one implementation.

export function fmtMoney(v: number): string {
  return (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2);
}

export function fmtPct(v: number): string {
  return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
}

export function fmtUnits(v: number): string {
  return v.toFixed(6).replace(/\.?0+$/, "");
}

// Resolves a real inconsistency found across the old pages: index.html treated an exact-zero
// P&L as neutral (no color); trades.html/position.html treated it as positive (green).
// Decided: zero is neutral -- a flat position hasn't won or lost anything, so coloring it green
// overstates it. "pos" | "neg" | "" (never colored at exactly zero).
export function plClass(v: number): "pos" | "neg" | "" {
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "";
}

// Display label for a fill's exit_reason -- "manual" reads as "Close" everywhere a fill/exit is
// shown to the user (the closed-positions tables, the export, and the Fills table on the
// position detail page), not the raw stored value. tpIndex numbers TP exits in order ("TP 1",
// "TP 2", ...) when the caller has it (see lib/pnlSeries.ts's exitBreakdown); omit it (or pass
// null) for a bare "TP" label, e.g. when labeling a single fill row with no breakdown context.
const EXIT_LABELS: Record<string, string> = { tp: "TP", stop: "Stop", manual: "Close", expired: "Expired" };

export function exitLabel(reason: string | null, tpIndex: number | null = null): string {
  if (reason === "tp") return tpIndex != null ? `TP ${tpIndex}` : "TP";
  return reason ? (EXIT_LABELS[reason] ?? reason) : "Close";
}

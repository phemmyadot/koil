import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePositions, usePositionsSummary, usePnlSeries } from "../hooks/usePositions";
import { useTickers } from "../hooks/useTickers";
import { addFill, cancelPosition, listFills } from "../api/positions";
import type { ExitReason, Fill, PositionsSummary } from "../api/types";
import { StatBox } from "../components/atoms/StatBox";
import { SpotPositionsTable } from "../components/organisms/SpotPositionsTable";
import { OptionsPositionsTable } from "../components/organisms/OptionsPositionsTable";
import { PnlChart } from "../components/organisms/PnlChart";
import { TradesExportModal } from "../components/organisms/TradesExportModal";
import { fmtMoney, fmtPct } from "../lib/format";
import { isClosedToday, todayIsoDate } from "../lib/dates";
import { buildTradesExportMarkdown } from "../lib/tradesExport";
import "./TradesPage.css";

function SummaryRow({ label, summary }: { label: string; summary: PositionsSummary | undefined }) {
  return (
    <div className="trades-summary-grid">
      <StatBox label={`${label} — Open`} value={summary?.open_count ?? 0} />
      <StatBox label="Closed" value={summary?.closed_count ?? 0} />
      <StatBox label="Win rate" value={summary?.win_rate_pct != null ? `${summary.win_rate_pct}%` : "—"} />
      <StatBox
        label="Avg return"
        value={summary?.avg_return_pct != null ? fmtPct(summary.avg_return_pct) : "—"}
        tone={summary?.avg_return_pct ?? undefined}
      />
      <StatBox
        label="Total unrealized"
        value={summary?.total_unrealized_pnl != null ? fmtMoney(summary.total_unrealized_pnl) : "—"}
        tone={summary?.total_unrealized_pnl ?? undefined}
      />
      <StatBox
        label="Total realized"
        value={summary?.total_realized_pnl != null ? fmtMoney(summary.total_realized_pnl) : "—"}
        tone={summary?.total_realized_pnl ?? undefined}
      />
    </div>
  );
}

export function TradesPage() {
  // All spot/options combos fetched together on mount -- the tab bar below only toggles which
  // already-fetched summary/chart/table is visible, it never triggers a new request.
  const { data: spotPositions } = usePositions(undefined, "spot");
  const { data: optionsPositions } = usePositions(undefined, "options");
  const { data: spotSummary } = usePositionsSummary("spot");
  const { data: optionsSummary } = usePositionsSummary("options");
  const { data: spotPnlSeries } = usePnlSeries("spot");
  const { data: optionsPnlSeries } = usePnlSeries("options");
  const queryClient = useQueryClient();

  const [typeFilter, setTypeFilter] = useState<"spot" | "options">("spot");
  // Only toggles the Open table (remaining-quantity rows) -- the Closed Exits table below it
  // always shows every exit fill regardless, since a completed TP/Stop/Manual/Expired exit
  // belongs there even on a position that's still partially open. See SpotPositionsTable/
  // OptionsPositionsTable's own ClosedExitsTable.
  const [showOpen, setShowOpen] = useState(true);
  const [showExport, setShowExport] = useState(false);

  // Only fetched once the export modal is actually opened -- the export's per-exit breakdown
  // needs fills for every position with at least one exit (closed, or open with a partial
  // exit), which the tables/summary above don't otherwise load.
  const exitedPositionIds = [...(spotPositions ?? []), ...(optionsPositions ?? [])]
    .filter((p) => p.status === "closed" || p.units_sold > 0)
    .map((p) => p.id);
  const { data: exitedFillsByPositionId, isLoading: exitedFillsLoading } = useQuery({
    queryKey: ["trades-export-fills", exitedPositionIds],
    queryFn: async () => {
      const entries = await Promise.all(exitedPositionIds.map(async (id) => [id, await listFills(id)] as const));
      return Object.fromEntries(entries) as Record<number, Fill[]>;
    },
    enabled: showExport && exitedPositionIds.length > 0,
  });
  const exportReady = !showExport || exitedPositionIds.length === 0 || !exitedFillsLoading;

  // Pre-Breakout Summary section of the export: every ticker currently held (any instrument,
  // units_remaining > 0) UNION every ticker closed today (local time) -- deduplicated. prebreak
  // itself comes from the ticker universe payload (useTickers), same source PrebreakChips/
  // StrategyDetailModal already read it from elsewhere in the app.
  const { data: tickersData } = useTickers();
  const allPositions = [...(spotPositions ?? []), ...(optionsPositions ?? [])];
  const prebreakTickerSet = new Set<string>();
  for (const p of allPositions) {
    if (p.units_remaining > 0) prebreakTickerSet.add(p.ticker);
    if (p.status === "closed" && p.closed_at && isClosedToday(p.closed_at)) prebreakTickerSet.add(p.ticker);
  }
  const prebreakTickers = [...prebreakTickerSet];
  const prebreakByTicker = Object.fromEntries(
    prebreakTickers.map((ticker) => [ticker, tickersData?.tickers.find((t) => t.ticker === ticker)?.prebreak ?? null]),
  );

  const positions = typeFilter === "spot" ? spotPositions : optionsPositions;
  const summary = typeFilter === "spot" ? spotSummary : optionsSummary;
  const pnlSeries = typeFilter === "spot" ? spotPnlSeries : optionsPnlSeries;
  const positionIds = positions?.map((p) => p.id) ?? [];

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: ["positions"] });
    for (const id of positionIds) queryClient.invalidateQueries({ queryKey: ["position", id] });
  }

  // `amount` is the exit price (spot) or exit option price (option) -- routed to the right
  // field here since options carry their price in premium, never price. See
  // docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md. lastFill is fetched
  // by the table row itself, on demand when the Exit form opens.
  async function handleExit(positionId: number, lastFill: Fill, amount: number, units: number, exitReason: ExitReason) {
    const position = positions?.find((p) => p.id === positionId);
    if (!position) return;
    await addFill(positionId, {
      kind: "exit",
      instrument: position.instrument,
      strategy_key: lastFill.strategy_key,
      signal_date: lastFill.signal_date,
      fill_date: todayIsoDate(),
      units,
      exit_reason: exitReason,
      // opt_side/opt_type/strike/expiry_date are required on every option fill, entry or exit
      // (see backend's _FILL_OPTION_REQUIRED) -- carried over from the position's own open lot
      // rather than re-collected here, since this form only asks for exit price/units/reason.
      ...(position.instrument === "option"
        ? {
            premium: amount,
            opt_side: lastFill.opt_side ?? undefined,
            opt_type: lastFill.opt_type ?? undefined,
            strike: lastFill.strike ?? undefined,
            expiry_date: lastFill.expiry_date ?? undefined,
          }
        : { price: amount }),
    });
    invalidateAll();
  }

  async function handleCancel(positionId: number) {
    await cancelPosition(positionId);
    invalidateAll();
  }

  return (
    <div className="trades-page">
      <div className="trades-header">
        <h1>Trades</h1>
        <button type="button" className="small-btn" onClick={() => setShowExport(true)}>
          Export
        </button>
      </div>

      {showExport && (exportReady ? (
        <TradesExportModal
          markdown={buildTradesExportMarkdown(
            spotPositions,
            optionsPositions,
            spotSummary,
            optionsSummary,
            exitedFillsByPositionId ?? {},
            prebreakTickers,
            prebreakByTicker,
            tickersData?.asof ?? null,
          )}
          onClose={() => setShowExport(false)}
        />
      ) : (
        <p className="empty">Preparing export…</p>
      ))}

      <div className="trades-tabs">
        <button
          type="button"
          className={typeFilter === "spot" ? "active" : ""}
          onClick={() => setTypeFilter("spot")}
        >
          Spot
        </button>
        <button
          type="button"
          className={typeFilter === "options" ? "active" : ""}
          onClick={() => setTypeFilter("options")}
        >
          Options
        </button>
      </div>

      <SummaryRow label={typeFilter === "spot" ? "Spot" : "Options"} summary={summary} />

      <h2>Daily P&amp;L</h2>
      <PnlChart series={pnlSeries ?? { dates: [], realized: [], unrealized: [] }} />

      <div className="trades-filterbar">
        <label>
          <input type="checkbox" checked={showOpen} onChange={(e) => setShowOpen(e.target.checked)} />
          Show open positions
        </label>
      </div>

      <h2>Positions</h2>
      {typeFilter === "spot" ? (
        <SpotPositionsTable positions={positions ?? []} onExit={handleExit} onCancel={handleCancel} showOpen={showOpen} />
      ) : (
        <OptionsPositionsTable positions={positions ?? []} onExit={handleExit} onCancel={handleCancel} showOpen={showOpen} />
      )}
    </div>
  );
}

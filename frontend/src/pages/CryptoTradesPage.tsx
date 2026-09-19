import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { usePositions, usePositionsSummary, usePnlSeries } from "../hooks/usePositions";
import { addFill, cancelPosition } from "../api/positions";
import type { ExitReason, Fill } from "../api/types";
import { SpotPositionsTable } from "../components/organisms/SpotPositionsTable";
import { PnlChart } from "../components/organisms/PnlChart";
import { PositionsSummaryRow } from "../components/molecules/PositionsSummaryRow";
import { todayIsoDate } from "../lib/dates";
import "./TradesPage.css";

// Mirrors TradesPage.tsx (equity), scoped to market=crypto -- crypto trades are always spot (no
// options market for crypto in this app), so there's no Spot/Options tab here, just the one
// table. No export here either (equity's Export button is a separate, additive feature this
// mirror doesn't need to replicate).
export function CryptoTradesPage() {
  const { data: positions } = usePositions(undefined, "spot", "crypto");
  const { data: summary } = usePositionsSummary("spot", "crypto");
  const { data: pnlSeries } = usePnlSeries("spot", "crypto");
  const queryClient = useQueryClient();

  const [showOpen, setShowOpen] = useState(true);

  const positionIds = positions?.map((p) => p.id) ?? [];

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: ["positions"] });
    for (const id of positionIds) queryClient.invalidateQueries({ queryKey: ["position", id] });
  }

  async function handleExit(positionId: number, lastFill: Fill, amount: number, units: number, exitReason: ExitReason) {
    const position = positions?.find((p) => p.id === positionId);
    if (!position) return;
    await addFill(positionId, {
      kind: "exit",
      instrument: "spot",
      strategy_key: lastFill.strategy_key,
      signal_date: lastFill.signal_date,
      fill_date: todayIsoDate(),
      units,
      exit_reason: exitReason,
      price: amount,
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
        <h1>Crypto Trades</h1>
      </div>

      <PositionsSummaryRow label="Crypto" summary={summary} />

      <h2>Daily P&amp;L</h2>
      <PnlChart series={pnlSeries ?? { dates: [], realized: [], unrealized: [] }} />

      <div className="trades-filterbar">
        <label>
          <input type="checkbox" checked={showOpen} onChange={(e) => setShowOpen(e.target.checked)} />
          Show open positions
        </label>
      </div>

      <h2>Positions</h2>
      <SpotPositionsTable
        positions={positions ?? []}
        onExit={handleExit}
        onCancel={handleCancel}
        showOpen={showOpen}
        basePath="/crypto/trades"
      />
    </div>
  );
}

import { useMemo, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { usePositions, usePositionsSummary } from "../hooks/usePositions";
import { addFill, cancelPosition, getMarks, listFills } from "../api/positions";
import type { DailyMark, ExitReason, Fill } from "../api/types";
import { StatBox } from "../components/atoms/StatBox";
import { PositionsTable } from "../components/organisms/PositionsTable";
import { PnlChart } from "../components/organisms/PnlChart";
import { fmtPct } from "../lib/format";
import { computePnlSeries } from "../lib/pnlSeries";
import { todayIsoDate } from "../lib/dates";
import "./TradesPage.css";

export function TradesPage() {
  const { data: positions } = usePositions();
  const { data: summary } = usePositionsSummary();
  const queryClient = useQueryClient();

  const [statusFilter, setStatusFilter] = useState<"" | "open" | "closed">("open");
  const [tickerFilter, setTickerFilter] = useState("");

  const positionIds = positions?.map((p) => p.id) ?? [];

  const marksQueries = useQueries({
    queries: positionIds.map((id) => ({ queryKey: ["position", id, "marks"], queryFn: () => getMarks(id) })),
  });
  const fillsQueries = useQueries({
    queries: positionIds.map((id) => ({ queryKey: ["position", id, "fills"], queryFn: () => listFills(id) })),
  });

  const marksUpdatedKey = marksQueries.map((q) => q.dataUpdatedAt).join(",");
  const fillsUpdatedKey = fillsQueries.map((q) => q.dataUpdatedAt).join(",");

  const marksByPosition = useMemo(() => {
    const map: Record<number, DailyMark[]> = {};
    positionIds.forEach((id, i) => {
      map[id] = marksQueries[i]?.data ?? [];
    });
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, marksUpdatedKey]);

  const fillsByPosition = useMemo(() => {
    const map: Record<number, Fill[]> = {};
    positionIds.forEach((id, i) => {
      map[id] = fillsQueries[i]?.data ?? [];
    });
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, fillsUpdatedKey]);

  const pnlSeries = useMemo(
    () => computePnlSeries(positions ?? [], marksByPosition, fillsByPosition),
    [positions, marksByPosition, fillsByPosition],
  );

  const tickers = useMemo(() => [...new Set((positions ?? []).map((p) => p.ticker))].sort(), [positions]);

  const filtered = (positions ?? []).filter(
    (p) => (!statusFilter || p.status === statusFilter) && (!tickerFilter || p.ticker === tickerFilter),
  );

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: ["positions"] });
    for (const id of positionIds) queryClient.invalidateQueries({ queryKey: ["position", id] });
  }

  async function handleExit(positionId: number, price: number, units: number, exitReason: ExitReason) {
    const fills = fillsByPosition[positionId] ?? [];
    const lastFill = fills[fills.length - 1];
    const position = positions?.find((p) => p.id === positionId);
    if (!position || !lastFill) return;
    await addFill(positionId, {
      kind: "exit",
      instrument: position.instrument,
      strategy_key: lastFill.strategy_key,
      signal_date: lastFill.signal_date,
      fill_date: todayIsoDate(),
      price,
      units,
      exit_reason: exitReason,
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
      </div>

      <div className="trades-summary-grid">
        <StatBox label="Open" value={summary?.open_count ?? 0} />
        <StatBox label="Closed" value={summary?.closed_count ?? 0} />
        <StatBox label="Win rate" value={summary?.win_rate_pct != null ? `${summary.win_rate_pct}%` : "—"} />
        <StatBox
          label="Avg return"
          value={summary?.avg_return_pct != null ? fmtPct(summary.avg_return_pct) : "—"}
          tone={summary?.avg_return_pct ?? undefined}
        />
      </div>

      <h2>Daily P&amp;L</h2>
      <PnlChart series={pnlSeries} />

      <div className="trades-filterbar">
        <label>Status</label>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as "" | "open" | "closed")}>
          <option value="">All</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </select>
        <label>Ticker</label>
        <select value={tickerFilter} onChange={(e) => setTickerFilter(e.target.value)}>
          <option value="">All</option>
          {tickers.map((tk) => (
            <option key={tk} value={tk}>
              {tk}
            </option>
          ))}
        </select>
      </div>

      <h2>Positions</h2>
      <PositionsTable positions={filtered} marksByPosition={marksByPosition} onExit={handleExit} onCancel={handleCancel} />
    </div>
  );
}

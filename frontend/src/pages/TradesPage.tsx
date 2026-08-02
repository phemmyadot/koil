import { useMemo, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { usePositions, usePositionsSummary } from "../hooks/usePositions";
import { addFill, cancelPosition, getMarks, listFills } from "../api/positions";
import type { DailyMark, ExitReason, Fill } from "../api/types";
import { StatBox } from "../components/atoms/StatBox";
import { PositionsTable } from "../components/organisms/PositionsTable";
import { PnlChart } from "../components/organisms/PnlChart";
import { fmtMoney, fmtPct } from "../lib/format";
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

  const totalUnrealized = useMemo(() => {
    return (positions ?? [])
      .filter((p) => p.status === "open" && p.avg_cost != null)
      .reduce((sum, p) => {
        const marks = marksByPosition[p.id] ?? [];
        if (!marks.length) return sum;
        const isOption = p.instrument === "option";
        const hasOptionValues = isOption && marks[0].option_value != null;
        const last = hasOptionValues ? (marks[marks.length - 1].option_value as number) : marks[marks.length - 1].close_price;
        const avgCostPerShare = isOption ? (p.avg_cost as number) / 100 : (p.avg_cost as number);
        const multiplier = isOption ? 100 : 1;
        return sum + (last - avgCostPerShare) * p.units_remaining * multiplier;
      }, 0);
  }, [positions, marksByPosition]);

  const filtered = (positions ?? []).filter(
    (p) => (!statusFilter || p.status === statusFilter) && (!tickerFilter || p.ticker === tickerFilter),
  );

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: ["positions"] });
    for (const id of positionIds) queryClient.invalidateQueries({ queryKey: ["position", id] });
  }

  // `amount` is the exit price (spot) or exit option price (option) -- PositionsTable's single
  // generic input, routed to the right field here since options carry their price in premium,
  // never price. See docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md.
  async function handleExit(positionId: number, amount: number, units: number, exitReason: ExitReason) {
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
        <StatBox label="Total unrealized" value={fmtMoney(totalUnrealized)} tone={totalUnrealized} />
        <StatBox
          label="Total realized"
          value={summary?.total_realized_pnl != null ? fmtMoney(summary.total_realized_pnl) : "—"}
          tone={summary?.total_realized_pnl ?? undefined}
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

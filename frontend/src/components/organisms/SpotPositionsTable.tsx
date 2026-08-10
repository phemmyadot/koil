import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listFills } from "../../api/positions";
import type { ExitReason, Fill, Position } from "../../api/types";
import { exitLabel, fmtMoney, fmtPct, fmtUnits, plClass } from "../../lib/format";
import { exitBreakdown } from "../../lib/pnlSeries";
import { StrategyCellLink } from "../molecules/StrategyCellLink";
import "./PositionsTable.css";

export interface SpotPositionsTableProps {
  positions: Position[];
  onExit: (positionId: number, lastFill: Fill, price: number, units: number, exitReason: ExitReason) => Promise<void>;
  onCancel: (positionId: number) => Promise<void>;
}

// A completed exit fill belongs in the Closed table regardless of whether its parent position
// still has units open (e.g. a partial TP on a position that's 1-of-2 sold) -- this is a
// row-level split, not a position-level one. Fetches fills for every position with at least one
// exit fill (units_sold > 0 OR status closed, covering the "closed with a single full exit"
// case where units_sold still equals units entered).
function ClosedExitsTable({ positions }: { positions: Position[] }) {
  const candidates = positions.filter((p) => p.status === "closed" || p.units_sold > 0);
  const fillsQueries = useQuery({
    queryKey: ["spot-closed-exits-fills", candidates.map((p) => p.id)],
    queryFn: async () => {
      const entries = await Promise.all(candidates.map(async (p) => [p.id, await listFills(p.id)] as const));
      return Object.fromEntries(entries) as Record<number, Fill[]>;
    },
    enabled: candidates.length > 0,
  });

  const rows: { p: Position; row: ReturnType<typeof exitBreakdown>[number] }[] = [];
  if (fillsQueries.data) {
    for (const p of candidates) {
      const fills = fillsQueries.data[p.id];
      if (!fills) continue;
      for (const row of exitBreakdown(fills)) rows.push({ p, row });
    }
  }

  return (
    <div className="table-scroll">
      <table className="postable">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Exit</th>
            <th>Units</th>
            <th>Price</th>
            <th>Total</th>
            <th>Realized $ / %</th>
          </tr>
        </thead>
        <tbody>
          {candidates.length === 0 ? (
            <tr>
              <td colSpan={6} className="empty">
                No closed exits yet.
              </td>
            </tr>
          ) : fillsQueries.isLoading ? (
            <tr>
              <td colSpan={6}>Loading…</td>
            </tr>
          ) : (
            rows.map(({ p, row }) => (
              <tr key={row.fillId}>
                <td>
                  <Link className="tk-link" to={`/trades/${p.id}`}>
                    {p.ticker}
                  </Link>
                </td>
                <td>
                  <span className="status-tag closed">{exitLabel(row.exitReason, row.tpIndex)}</span>
                </td>
                <td>{fmtUnits(row.units)}</td>
                <td>{fmtMoney(row.exitValue)}</td>
                <td>{fmtMoney(row.exitValue * row.units)}</td>
                <td>
                  <b className={plClass(row.realizedDollar)}>
                    {fmtMoney(row.realizedDollar)}
                    {row.realizedPct != null && (
                      <>
                        <br />
                        {fmtPct(row.realizedPct)}
                      </>
                    )}
                  </b>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function PositionRow({
  p,
  onExit,
  onCancel,
}: {
  p: Position;
  onExit: SpotPositionsTableProps["onExit"];
  onCancel: SpotPositionsTableProps["onCancel"];
}) {
  const [exitOpen, setExitOpen] = useState(false);
  const [exitPrice, setExitPrice] = useState("");
  const [exitUnits, setExitUnits] = useState(String(p.units_remaining));
  const [exitReason, setExitReason] = useState<ExitReason>("tp");
  const [submitting, setSubmitting] = useState(false);

  // Fetched only when the Exit form is actually opened, not eagerly for every row on page
  // load -- handleExit needs the position's last fill (strategy_key) to carry over onto the
  // new exit fill.
  const fillsQuery = useQuery({
    queryKey: ["position", p.id, "fills"],
    queryFn: () => listFills(p.id),
    enabled: exitOpen,
  });
  const lastFill = fillsQuery.data?.[fillsQuery.data.length - 1];

  const totalCost = p.avg_cost != null ? p.avg_cost * p.units_remaining : null;
  const currentTotal = p.current_price != null ? p.current_price * p.units_remaining : null;
  const unrealizedDollar = totalCost != null && currentTotal != null ? currentTotal - totalCost : null;
  const unrealizedPct = p.current_price != null && p.avg_cost ? ((p.current_price - p.avg_cost) / p.avg_cost) * 100 : 0;

  async function submitExit() {
    const price = parseFloat(exitPrice);
    const units = parseFloat(exitUnits);
    if (!Number.isFinite(price)) {
      window.alert("Enter a valid exit price");
      return;
    }
    if (!Number.isFinite(units) || units <= 0) {
      window.alert("Enter valid units to exit");
      return;
    }
    if (!lastFill) {
      window.alert("Still loading this position's fill history -- try again in a moment.");
      return;
    }
    setSubmitting(true);
    try {
      await onExit(p.id, lastFill, price, units, exitReason);
      setExitOpen(false);
    } catch (e) {
      window.alert(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCancel() {
    if (!window.confirm("Cancel this position? This permanently deletes it and all its fills, and cannot be undone.")) return;
    await onCancel(p.id);
  }

  return (
    <>
      <tr>
        <td>
          <Link className="tk-link" to={`/trades/${p.id}`}>
            {p.ticker}
          </Link>
        </td>
        <td>
          <span className={`status-tag ${p.status}`}>{p.status}</span>
        </td>
        <td>
          <StrategyCellLink ticker={p.ticker} strategyKey={p.strategy_key} />
        </td>
        <td>{fmtUnits(p.units_remaining)}</td>
        <td>{p.avg_cost != null ? fmtMoney(p.avg_cost) : "—"}</td>
        <td>{totalCost != null ? fmtMoney(totalCost) : "—"}</td>
        <td>{p.current_price != null ? fmtMoney(p.current_price) : "—"}</td>
        <td>{currentTotal != null ? fmtMoney(currentTotal) : "—"}</td>
        <td>
          {unrealizedDollar != null ? (
            <b className={plClass(unrealizedDollar)}>
              {fmtMoney(unrealizedDollar)}
              <br />
              {fmtPct(unrealizedPct)}
            </b>
          ) : (
            "—"
          )}
        </td>
        <td className="actions-cell">
          <button type="button" className="small-btn" onClick={() => setExitOpen((v) => !v)}>
            Exit
          </button>{" "}
          <button type="button" className="small-btn danger" onClick={handleCancel}>
            Cancel
          </button>
        </td>
      </tr>
      {exitOpen && (
        <tr>
          <td colSpan={10}>
            {fillsQuery.isLoading ? (
              <div className="exit-form">Loading…</div>
            ) : (
              <div className="exit-form">
                <input
                  type="number"
                  step={0.01}
                  placeholder="Exit price"
                  value={exitPrice}
                  onChange={(e) => setExitPrice(e.target.value)}
                />
                <input type="number" step={1} placeholder="Units" value={exitUnits} onChange={(e) => setExitUnits(e.target.value)} />
                <select value={exitReason} onChange={(e) => setExitReason(e.target.value as ExitReason)}>
                  <option value="tp">TP</option>
                  <option value="stop">Stop</option>
                  <option value="manual">Manual</option>
                </select>
                <button type="button" className="small-btn danger" disabled={submitting} onClick={submitExit}>
                  Confirm exit
                </button>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export function SpotPositionsTable({
  positions,
  onExit,
  onCancel,
  showOpen = true,
}: SpotPositionsTableProps & { showOpen?: boolean }) {
  const openRows = positions.filter((p) => p.units_remaining > 0);

  return (
    <>
      {showOpen && (
        <div className="table-scroll">
          <table className="postable">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Status</th>
                <th>Strategy</th>
                <th>Units</th>
                <th>Avg Cost</th>
                <th>Total Cost</th>
                <th>Last</th>
                <th>Current Total</th>
                <th>Unrealized $ / %</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {openRows.length === 0 ? (
                <tr>
                  <td colSpan={10} className="empty">
                    No open spot positions.
                  </td>
                </tr>
              ) : (
                openRows.map((p) => <PositionRow key={p.id} p={p} onExit={onExit} onCancel={onCancel} />)
              )}
            </tbody>
          </table>
        </div>
      )}

      <h2>Closed Exits</h2>
      <ClosedExitsTable positions={positions} />
    </>
  );
}

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listFills } from "../../api/positions";
import type { ExitReason, Fill, Position } from "../../api/types";
import { fmtMoney, fmtPct, fmtUnits, plClass } from "../../lib/format";
import "./PositionsTable.css";

export interface PositionsTableProps {
  positions: Position[];
  onExit: (positionId: number, lastFill: Fill, price: number, units: number, exitReason: ExitReason) => Promise<void>;
  onCancel: (positionId: number) => Promise<void>;
}

function PositionRow({
  p,
  onExit,
  onCancel,
}: {
  p: Position;
  onExit: PositionsTableProps["onExit"];
  onCancel: PositionsTableProps["onCancel"];
}) {
  const [exitOpen, setExitOpen] = useState(false);
  const [exitPrice, setExitPrice] = useState("");
  const [exitUnits, setExitUnits] = useState(String(p.units_remaining));
  const [exitReason, setExitReason] = useState<ExitReason>("tp");
  const [submitting, setSubmitting] = useState(false);

  // Fetched only when the Exit form is actually opened, not eagerly for every row on page
  // load -- handleExit needs the position's last fill (strategy_key/option contract terms) to
  // carry over onto the new exit fill.
  const fillsQuery = useQuery({
    queryKey: ["position", p.id, "fills"],
    queryFn: () => listFills(p.id),
    enabled: exitOpen,
  });
  const lastFill = fillsQuery.data?.[fillsQuery.data.length - 1];

  const isOption = p.instrument === "option";
  const isOpen = p.status === "open";
  // current_price is spot-only (see Position type) -- an option's avg_cost isn't comparable to
  // a stock price without re-pricing the contract, so unrealized is left unavailable for options.
  const last = !isOption ? p.current_price : null;
  const pct = last != null && p.avg_cost ? ((last - p.avg_cost) / p.avg_cost) * 100 : 0;

  async function submitExit() {
    const price = parseFloat(exitPrice);
    const units = parseFloat(exitUnits);
    if (!Number.isFinite(price)) {
      window.alert(isOption ? "Enter a valid exit option price" : "Enter a valid exit price");
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
        <td>{isOption ? "Option" : "Spot"}</td>
        <td>{fmtUnits(p.units_remaining)}</td>
        <td>{p.avg_cost != null ? fmtMoney(p.avg_cost) : "—"}</td>
        <td>{fmtMoney(p.tp_price)}</td>
        <td>{fmtMoney(p.stop_price)}</td>
        <td>
          {last != null ? (
            <b className={plClass(pct)}>
              {fmtMoney(last)}
              <br />
              {fmtPct(pct)}
            </b>
          ) : (
            "—"
          )}
        </td>
        <td>
          <b className={plClass(p.realized_pnl)}>{fmtMoney(p.realized_pnl)}</b>
        </td>
        <td className="actions-cell">
          {isOpen && (
            <>
              <button type="button" className="small-btn" onClick={() => setExitOpen((v) => !v)}>
                Exit
              </button>{" "}
              <button type="button" className="small-btn danger" onClick={handleCancel}>
                Cancel
              </button>
            </>
          )}
        </td>
      </tr>
      {isOpen && exitOpen && (
        <tr>
          <td colSpan={10}>
            {fillsQuery.isLoading ? (
              <div className="exit-form">Loading…</div>
            ) : (
              <div className="exit-form">
                <input
                  type="number"
                  step={0.01}
                  placeholder={isOption ? "Exit option price" : "Exit price"}
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

export function PositionsTable({ positions, onExit, onCancel }: PositionsTableProps) {
  if (!positions.length) return <p className="empty">No positions match this filter.</p>;
  return (
    <div className="table-scroll">
      <table className="postable">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Status</th>
            <th>Instrument</th>
            <th>Units</th>
            <th>Avg Cost</th>
            <th>TP</th>
            <th>Stop</th>
            <th>Last / Unrealized %</th>
            <th>Realized $</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <PositionRow key={p.id} p={p} onExit={onExit} onCancel={onCancel} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

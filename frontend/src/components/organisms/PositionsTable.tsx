import { useState } from "react";
import { Link } from "react-router-dom";
import type { DailyMark, ExitReason, Position } from "../../api/types";
import { fmtMoney, fmtPct, plClass } from "../../lib/format";
import { sparklinePath } from "../../lib/sparkline";
import "./PositionsTable.css";

export interface PositionsTableProps {
  positions: Position[];
  marksByPosition: Record<number, DailyMark[]>;
  onExit: (positionId: number, price: number, units: number, exitReason: ExitReason) => Promise<void>;
  onCancel: (positionId: number) => Promise<void>;
}

function PositionRow({
  p,
  marks,
  onExit,
  onCancel,
}: {
  p: Position;
  marks: DailyMark[];
  onExit: PositionsTableProps["onExit"];
  onCancel: PositionsTableProps["onCancel"];
}) {
  const [exitOpen, setExitOpen] = useState(false);
  const [exitPrice, setExitPrice] = useState("");
  const [exitUnits, setExitUnits] = useState(String(p.units_remaining));
  const [exitReason, setExitReason] = useState<ExitReason>("tp");
  const [submitting, setSubmitting] = useState(false);

  const isOption = p.instrument === "option";
  const isOpen = p.status === "open";
  const hasOptionValues = isOption && marks.length > 0 && marks[0].option_value != null;
  const values = marks.map((m) => (hasOptionValues ? (m.option_value as number) : m.close_price));
  const path = sparklinePath(values, 130, 32, 3);
  const last = marks.length ? values[values.length - 1] : (p.avg_cost ?? 0);
  const pct = p.avg_cost ? ((last - p.avg_cost) / p.avg_cost) * 100 : 0;

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
    setSubmitting(true);
    try {
      await onExit(p.id, price, units, exitReason);
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
        <td>{p.units_remaining}</td>
        <td>{p.avg_cost != null ? fmtMoney(p.avg_cost) : "—"}</td>
        <td>{fmtMoney(p.tp_price)}</td>
        <td>{fmtMoney(p.stop_price)}</td>
        <td>
          <b className={plClass(pct)}>
            {fmtMoney(last)}
            <br />
            {fmtPct(pct)}
          </b>
        </td>
        <td>
          <b className={plClass(p.realized_pnl)}>{fmtMoney(p.realized_pnl)}</b>
        </td>
        <td className="spark-cell">
          <svg viewBox="0 0 130 32">
            <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.4} />
          </svg>
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
          <td colSpan={11}>
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
          </td>
        </tr>
      )}
    </>
  );
}

export function PositionsTable({ positions, marksByPosition, onExit, onCancel }: PositionsTableProps) {
  if (!positions.length) return <p className="empty">No positions match this filter.</p>;
  return (
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
          <th>Chart</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <PositionRow key={p.id} p={p} marks={marksByPosition[p.id] ?? []} onExit={onExit} onCancel={onCancel} />
        ))}
      </tbody>
    </table>
  );
}

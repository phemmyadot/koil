import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listFills } from "../../api/positions";
import type { ExitReason, Fill, Position } from "../../api/types";
import { fmtMoney, fmtPct, fmtUnits, plClass } from "../../lib/format";
import "./PositionsTable.css";

export interface OptionsPositionsTableProps {
  positions: Position[];
  onExit: (positionId: number, lastFill: Fill, premium: number, units: number, exitReason: ExitReason) => Promise<void>;
  onCancel: (positionId: number) => Promise<void>;
}

function PositionRow({
  p,
  onExit,
  onCancel,
}: {
  p: Position;
  onExit: OptionsPositionsTableProps["onExit"];
  onCancel: OptionsPositionsTableProps["onCancel"];
}) {
  const [exitOpen, setExitOpen] = useState(false);
  const [exitPremium, setExitPremium] = useState("");
  const [exitUnits, setExitUnits] = useState(String(p.units_remaining));
  const [exitReason, setExitReason] = useState<ExitReason>("tp");
  const [submitting, setSubmitting] = useState(false);

  // Fetched only when the Exit form is actually opened -- handleExit needs the position's last
  // fill (strategy_key, opt_side/opt_type/strike/expiry_date -- required on every option fill).
  const fillsQuery = useQuery({
    queryKey: ["position", p.id, "fills"],
    queryFn: () => listFills(p.id),
    enabled: exitOpen,
  });
  const lastFill = fillsQuery.data?.[fillsQuery.data.length - 1];

  const isOpen = p.status === "open";
  // avg_cost has the 100x contract multiplier baked in (see replay_fills) -- unwind to
  // per-share premium before comparing against current_price, which is the modeled per-share
  // option value for option positions (backend's _position_with_state).
  const premium = p.avg_cost != null ? p.avg_cost / 100 : null;
  const pct = p.current_price != null && premium ? ((p.current_price - premium) / premium) * 100 : 0;

  async function submitExit() {
    const premium = parseFloat(exitPremium);
    const units = parseFloat(exitUnits);
    if (!Number.isFinite(premium)) {
      window.alert("Enter a valid exit option price");
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
      await onExit(p.id, lastFill, premium, units, exitReason);
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
        <td>{fmtUnits(p.units_remaining)}</td>
        <td>{premium != null ? fmtMoney(premium) : "—"}</td>
        <td>{fmtMoney(p.tp_price)}</td>
        <td>{fmtMoney(p.stop_price)}</td>
        <td>
          {p.current_price != null ? (
            <b className={plClass(pct)}>
              {fmtMoney(p.current_price)}
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
          <td colSpan={9}>
            {fillsQuery.isLoading ? (
              <div className="exit-form">Loading…</div>
            ) : (
              <div className="exit-form">
                <input
                  type="number"
                  step={0.01}
                  placeholder="Exit option price"
                  value={exitPremium}
                  onChange={(e) => setExitPremium(e.target.value)}
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

export function OptionsPositionsTable({ positions, onExit, onCancel }: OptionsPositionsTableProps) {
  if (!positions.length) return <p className="empty">No option positions match this filter.</p>;
  return (
    <div className="table-scroll">
      <table className="postable">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Status</th>
            <th>Units</th>
            <th>Premium</th>
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

import { useState } from "react";
import type { ExitReason, Fill } from "../../api/types";
import { stratLabel } from "../../constants/strategy";
import { exitLabel, fmtMoney, fmtUnits } from "../../lib/format";

export interface FillsTableProps {
  fills: Fill[];
  onEdit: (
    fillId: number,
    body: { fill_date: string; price?: number; premium?: number; units: number; exit_reason?: ExitReason },
  ) => Promise<void>;
  onDelete: (fillId: number) => Promise<void>;
}

function FillEditRow({ fill, onSave, onCancel }: { fill: Fill; onSave: FillsTableProps["onEdit"]; onCancel: () => void }) {
  const isOption = fill.instrument === "option";
  const [fillDate, setFillDate] = useState(fill.fill_date);
  // Options edit premium (their own price, entry or exit); spot edits price -- same field, one
  // or the other is always null on a given fill depending on instrument.
  const [amount, setAmount] = useState(String(isOption ? (fill.premium ?? "") : (fill.price ?? "")));
  const [units, setUnits] = useState(String(fill.units));
  const [exitReason, setExitReason] = useState<ExitReason>(fill.exit_reason ?? "manual");

  async function save() {
    await onSave(fill.id, {
      fill_date: fillDate,
      ...(isOption ? { premium: parseFloat(amount) } : { price: parseFloat(amount) }),
      units: parseFloat(units),
      ...(fill.kind === "exit" ? { exit_reason: exitReason } : {}),
    });
    onCancel();
  }

  return (
    <tr>
      <td colSpan={7}>
        <div className="fill-form" style={{ margin: "6px 0" }}>
          <div className="fill-grid">
            <div className="form-row">
              <label>Fill Date</label>
              <input type="date" value={fillDate} onChange={(e) => setFillDate(e.target.value)} />
            </div>
            <div className="form-row">
              <label>{isOption ? "Option Price" : "Price"}</label>
              <input type="number" step={0.01} value={amount} onChange={(e) => setAmount(e.target.value)} />
            </div>
            <div className="form-row">
              <label>Units</label>
              <input type="number" step={1} value={units} onChange={(e) => setUnits(e.target.value)} />
            </div>
            {fill.kind === "exit" && (
              <div className="form-row">
                <label>Exit Reason</label>
                <select value={exitReason} onChange={(e) => setExitReason(e.target.value as ExitReason)}>
                  <option value="tp">TP</option>
                  <option value="stop">Stop</option>
                  <option value="manual">Manual</option>
                  <option value="expired">Expired</option>
                </select>
              </div>
            )}
          </div>
          <div className="form-actions">
            <button type="button" className="small-btn" onClick={save}>
              Save
            </button>
            <button type="button" className="small-btn" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

export function FillsTable({ fills, onEdit, onDelete }: FillsTableProps) {
  const [editingId, setEditingId] = useState<number | null>(null);

  if (!fills.length) return <p className="empty">No fills recorded.</p>;

  return (
    <div className="table-scroll">
      <table className="fillstable">
        <thead>
          <tr>
            <th>Date</th>
            <th>Kind</th>
            <th>Strategy</th>
            <th>Price</th>
            <th>Units</th>
            <th>Exit Reason</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {fills.map((f) => (
            <>
              <tr key={f.id}>
                <td>{f.fill_date}</td>
                <td>
                  <span className={`kind-tag ${f.kind}`}>{f.kind}</span>
                </td>
                <td>
                  <span className="strat-tag">{stratLabel(f.strategy_key)}</span>
                </td>
                <td>{fmtMoney(f.instrument === "option" ? (f.premium ?? 0) : (f.price ?? 0))}</td>
                <td>{fmtUnits(f.units)}</td>
                <td>{f.kind === "exit" ? exitLabel(f.exit_reason) : "—"}</td>
                <td className="actions-cell">
                  <button type="button" className="small-btn tiny" onClick={() => setEditingId(editingId === f.id ? null : f.id)}>
                    Edit
                  </button>{" "}
                  <button type="button" className="small-btn tiny danger" onClick={() => onDelete(f.id)}>
                    Delete
                  </button>
                </td>
              </tr>
              {editingId === f.id && <FillEditRow key={`edit-${f.id}`} fill={f} onSave={onEdit} onCancel={() => setEditingId(null)} />}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

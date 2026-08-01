import { useState } from "react";
import type { Position } from "../../api/types";

export interface EditPositionFormProps {
  position: Position;
  onSubmit: (body: { tp_price: number; stop_price: number; notes: string | null }) => Promise<void>;
  onCancel: () => void;
}

export function EditPositionForm({ position, onSubmit, onCancel }: EditPositionFormProps) {
  const [tpPrice, setTpPrice] = useState(String(position.tp_price ?? ""));
  const [stopPrice, setStopPrice] = useState(String(position.stop_price ?? ""));
  const [notes, setNotes] = useState(position.notes ?? "");

  async function handleSubmit() {
    await onSubmit({
      tp_price: parseFloat(tpPrice),
      stop_price: parseFloat(stopPrice),
      notes: notes || null,
    });
  }

  return (
    <div className="edit-form">
      <div className="edit-grid">
        <div className="form-row">
          <label>Take Profit</label>
          <input type="number" step={0.01} value={tpPrice} onChange={(e) => setTpPrice(e.target.value)} />
        </div>
        <div className="form-row">
          <label>Stop</label>
          <input type="number" step={0.01} value={stopPrice} onChange={(e) => setStopPrice(e.target.value)} />
        </div>
        <div className="form-row" style={{ gridColumn: "1/-1" }}>
          <label>Notes</label>
          <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
      </div>
      <div className="edit-actions">
        <button type="button" className="small-btn" onClick={handleSubmit}>
          Save changes
        </button>
        <button type="button" className="small-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <div className="edit-note">
        Position-level TP/stop/notes. To correct an individual fill's price/units, use the edit action on that fill's row below.
      </div>
    </div>
  );
}

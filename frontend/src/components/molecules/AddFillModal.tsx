import { useState } from "react";
import { Modal, ModalRow } from "../atoms/Modal";
import { todayIsoDate } from "../../lib/dates";
import { addFill } from "../../api/positions";
import type { ExitReason, FillKind, Position, StrategyKey } from "../../api/types";
import "./TradeConfirmModal.css";

export interface AddFillModalProps {
  position: Position;
  stratKey: StrategyKey;
  signalDate: string;
  currentPrice: number;
  onClose: () => void;
  onSubmitted: () => void;
}

export function AddFillModal({ position, stratKey, signalDate, currentPrice, onClose, onSubmitted }: AddFillModalProps) {
  const isOption = position.instrument === "option";
  const [kind, setKind] = useState<FillKind>("entry");
  const [fillDate, setFillDate] = useState(todayIsoDate());
  const [price, setPrice] = useState(currentPrice.toFixed(2));
  const [units, setUnits] = useState(isOption ? "1" : String(position.units_remaining));
  const [exitReason, setExitReason] = useState<ExitReason>("tp");
  const [optSide, setOptSide] = useState<"buy" | "sell">("buy");
  const [optType, setOptType] = useState<"call" | "put">("call");
  const [strike, setStrike] = useState("");
  const [premium, setPremium] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [iv, setIv] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit() {
    setFormError(null);
    const body = {
      kind,
      instrument: position.instrument,
      strategy_key: stratKey,
      signal_date: signalDate,
      fill_date: fillDate,
      price: parseFloat(price),
      units: parseFloat(units),
      ...(kind === "exit" ? { exit_reason: exitReason } : {}),
      ...(isOption
        ? {
            opt_side: optSide,
            opt_type: optType,
            strike: parseFloat(strike),
            premium: parseFloat(premium),
            expiry_date: expiryDate,
            iv_at_entry: Number.isFinite(parseFloat(iv)) ? parseFloat(iv) / 100 : null,
          }
        : {}),
    };
    setSubmitting(true);
    try {
      await addFill(position.id, body);
      onSubmitted();
    } catch (e) {
      setFormError(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Add to Position — ${position.ticker}`} onClose={onClose} width={480}>
      <div className="trade-modal">
        <ModalRow label="Current avg cost" value={position.avg_cost != null ? `$${position.avg_cost.toFixed(2)}` : "—"} />
        <ModalRow label="Units remaining" value={position.units_remaining} />
        <div className="modal-sep" />
        <div className="toggle-grp">
          <button type="button" className={`entry${kind === "entry" ? " active" : ""}`} onClick={() => setKind("entry")}>
            Add Entry
          </button>
          <button type="button" className={`exit${kind === "exit" ? " active" : ""}`} onClick={() => setKind("exit")}>
            Partial/Full Exit
          </button>
        </div>
        <div className="opt-fields">
          <div className="form-row">
            <label>Fill Date</label>
            <input type="date" value={fillDate} onChange={(e) => setFillDate(e.target.value)} />
          </div>
          <div className="form-row">
            <label>Price</label>
            <input type="number" step={0.01} value={price} onChange={(e) => setPrice(e.target.value)} />
          </div>
          <div className="form-row">
            <label>Units</label>
            <input type="number" step={1} value={units} onChange={(e) => setUnits(e.target.value)} />
          </div>
        </div>
        {kind === "exit" && (
          <div className="opt-fields">
            <div className="form-row">
              <label>Exit Reason</label>
              <select value={exitReason} onChange={(e) => setExitReason(e.target.value as ExitReason)}>
                <option value="tp">TP</option>
                <option value="stop">Stop</option>
                <option value="manual">Manual</option>
              </select>
            </div>
          </div>
        )}
        {isOption && (
          <div className="opt-fields">
            <div className="form-row">
              <label>Side</label>
              <select value={optSide} onChange={(e) => setOptSide(e.target.value as "buy" | "sell")}>
                <option value="buy">Buy</option>
                <option value="sell">Sell</option>
              </select>
            </div>
            <div className="form-row">
              <label>Type</label>
              <select value={optType} onChange={(e) => setOptType(e.target.value as "call" | "put")}>
                <option value="call">Call</option>
                <option value="put">Put</option>
              </select>
            </div>
            <div className="form-row">
              <label>Strike</label>
              <input type="number" step={0.5} value={strike} onChange={(e) => setStrike(e.target.value)} />
            </div>
            <div className="form-row">
              <label>Premium ($/sh)</label>
              <input type="number" step={0.05} value={premium} onChange={(e) => setPremium(e.target.value)} />
            </div>
            <div className="form-row">
              <label>Expiration Date</label>
              <input type="date" value={expiryDate} onChange={(e) => setExpiryDate(e.target.value)} />
            </div>
            <div className="form-row">
              <label>IV (%)</label>
              <input type="number" step={0.5} value={iv} onChange={(e) => setIv(e.target.value)} />
            </div>
          </div>
        )}
        <div className="form-error">{formError}</div>
        <button type="button" className="trade-btn" disabled={submitting} onClick={handleSubmit}>
          Add Fill
        </button>
      </div>
    </Modal>
  );
}

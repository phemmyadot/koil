import { useState } from "react";
import type { ExitReason, FillKind, Instrument, StrategyKey } from "../../api/types";
import { todayIsoDate } from "../../lib/dates";

export interface AddFillFormProps {
  instrument: Instrument;
  onSubmit: (body: {
    kind: FillKind;
    strategy_key: StrategyKey;
    signal_date: string;
    fill_date: string;
    price?: number;
    units: number;
    exit_reason?: ExitReason;
    opt_side?: "buy" | "sell";
    opt_type?: "call" | "put";
    strike?: number;
    premium?: number;
    expiry_date?: string;
    iv_at_entry?: number | null;
  }) => Promise<void>;
  onCancel: () => void;
}

export function AddFillForm({ instrument, onSubmit, onCancel }: AddFillFormProps) {
  const isOption = instrument === "option";
  const today = todayIsoDate();
  const [kind, setKind] = useState<FillKind>("entry");
  const [fillDate, setFillDate] = useState(today);
  const [price, setPrice] = useState("");
  const [units, setUnits] = useState("");
  const [strategyKey, setStrategyKey] = useState<StrategyKey>("vexh");
  const [signalDate, setSignalDate] = useState(today);
  const [exitReason, setExitReason] = useState<ExitReason>("tp");
  const [optSide, setOptSide] = useState<"buy" | "sell">("buy");
  const [optType, setOptType] = useState<"call" | "put">("call");
  const [strike, setStrike] = useState("");
  const [premium, setPremium] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [iv, setIv] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setError(null);
    try {
      await onSubmit({
        kind,
        strategy_key: strategyKey,
        signal_date: signalDate,
        fill_date: fillDate,
        units: parseFloat(units),
        ...(kind === "exit" ? { exit_reason: exitReason } : {}),
        // Options never need a stock price -- premium (the option's own price, whether this is
        // an entry or an exit fill) is what drives P&L. price stays spot-only.
        ...(isOption
          ? {
              opt_side: optSide,
              opt_type: optType,
              strike: parseFloat(strike),
              premium: parseFloat(premium),
              expiry_date: expiryDate,
              iv_at_entry: Number.isFinite(parseFloat(iv)) ? parseFloat(iv) / 100 : null,
            }
          : { price: parseFloat(price) }),
      });
    } catch (e) {
      setError(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="fill-form">
      <div className="toggle-grp">
        <button type="button" className={kind === "entry" ? "active" : ""} onClick={() => setKind("entry")}>
          Add Entry
        </button>
        <button type="button" className={kind === "exit" ? "active" : ""} onClick={() => setKind("exit")}>
          Partial/Full Exit
        </button>
      </div>
      <div className="fill-grid">
        <div className="form-row">
          <label>Fill Date</label>
          <input type="date" value={fillDate} onChange={(e) => setFillDate(e.target.value)} />
        </div>
        {!isOption && (
          <div className="form-row">
            <label>Price</label>
            <input type="number" step={0.01} value={price} onChange={(e) => setPrice(e.target.value)} />
          </div>
        )}
        <div className="form-row">
          <label>Units</label>
          <input type="number" step={1} value={units} onChange={(e) => setUnits(e.target.value)} />
        </div>
        <div className="form-row">
          <label>Strategy</label>
          <select value={strategyKey} onChange={(e) => setStrategyKey(e.target.value as StrategyKey)}>
            <option value="vexh">VEXH</option>
            <option value="strategy_vcp">VCP</option>
            <option value="strategy_vcpo">VCPO</option>
          </select>
        </div>
        <div className="form-row">
          <label>Signal Date</label>
          <input type="date" value={signalDate} onChange={(e) => setSignalDate(e.target.value)} />
        </div>
        {kind === "exit" && (
          <div className="form-row">
            <label>Exit Reason</label>
            <select value={exitReason} onChange={(e) => setExitReason(e.target.value as ExitReason)}>
              <option value="tp">TP</option>
              <option value="stop">Stop</option>
              <option value="manual">Manual</option>
            </select>
          </div>
        )}
        {isOption && (
          <>
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
              <label>{kind === "exit" ? "Exit Premium ($/sh)" : "Premium ($/sh)"}</label>
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
          </>
        )}
      </div>
      <div className="form-actions">
        <button type="button" className="small-btn" onClick={handleSubmit}>
          Add Fill
        </button>
        <button type="button" className="small-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && <div className="empty">{error}</div>}
    </div>
  );
}

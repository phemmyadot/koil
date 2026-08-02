import { useEffect, useState } from "react";
import { Modal, ModalRow } from "../atoms/Modal";
import { todayIsoDate } from "../../lib/dates";
import { estimateEntry } from "../../api/plCalc";
import { createPosition } from "../../api/positions";
import type { EstimateEntryResponse, Instrument, OpenPosition, StrategyKey } from "../../api/types";
import "./TradeConfirmModal.css";

export interface TradeConfirmModalProps {
  ticker: string;
  // "manual" -- a trade added via "+ Add Trade" for a ticker with no strategy signal behind it
  // (see docs/superpowers/specs/2026-08-01-add-trade-untracked-ticker-design.md) -- is NOT part
  // of StrategyKey itself (that stays the 3-value screener-signal union, used elsewhere to index
  // TickerPayload's per-strategy fields, which "manual" was never a member of), just accepted
  // here as an additional valid value for this one prop.
  stratKey: StrategyKey | "manual";
  signalDate: string;
  currentPrice: number;
  openPosition: OpenPosition | null;
  avgMaeWinsPct: number | null;
  onClose: () => void;
  onSubmitted: () => void;
}

export function TradeConfirmModal({
  ticker,
  stratKey,
  signalDate,
  currentPrice,
  openPosition: op,
  avgMaeWinsPct,
  onClose,
  onSubmitted,
}: TradeConfirmModalProps) {
  const today = todayIsoDate();
  const [instrument, setInstrument] = useState<Instrument>("spot");
  const [entryDate, setEntryDate] = useState(op ? op.entry_date : today);
  const [entryPrice, setEntryPrice] = useState((op ? op.entry_price : currentPrice).toFixed(2));
  const [tpPrice, setTpPrice] = useState(op ? String(op.target) : "");
  const [stopPrice, setStopPrice] = useState(op && op.stop != null ? String(op.stop) : "");
  const [units, setUnits] = useState("1");
  const [optSide, setOptSide] = useState<"buy" | "sell">("buy");
  const [optType, setOptType] = useState<"call" | "put">("call");
  const [strike, setStrike] = useState("");
  const [premium, setPremium] = useState("");
  const [contracts, setContracts] = useState("1");
  const [expiryDate, setExpiryDate] = useState("");
  const [iv, setIv] = useState("");
  const [estimate, setEstimate] = useState<EstimateEntryResponse | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const showEstimate = !!(op && avgMaeWinsPct != null);

  useEffect(() => {
    if (!showEstimate) return;
    // showEstimate is only true when op (an existing open_position from a real strategy
    // signal) is present -- the manual flow (stratKey === "manual") never has one, so this
    // narrowing is safe: stratKey is a real StrategyKey whenever this code actually runs.
    const realStratKey = stratKey as StrategyKey;
    let cancelled = false;
    estimateEntry(ticker, realStratKey)
      .then((est) => {
        if (cancelled) return;
        setEstimate(est);
        setEntryPrice(est.recommended_limit.toFixed(2));
        setStopPrice((prev) => (prev ? prev : est.mae_floor.toFixed(2)));
      })
      .catch((e) => !cancelled && setEstimateError(String(e)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit() {
    setFormError(null);
    const body = {
      ticker,
      strategy_key: stratKey,
      signal_date: signalDate,
      instrument,
      fill_date: entryDate,
      tp_price: parseFloat(tpPrice),
      stop_price: parseFloat(stopPrice),
      units:
        instrument === "option" ? Math.max(1, Math.round(parseFloat(contracts) || 1)) : parseFloat(units),
      // Options never need a stock entry price -- premium is what drives P&L, see
      // docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md. price stays
      // spot-only.
      ...(instrument === "option"
        ? {
            opt_side: optSide,
            opt_type: optType,
            strike: parseFloat(strike),
            premium: parseFloat(premium),
            expiry_date: expiryDate,
            iv_at_entry: Number.isFinite(parseFloat(iv)) ? parseFloat(iv) / 100 : null,
          }
        : { price: parseFloat(entryPrice) }),
    };
    setSubmitting(true);
    try {
      await createPosition(body);
      onSubmitted();
    } catch (e) {
      setFormError(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Confirm Trade — ${ticker}`} onClose={onClose} width={560}>
      {showEstimate && (
        <div className="trade-modal" style={{ marginBottom: 12 }}>
          <ModalRow label="Current price" value={`$${currentPrice.toFixed(2)}`} />
          <ModalRow label="Sim entry" value={`$${op!.entry_price.toFixed(2)}`} />
          <ModalRow label="Avg MAE (wins)" value={`-${avgMaeWinsPct}%`} />
          <div className="modal-sep" />
          {estimateError && <div className="modal-note">Failed: {estimateError}</div>}
          {!estimateError && !estimate && <div className="modal-note">Computing&hellip;</div>}
          {estimate && (
            <>
              <ModalRow label="MAE floor" value={`$${estimate.mae_floor.toFixed(2)}`} />
              <ModalRow
                label="Support used"
                value={`$${estimate.support_used.toFixed(2)}${estimate.support_touches != null ? ` (${estimate.support_touches}x)` : ""}`}
              />
              <ModalRow
                label="Recommended limit"
                value={`$${estimate.recommended_limit.toFixed(2)} (${estimate.pct_below_current}% vs current)`}
              />
            </>
          )}
        </div>
      )}
      <div className="trade-modal">
        <div className="toggle-grp">
          <button type="button" className={`spot${instrument === "spot" ? " active" : ""}`} onClick={() => setInstrument("spot")}>
            Spot
          </button>
          <button
            type="button"
            className={`option${instrument === "option" ? " active" : ""}`}
            onClick={() => setInstrument("option")}
          >
            Option
          </button>
        </div>
        <div className="opt-fields">
          <div className={`form-row${instrument === "option" ? " form-row-full" : ""}`}>
            <label>Entry Date</label>
            <input type="date" value={entryDate} onChange={(e) => setEntryDate(e.target.value)} />
          </div>
          {instrument === "spot" && (
            <div className="form-row">
              <label>Entry Price</label>
              <input type="number" step={0.01} value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)} />
            </div>
          )}
          <div className="form-row">
            <label>Take Profit</label>
            <input type="number" step={0.01} value={tpPrice} onChange={(e) => setTpPrice(e.target.value)} />
          </div>
          <div className="form-row">
            <label>Stop</label>
            <input type="number" step={0.01} value={stopPrice} onChange={(e) => setStopPrice(e.target.value)} />
          </div>
        </div>
        {instrument === "spot" ? (
          <div className="opt-fields">
            <div className="form-row">
              <label>Units (shares)</label>
              <input type="number" step={1} value={units} onChange={(e) => setUnits(e.target.value)} />
            </div>
          </div>
        ) : (
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
              <label>Contracts</label>
              <input type="number" step={1} value={contracts} onChange={(e) => setContracts(e.target.value)} />
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
          Confirm Trade
        </button>
      </div>
    </Modal>
  );
}

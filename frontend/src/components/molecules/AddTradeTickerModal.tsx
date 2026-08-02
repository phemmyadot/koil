import { useState } from "react";
import { Modal } from "../atoms/Modal";
import { fetchOneTicker, type FetchOneTickerResponse } from "../../api/tickers";
import { listPositions } from "../../api/positions";
import type { Position } from "../../api/types";
import "./TradeConfirmModal.css";

export interface AddTradeTickerModalProps {
  onClose: () => void;
  // Ticker already has an open position -- route to Add Fill instead of a fresh trade, per
  // docs/superpowers/specs/2026-08-01-add-trade-untracked-ticker-design.md (confirmed: this
  // means the ticker already has data, not an error case).
  onExistingPosition: (position: Position, currentPrice: number) => void;
  // No open position -- proceed to a fresh TradeConfirmModal, stratKey "manual".
  onNewTrade: (response: FetchOneTickerResponse) => void;
}

type FetchState = { kind: "idle" } | { kind: "fetching" } | { kind: "error"; message: string };

// Step 1 of the "+ Add Trade" flow -- a ticker not in the currently-loaded universe, fetched
// and computed on demand so the trade form (or Add Fill, if it turns out this ticker already
// has an open position) has real data to prefill from. On a failed fetch there is no path
// forward from this modal into a trade form -- Fetch & Compute is the only actionable control
// until the ticker resolves or the user closes the modal.
export function AddTradeTickerModal({ onClose, onExistingPosition, onNewTrade }: AddTradeTickerModalProps) {
  const [ticker, setTicker] = useState("");
  const [state, setState] = useState<FetchState>({ kind: "idle" });

  async function handleFetch() {
    const trimmed = ticker.trim().toUpperCase();
    if (!trimmed) {
      setState({ kind: "error", message: "Enter a ticker" });
      return;
    }
    setState({ kind: "fetching" });
    try {
      const response = await fetchOneTicker(trimmed);
      let openPositions: Position[] = [];
      try {
        openPositions = await listPositions("open");
      } catch {
        // Best-effort -- falls through to "new trade" if this lookup fails, same as the
        // existing dashboard-card TRADE flow's own openTradeFlow().
      }
      const existing = openPositions.find((p) => p.ticker === trimmed);
      if (existing) {
        onExistingPosition(existing, response.price);
      } else {
        onNewTrade(response);
      }
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }

  const fetching = state.kind === "fetching";

  return (
    <Modal title="Add Trade" onClose={onClose} width={360}>
      <div className="trade-modal">
        <div className="opt-fields">
          <div className="form-row">
            <label>Ticker</label>
            <input
              type="text"
              autoFocus
              value={ticker}
              disabled={fetching}
              onChange={(e) => {
                setTicker(e.target.value);
                if (state.kind === "error") setState({ kind: "idle" });
              }}
              onKeyDown={(e) => e.key === "Enter" && !fetching && handleFetch()}
              placeholder="e.g. AAPL"
            />
          </div>
        </div>
        {state.kind === "error" && <div className="form-error">{state.message}</div>}
        <button type="button" className="trade-btn" disabled={fetching} onClick={handleFetch}>
          {fetching ? "Fetching…" : "Fetch & Compute"}
        </button>
      </div>
    </Modal>
  );
}

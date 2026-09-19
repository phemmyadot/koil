import { Modal, ModalRow } from "../atoms/Modal";
import type { CryptoV2TickerPayload } from "../../api/cryptoV2";

export interface CryptoV2SignalModalProps {
  row: CryptoV2TickerPayload;
  onClose: () => void;
  // Optional -- omitted wherever this modal is opened without a trade-taking flow behind it.
  onTrade?: () => void;
}

function titleCase(setupType: string): string {
  return setupType.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

// v2 has no single verdict/score like v1's strategy_vcp -- a ticker can have several setups
// triggered the same day, each with its own component breakdown (doc §49's "why the signal
// triggered"). Shows every signals_today entry, the underlying backtest stats they're judged
// against, and the raw feature snapshot those setups were computed from.
export function CryptoV2SignalModal({ row, onClose, onTrade }: CryptoV2SignalModalProps) {
  const fs = row.feature_snapshot;
  return (
    <Modal title={`${row.ticker} — v2`} onClose={onClose} width={520}>
      <ModalRow label="Category" value={row.category ?? "—"} />
      <ModalRow label="Price" value={row.price != null ? `$${row.price}` : "—"} />
      <ModalRow label="Execution score" value={`${row.execution_score} / 10`} />
      <div className="modal-sep" />
      <ModalRow label="Backtested trades" value={row.n_trades} />
      <ModalRow label="Win rate" value={`${row.win_rate}%`} />
      <ModalRow label="Profit factor" value={row.profit_factor} />
      {row.avg_trade_days != null && <ModalRow label="Avg trade days" value={row.avg_trade_days} />}

      {row.open_position && (
        <>
          <div className="modal-sep" />
          <div className="modal-note">
            Open position ({row.open_setup_type ? titleCase(row.open_setup_type) : "—"}):
          </div>
          <ModalRow label="Entry" value={`${row.open_position.entry_date} @ $${row.open_position.entry_price}`} />
          <ModalRow
            label="Unrealized"
            value={`${row.open_position.unrealized_pct >= 0 ? "+" : ""}${row.open_position.unrealized_pct}% (${row.open_position.days_held}d held)`}
          />
          <ModalRow label="Target / Stop" value={`$${row.open_position.target} / ${row.open_position.stop != null ? `$${row.open_position.stop}` : "—"}`} />
        </>
      )}

      {row.signals_today.length > 0 && (
        <>
          <div className="modal-sep" />
          <div className="modal-note">Signals today:</div>
          {row.signals_today.map((sig) => (
            <div key={sig.setup_type} style={{ marginBottom: 8 }}>
              <ModalRow label={titleCase(sig.setup_type)} value={`score ${sig.setup_score}`} />
              {Object.entries(sig.score_breakdown).map(([component, points]) => (
                <ModalRow
                  key={component}
                  label={`  ${sig.components[component] ? "✓" : "✗"} ${component.replace(/_/g, " ")}`}
                  value={points}
                />
              ))}
            </div>
          ))}
        </>
      )}

      <div className="modal-sep" />
      <div className="modal-note">Feature snapshot ({row.date}):</div>
      <ModalRow label="ATR %" value={fs.atr_pct != null ? `${(fs.atr_pct * 100).toFixed(2)}%` : "—"} />
      <ModalRow label="RVOL" value={fs.rvol ?? "—"} />
      <ModalRow label="RSI" value={fs.rsi ?? "—"} />
      <ModalRow label="RS vs BTC" value={fs.rs_btc != null ? `${(fs.rs_btc * 100).toFixed(2)}%` : "—"} />
      <ModalRow label="RS percentile" value={fs.rs_percentile != null ? `${fs.rs_percentile}%` : "—"} />
      <ModalRow label="Bullish BOS" value={fs.bullish_bos ? "yes" : "no"} />

      {onTrade && (
        <>
          <div className="modal-sep" />
          <button type="button" className="trade-btn" onClick={onTrade}>
            TRADE
          </button>
        </>
      )}
    </Modal>
  );
}

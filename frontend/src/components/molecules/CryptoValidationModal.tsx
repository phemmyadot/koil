import { Modal, ModalRow } from "../atoms/Modal";
import type { CryptoTickerPayload } from "../../api/crypto";

export interface CryptoValidationModalProps {
  row: CryptoTickerPayload;
  onClose: () => void;
}

// Cross-checking the app's backtest against a TradingView chart needs the data this strategy's
// own last5_trades (days/tp_pct only) doesn't carry -- actual entry/exit dates+prices, and the
// exact bar range the backtest ran over, so a mismatch (data source/history length/config) is
// visible immediately instead of guessed at.
export function CryptoValidationModal({ row, onClose }: CryptoValidationModalProps) {
  const s = row.strategy_vcp;
  return (
    <Modal title={`Validate — ${row.ticker}`} onClose={onClose} width={480}>
      <ModalRow label="Data range" value={`${s.data_range.start} → ${s.data_range.end}`} />
      <ModalRow label="Bars" value={s.data_range.n_bars} />
      <ModalRow label="Source" value={row.last_market ?? "—"} />
      <ModalRow
        label="Bars source"
        value={row.price_source === "yfinance" ? "yfinance (unverified)" : row.price_source ?? "—"}
      />
      <div className="modal-sep" />
      <ModalRow label="Trades" value={s.n_trades} />
      <ModalRow label="Win rate" value={`${s.win_rate}%`} />
      <ModalRow label="Profit factor" value={s.profit_factor} />
      <div className="modal-sep" />
      <div className="modal-note">Last {s.last5_trades_detailed.length} trades (entry → exit):</div>
      {s.last5_trades_detailed.map((t, i) => (
        <ModalRow
          key={i}
          label={`${t.entry_date} → ${t.exit_date}`}
          value={`${t.entry_price} → ${t.exit_price} (${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct}%)`}
        />
      ))}
    </Modal>
  );
}

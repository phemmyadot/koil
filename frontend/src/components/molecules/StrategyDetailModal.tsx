import { Modal, ModalRow } from "../atoms/Modal";
import type { PrebreakResult, StrategyKey, StrategyResult } from "../../api/types";
import { stratLabel } from "../../constants/strategy";
import { fmtPct, prebreakSummaryLine } from "../../lib/format";

export interface StrategyDetailModalProps {
  ticker: string;
  // stratKey/s are undefined for a manual trade (no backtested strategy behind it) -- the modal
  // then shows prebreak only, no trade-history stats.
  stratKey?: StrategyKey;
  s?: StrategyResult;
  prebreak: PrebreakResult | null;
  onClose: () => void;
  onTrade?: () => void;
}

function last5(trades: StrategyResult["last5_trades"]) {
  if (!trades.length) return <span style={{ color: "var(--muted)" }}>&mdash;</span>;
  return (
    <span style={{ fontSize: 11 }}>
      {trades
        .map((t) => `${t.tp_pct >= 0 ? "+" : ""}${t.tp_pct}% - ${t.days}D`)
        .join(", ")}
    </span>
  );
}

// Replaces strategyModal() (index.html) -- one modal per strategy badge click, not a
// 3-column comparison.
export function StrategyDetailModal({ ticker, stratKey, s, prebreak, onClose, onTrade }: StrategyDetailModalProps) {
  const op = s?.open_position;
  const title = stratKey ? `${stratLabel(stratKey)} — ${ticker}` : `Manual — ${ticker}`;
  return (
    <Modal title={title} onClose={onClose}>
      {prebreak && (
        <>
          <div className="modal-note">{prebreakSummaryLine(prebreak)}</div>
          {s && <div className="modal-sep" />}
        </>
      )}
      {s && (
        <>
          <ModalRow label="Trades" value={s.n_trades} />
          <ModalRow label="Profit factor" value={s.profit_factor} />
          <ModalRow label="Win rate" value={`${s.win_rate}%`} />
          {s.first_trade_date && <ModalRow label="Since" value={s.first_trade_date} />}
          {op && (
            <>
              <div className="modal-sep" />
              <ModalRow label="Entry" value={`${op.entry_date} @ ${op.entry_price}`} />
              <ModalRow label="Target" value={op.target} />
              <ModalRow label="Unrealized" value={fmtPct(op.unrealized_pct)} />
              <ModalRow label="Bars held" value={op.days_held} />
              <div className="modal-sep" />
              <ModalRow label="To TP %" value={fmtPct(op.to_tp_pct)} />
            </>
          )}
          {s.avg_mae_wins_pct != null && (
            <>
              {!op && <div className="modal-sep" />}
              <ModalRow
                label="Avg MAE (wins)"
                value={
                  `-${s.avg_mae_wins_pct}%` +
                  (op ? ` [→ limit at $${(op.entry_price * (1 - s.avg_mae_wins_pct / 100)).toFixed(2)}]` : "")
                }
              />
              {s.pct_near_zero_mae != null && <ModalRow label="Wins w/ MAE<1%" value={`${s.pct_near_zero_mae}%`} />}
            </>
          )}
          {s.avg_mfe_wins_pct != null && <ModalRow label="Avg MFE (wins)" value={`+${s.avg_mfe_wins_pct}%`} />}
          <div className="modal-sep" />
          <ModalRow label="Avg Days" value={s.avg_trade_days ?? "—"} />
          <div className="modal-sep" />
          <ModalRow label="Last 5 TP %" value={last5(s.last5_trades)} />
          {s.verdict && (
            <>
              <div className="modal-sep" />
              <div className="modal-note">
                <b>{s.verdict}</b>: {s.verdict_reason || ""}
              </div>
            </>
          )}
        </>
      )}
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

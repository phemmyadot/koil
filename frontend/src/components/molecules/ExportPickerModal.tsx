import { Modal } from "../atoms/Modal";
import "./TradeConfirmModal.css";

export interface ExportPickerModalProps {
  count: number;
  onPick: (format: "pdf" | "csv") => void;
  onClose: () => void;
}

export function ExportPickerModal({ count, onPick, onClose }: ExportPickerModalProps) {
  return (
    <Modal title={`Export ${count} ticker(s) as…`} onClose={onClose}>
      <div className="watchlist-picker">
        <button type="button" className="watchlist-pick-btn" onClick={() => onPick("pdf")}>
          PDF
        </button>
        <button type="button" className="watchlist-pick-btn" onClick={() => onPick("csv")}>
          CSV
        </button>
      </div>
    </Modal>
  );
}

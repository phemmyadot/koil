import { Modal } from "../atoms/Modal";
import { WATCHLIST_NAMES } from "../../constants/filterDefaults";
import "./TradeConfirmModal.css";

export interface WatchlistPickerModalProps {
  count: number;
  onPick: (name: (typeof WATCHLIST_NAMES)[number]) => void;
  onClose: () => void;
}

export function WatchlistPickerModal({ count, onPick, onClose }: WatchlistPickerModalProps) {
  return (
    <Modal title={`Add ${count} ticker(s) to…`} onClose={onClose}>
      <div className="watchlist-picker">
        {WATCHLIST_NAMES.map((name) => (
          <button key={name} type="button" className="watchlist-pick-btn" onClick={() => onPick(name)}>
            {name}
          </button>
        ))}
      </div>
    </Modal>
  );
}

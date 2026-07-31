import { useEffect } from "react";
import "./Modal.css";

export interface ModalProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
}

// Replaces the old #modalBackdrop/#modalBox generic shell (one div, innerHTML-swapped per
// "modal type") -- here every modal is a real component using this one shell, instead of a
// shared DOM node whose content changes underneath it.
export function Modal({ title, onClose, children, width }: ModalProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-box"
        style={width ? { width } : undefined}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-title">{title}</div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function ModalRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="modal-row">
      <span className="modal-row-label">{label}</span>
      <span className="modal-row-value">{value}</span>
    </div>
  );
}

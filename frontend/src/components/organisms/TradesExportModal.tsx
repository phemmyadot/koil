import { useState } from "react";
import { Modal } from "../atoms/Modal";
import "./TradesExportModal.css";

export function TradesExportModal({
  markdown,
  onClose,
  title = "Export Trades (Markdown)",
}: {
  markdown: string;
  onClose: () => void;
  title?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      window.alert("Could not copy to clipboard -- select the text below and copy manually.");
    }
  }

  return (
    <Modal title={title} onClose={onClose} width={720}>
      <textarea className="trades-export-textarea" readOnly value={markdown} onFocus={(e) => e.target.select()} />
      <button type="button" className="small-btn trades-export-copy-btn" onClick={handleCopy}>
        {copied ? "Copied!" : "Copy to clipboard"}
      </button>
    </Modal>
  );
}

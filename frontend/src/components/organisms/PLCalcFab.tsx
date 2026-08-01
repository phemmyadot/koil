import { useState } from "react";
import { PLCalculatorModal } from "../molecules/PLCalculatorModal";
import "./PLCalcFab.css";

// Fixed floating action button, available on every page (not a nav destination) -- see
// docs/superpowers/specs/2026-07-31-react-spa-rewrite-design.md's `PLCalcFab` section.
export function PLCalcFab() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="plcalc-fab" onClick={() => setOpen(true)} aria-label="P/L Calculator">
        <svg className="plcalc-fab-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="4" y="2.5" width="16" height="19" rx="2.5" stroke="currentColor" strokeWidth="1.8" />
          <rect x="6.75" y="5.25" width="10.5" height="4.5" rx="0.75" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="7.5" cy="13.5" r="1.1" fill="currentColor" />
          <circle cx="12" cy="13.5" r="1.1" fill="currentColor" />
          <circle cx="16.5" cy="13.5" r="1.1" fill="currentColor" />
          <circle cx="7.5" cy="17.5" r="1.1" fill="currentColor" />
          <circle cx="12" cy="17.5" r="1.1" fill="currentColor" />
          <circle cx="16.5" cy="17.5" r="1.1" fill="currentColor" />
        </svg>
        <span className="plcalc-fab-label">P/L Calc</span>
      </button>
      {open && <PLCalculatorModal onClose={() => setOpen(false)} />}
    </>
  );
}

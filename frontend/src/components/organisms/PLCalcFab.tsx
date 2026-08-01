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
        <span className="plcalc-fab-icon" aria-hidden="true">
          &#129518;
        </span>
        <span className="plcalc-fab-label">P/L Calc</span>
      </button>
      {open && <PLCalculatorModal onClose={() => setOpen(false)} />}
    </>
  );
}

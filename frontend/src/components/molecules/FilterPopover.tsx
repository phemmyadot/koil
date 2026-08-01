import { useEffect, useRef, useState } from "react";
import "./FilterPopover.css";

export interface FilterPopoverProps {
  label: string;
  activeCount: number;
  onClear: () => void;
  children: React.ReactNode;
}

// Generic popover shell shared by Advance Filter / Trade On / Pre-Breakout -- replaces
// setupFilterPopover's open/close/outside-click wiring (index.html), applied once here instead
// of once per panel.
export function FilterPopover({ label, activeCount, onClear, children }: FilterPopoverProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [open]);

  return (
    <div className="advfilter" ref={ref}>
      <button
        type="button"
        className="advfilterbtn"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        {label}
        {activeCount > 0 && <span className="advfiltercount">{activeCount}</span>}
        <span className="filtertogglearrow">&#9662;</span>
      </button>
      {open && (
        <div className="advfilterpanel">
          {children}
          <div className="advfilterfoot">
            <button type="button" className="clearfilters" onClick={onClear}>
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

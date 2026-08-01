import { useEffect, useRef, useState } from "react";
import "./FilterPopover.css";

export interface FilterPopoverProps {
  label: string;
  activeCount: number;
  onClear: () => void;
  children: React.ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  className?: string;
}

// Closing animation duration -- must match .advfilterpanel-closing's animation-duration in
// FilterPopover.css. Kept as a single source of truth here since the unmount timer needs the
// same number the CSS uses.
const CLOSE_ANIM_MS = 130;

// Generic popover shell shared by Advance Filter / Trade On / Pre-Breakout -- replaces
// setupFilterPopover's open/close/outside-click wiring (index.html), applied once here instead
// of once per panel. Open state is owned by the parent (FilterBar) so only one panel can be
// open at a time -- opening one closes any other that was already open.
export function FilterPopover({ label, activeCount, onClear, children, open, onOpenChange, className }: FilterPopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  // Panel stays mounted for CLOSE_ANIM_MS after `open` goes false, so the slide-in-reverse
  // (closing) animation can actually play instead of the panel just vanishing instantly.
  const [mounted, setMounted] = useState(open);

  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    const timer = setTimeout(() => setMounted(false), CLOSE_ANIM_MS);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOpenChange(false);
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [open, onOpenChange]);

  return (
    <div className={`advfilter${className ? ` ${className}` : ""}`} ref={ref}>
      <button
        type="button"
        className="advfilterbtn"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          onOpenChange(!open);
        }}
      >
        {label}
        {activeCount > 0 && <span className="advfiltercount">{activeCount}</span>}
        <span className="filtertogglearrow">&#9662;</span>
      </button>
      {mounted && (
        <div className={`advfilterpanel${open ? "" : " advfilterpanel-closing"}`}>
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

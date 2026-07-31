import "./StatBox.css";
import { plClass } from "../../lib/format";

export interface StatBoxProps {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: number; // if provided, colors `value` via plClass(tone)
}

// Replaces .statbox, redefined 3 times (index.html/trades.html/position.html) with slightly
// different markup each time -- one component here.
export function StatBox({ label, value, sub, tone }: StatBoxProps) {
  const toneClass = tone !== undefined ? plClass(tone) : "";
  return (
    <div className="statbox">
      <span className="statbox-lbl">{label}</span>
      <span className={`statbox-val ${toneClass}`}>
        {value}
        {sub !== undefined && <span className="statbox-sub">{sub}</span>}
      </span>
    </div>
  );
}

import "./Chip.css";
import type { ColorTier } from "../../lib/colorTiers";

export interface ChipProps {
  children: React.ReactNode;
  tone: ColorTier | "active" | "pending";
  onClick?: () => void;
}

// Replaces .chip.ok/.no/.mid/.neutral/.active/.pending from index.html/watchlist.html.
export function Chip({ children, tone, onClick }: ChipProps) {
  const cls = `chip chip-${tone}` + (onClick ? " chip-clickable" : "");
  return (
    <span className={cls} onClick={onClick} role={onClick ? "button" : undefined}>
      {children}
    </span>
  );
}

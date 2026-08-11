import { useState } from "react";
import { useTickers } from "../../hooks/useTickers";
import { StrategyDetailModal } from "./StrategyDetailModal";
import type { StrategyKey } from "../../api/types";
import { stratLabel } from "../../constants/strategy";

export interface StrategyCellLinkProps {
  ticker: string;
  strategyKey: string;
  // "link" (default): small dashed-underline text, used inline in table cells.
  // "button": bordered small-btn look, used standalone (e.g. PositionDetailPage's action bar).
  variant?: "link" | "button";
}

// Reuses the dashboard's own StrategyDetailModal -- looks up the same TickerPayload row
// (useTickers is React-Query cached under ["tickers"], so this doesn't trigger a new fetch if
// the dashboard's already loaded it this session). "manual" positions have no StrategyResult,
// but still get a clickable link when prebreak data exists -- the modal then shows only the
// Pre-Breakout summary, no trade-history stats. No link at all if prebreak is also unavailable.
export function StrategyCellLink({ ticker, strategyKey, variant = "link" }: StrategyCellLinkProps) {
  const [open, setOpen] = useState(false);
  const { data } = useTickers();

  const label = stratLabel(strategyKey);
  const row = data?.tickers.find((t) => t.ticker === ticker);
  const className = variant === "button" ? "small-btn" : "strategy-cell-link";

  if (strategyKey === "manual") {
    if (!row?.prebreak) return variant === "button" ? null : <span className="strategy-cell-label">{label}</span>;
    return (
      <>
        <button type="button" className={className} onClick={() => setOpen(true)}>
          {variant === "button" ? `${label} Strategy` : label}
        </button>
        {open && <StrategyDetailModal ticker={ticker} prebreak={row.prebreak} onClose={() => setOpen(false)} />}
      </>
    );
  }

  const s = row?.[strategyKey as StrategyKey];

  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>
        {variant === "button" ? `${label} Strategy` : label}
      </button>
      {open && s && (
        <StrategyDetailModal
          ticker={ticker}
          stratKey={strategyKey as StrategyKey}
          s={s}
          prebreak={row?.prebreak ?? null}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

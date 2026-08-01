import type { StrategyKey, TickerPayload } from "../../api/types";
import type { StrategyShortKey } from "../../constants/strategy";
import { TickerCard } from "./TickerCard";
import "./TickerCardGrid.css";

export interface TickerCardGridProps {
  rows: TickerPayload[];
  errors: [string, string][];
  showErrors: boolean;
  scoreStrategy: StrategyShortKey;
  selected: Set<string>;
  onToggleSelect: (ticker: string) => void;
  onOpenStrategy: (ticker: string, stratKey: StrategyKey) => void;
}

// Replaces #rows + render()'s card-building loop (index.html).
export function TickerCardGrid({
  rows,
  errors,
  showErrors,
  scoreStrategy,
  selected,
  onToggleSelect,
  onOpenStrategy,
}: TickerCardGridProps) {
  if (!rows.length && !errors.length) {
    return (
      <div className="cardgrid">
        <p style={{ color: "var(--muted)" }}>No tickers match this filter</p>
      </div>
    );
  }
  return (
    <div className="cardgrid">
      {rows.map((r) => (
        <TickerCard
          key={r.ticker}
          row={r}
          scoreStrategy={scoreStrategy}
          selected={selected.has(r.ticker)}
          onToggleSelect={onToggleSelect}
          onOpenStrategy={onOpenStrategy}
        />
      ))}
      {showErrors &&
        errors.map(([tk, err]) => (
          <div className="tickercard err" key={tk}>
            <div className="cardhead">
              <span className="tk">{tk}</span>
            </div>
            <span>{err}</span>
          </div>
        ))}
    </div>
  );
}

export function Pagination({
  page,
  pageCount,
  onPrev,
  onNext,
}: {
  page: number;
  pageCount: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className="pager">
      <button type="button" disabled={page === 1} onClick={onPrev}>
        &larr; Prev
      </button>
      <span>
        Page {page} of {pageCount}
      </span>
      <button type="button" disabled={page === pageCount} onClick={onNext}>
        Next &rarr;
      </button>
    </div>
  );
}

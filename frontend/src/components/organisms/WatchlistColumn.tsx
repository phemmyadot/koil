import type { TickerPayload } from "../../api/types";
import { Chip } from "../atoms/Chip";
import { pfColorClass, tradeCountColorClass, wrColorClass } from "../../lib/colorTiers";
import { sortByLatestTrade, statsFor } from "../../lib/watchlistSort";
import "./WatchlistColumn.css";

export interface WatchlistColumnProps {
  name: string;
  tickers: string[];
  byTicker: Record<string, TickerPayload>;
  selected: Set<string>;
  onToggle: (ticker: string, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  onRemove: (ticker: string) => void;
  onRemoveSelected: () => void;
}

function WatchlistBadges({ stats }: { stats: ReturnType<typeof statsFor> }) {
  if (!stats) return <Chip tone="neutral">no data</Chip>;
  const wins = Math.round((stats.win_rate / 100) * stats.n_trades);
  return (
    <span className="badge-row">
      {stats.active && <Chip tone="active">O-{stats.days}</Chip>}
      <Chip tone={tradeCountColorClass(stats.n_trades)}>T{stats.n_trades}</Chip>
      <Chip tone={wrColorClass(stats.win_rate)}>
        WR{stats.win_rate}%({wins}/{stats.n_trades})
      </Chip>
      <Chip tone={pfColorClass(stats.profit_factor)}>PF{stats.profit_factor}</Chip>
    </span>
  );
}

export function WatchlistColumn({ name, tickers, byTicker, selected, onToggle, onToggleAll, onRemove, onRemoveSelected }: WatchlistColumnProps) {
  const sorted = sortByLatestTrade(tickers, byTicker, name);
  const allChecked = sorted.length > 0 && selected.size === sorted.length;

  return (
    <div className="watchlist-col">
      <div className="watchlist-colhead">
        <input
          type="checkbox"
          title="Select all"
          checked={allChecked}
          disabled={sorted.length === 0}
          onChange={(e) => onToggleAll(e.target.checked)}
        />
        <h2>{name}</h2>
        <span className="count">{sorted.length}</span>
      </div>
      {selected.size > 0 && (
        <div className="watchlist-colactions">
          <button type="button" className="watchlist-rmselbtn" onClick={onRemoveSelected}>
            Remove selected
          </button>
        </div>
      )}
      {sorted.length === 0 ? (
        <p className="empty">No tickers yet -- add some from the dashboard.</p>
      ) : (
        sorted.map((tk) => (
          <div className="wl-row" key={tk}>
            <input type="checkbox" checked={selected.has(tk)} onChange={(e) => onToggle(tk, e.target.checked)} />
            <a className="tk" href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tk)}`} target="_blank" rel="noopener noreferrer">
              {tk}
            </a>
            <WatchlistBadges stats={statsFor(byTicker[tk], name)} />
            <button className="wl-rmbtn" type="button" title="Remove" onClick={() => onRemove(tk)}>
              &times;
            </button>
          </div>
        ))
      )}
    </div>
  );
}

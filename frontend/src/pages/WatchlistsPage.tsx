import { useState } from "react";
import { useTickers } from "../hooks/useTickers";
import { useWatchlists } from "../hooks/useWatchlists";
import { WatchlistColumn } from "../components/organisms/WatchlistColumn";
import { WATCHLIST_NAMES } from "../constants/filterDefaults";
import type { TickerPayload } from "../api/types";
import "./WatchlistsPage.css";

export function WatchlistsPage() {
  const { data } = useTickers();
  const { lists, removeFromList, removeMany } = useWatchlists();
  const [selected, setSelected] = useState<Record<string, Set<string>>>(
    Object.fromEntries(WATCHLIST_NAMES.map((n) => [n, new Set<string>()])),
  );

  const byTicker: Record<string, TickerPayload> = {};
  for (const r of data?.tickers ?? []) byTicker[r.ticker] = r;

  function toggle(name: string, ticker: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev[name]);
      if (checked) next.add(ticker);
      else next.delete(ticker);
      return { ...prev, [name]: next };
    });
  }

  function toggleAll(name: string, checked: boolean) {
    setSelected((prev) => ({
      ...prev,
      [name]: checked ? new Set(lists[name] ?? []) : new Set<string>(),
    }));
  }

  function handleRemove(name: string, ticker: string) {
    removeFromList(name, ticker);
    setSelected((prev) => {
      const next = new Set(prev[name]);
      next.delete(ticker);
      return { ...prev, [name]: next };
    });
  }

  function handleRemoveSelected(name: string) {
    removeMany(name, selected[name]);
    setSelected((prev) => ({ ...prev, [name]: new Set<string>() }));
  }

  return (
    <div className="watchlists-page">
      <header>
        <h1>Watchlists</h1>
      </header>
      <div className="watchlist-cols">
        {WATCHLIST_NAMES.map((name) => (
          <WatchlistColumn
            key={name}
            name={name}
            tickers={lists[name] ?? []}
            byTicker={byTicker}
            selected={selected[name]}
            onToggle={(ticker, checked) => toggle(name, ticker, checked)}
            onToggleAll={(checked) => toggleAll(name, checked)}
            onRemove={(ticker) => handleRemove(name, ticker)}
            onRemoveSelected={() => handleRemoveSelected(name)}
          />
        ))}
      </div>
    </div>
  );
}

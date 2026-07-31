import { useCallback, useState } from "react";
import { syncWatchlistTickers } from "../api/tickers";
import { WATCHLIST_NAMES } from "../constants/filterDefaults";

// Watchlists are genuinely client-authoritative (localStorage only) -- the backend never
// returns watchlist membership, it only accepts a flattened, deduped ticker-liveness list so
// the background fetch/compute loop keeps watchlisted tickers alive. Ported from
// index.html/watchlist.html's identical loadWatchlists/saveWatchlists/syncWatchlistTickers,
// which is exactly the duplication this rewrite removes -- one hook, used by both the
// Dashboard's "add to watchlist" action and the Watchlists page.
//
// See docs/superpowers/specs/2026-07-31-react-spa-rewrite-design.md open question #3:
// multi-device sync is explicitly out of scope for this rewrite, kept as-is.

export type Watchlists = Record<string, string[]>;

const STORAGE_KEY = "watchlists";

function readStorage(): Watchlists {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Watchlists) : {};
  } catch {
    return {};
  }
}

export function useWatchlists() {
  const [lists, setLists] = useState<Watchlists>(readStorage);

  const persist = useCallback((next: Watchlists) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setLists(next);
    const allTickers = [...new Set(Object.values(next).flat())];
    syncWatchlistTickers(allTickers).catch(() => {
      // fire-and-forget, matches the old app's behavior -- a failed sync just means the next
      // save (or page load) retries; watchlist membership itself is never lost, it stays in
      // localStorage regardless.
    });
  }, []);

  const addToList = useCallback(
    (listName: (typeof WATCHLIST_NAMES)[number], ticker: string) => {
      const current = readStorage();
      const existing = current[listName] ?? [];
      if (existing.includes(ticker)) return;
      persist({ ...current, [listName]: [...existing, ticker] });
    },
    [persist],
  );

  const removeFromList = useCallback(
    (listName: string, ticker: string) => {
      const current = readStorage();
      persist({ ...current, [listName]: (current[listName] ?? []).filter((t) => t !== ticker) });
    },
    [persist],
  );

  const removeMany = useCallback(
    (listName: string, tickers: Set<string>) => {
      const current = readStorage();
      persist({ ...current, [listName]: (current[listName] ?? []).filter((t) => !tickers.has(t)) });
    },
    [persist],
  );

  return { lists, addToList, removeFromList, removeMany, resync: () => persist(readStorage()) };
}

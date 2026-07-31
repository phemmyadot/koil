import { apiGet, apiPost } from "./client";
import type { MetaResponse, TickersResponse } from "./types";

export function getMeta(): Promise<MetaResponse> {
  return apiGet<MetaResponse>("/api/meta");
}

export function getTickers(refresh = false): Promise<TickersResponse> {
  return apiGet<TickersResponse>(`/api/tickers?refresh=${refresh ? 1 : 0}`);
}

// Fire-and-forget liveness sync -- tells the backend which tickers to keep fetching even if
// they later fail the technical screening filter. Watchlist membership itself stays client-
// side (see hooks/useWatchlists.ts); this only sends the flattened, deduped ticker set.
export function syncWatchlistTickers(tickers: string[]): Promise<{ ok: true }> {
  return apiPost<{ ok: true }>("/api/watchlist-tickers", tickers);
}

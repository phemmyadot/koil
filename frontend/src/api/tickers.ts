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

export interface FetchOneTickerResponse {
  ticker: string;
  price: number;
  date: string;
  found: true;
}

// One-off fetch+compute for a ticker outside the screened universe, so "+ Add Trade" has real
// price data to prefill the trade form from. See
// docs/superpowers/specs/2026-08-01-add-trade-untracked-ticker-design.md. Throws ApiError (422)
// with the backend's real error text if the ticker has no data.
export function fetchOneTicker(ticker: string): Promise<FetchOneTickerResponse> {
  return apiPost<FetchOneTickerResponse>("/api/tickers/fetch-one", { ticker });
}

import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getFilterDefaults, getMeta, getTickers } from "../api/tickers";

// Polls /api/meta at a fast cadence only while a fetch/compute cycle is actively running --
// mirrors the old startRealProgressPolling behavior (index.html), but as a plain React Query
// refetchInterval instead of a hand-rolled setInterval.
const IDLE_POLL_MS = 20_000; // matches the old BACKGROUND_WATCH_MS (checkForBackgroundRefresh)
const ACTIVE_POLL_MS = 500;

export function useMeta() {
  return useQuery({
    queryKey: ["meta"],
    queryFn: getMeta,
    refetchInterval: (query) => {
      const data = query.state.data;
      const active = !!(data?.fetch_progress || data?.compute_progress);
      return active ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    },
  });
}

export function useFilterDefaults() {
  return useQuery({
    queryKey: ["filter-defaults"],
    queryFn: getFilterDefaults,
    staleTime: Infinity,
  });
}

export function useTickers() {
  const { data: meta } = useMeta();
  const queryClient = useQueryClient();
  const wasActive = useRef(false);

  const active = !!(meta?.fetch_progress || meta?.compute_progress);

  // Transition detection: a cycle just finished (was active, now isn't) -> refetch the ticker
  // list once, same as the old waitUntilDone() in checkForBackgroundRefresh -- catches both a
  // manual refresh and the backend's own scheduled 2h background loop.
  useEffect(() => {
    if (wasActive.current && !active) {
      queryClient.invalidateQueries({ queryKey: ["tickers"] });
    }
    wasActive.current = active;
  }, [active, queryClient]);

  return useQuery({
    queryKey: ["tickers"],
    queryFn: () => getTickers(false),
  });
}

// Manual refresh: kicks the backend's background refresh (fire-and-forget, matches the old
// /api/tickers?refresh=1 behavior of returning immediately), then invalidates meta so the
// active-poll cadence picks up right away instead of waiting up to IDLE_POLL_MS.
export function useRefreshTickers() {
  const queryClient = useQueryClient();
  return async () => {
    await getTickers(true);
    await queryClient.invalidateQueries({ queryKey: ["meta"] });
  };
}

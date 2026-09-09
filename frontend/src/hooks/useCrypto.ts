import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getCryptoMeta, getCryptoSignals, refreshCryptoSignals } from "../api/crypto";

// Mirrors useTickers.ts's meta-driven active/idle poll split exactly.
const IDLE_POLL_MS = 20_000;
const ACTIVE_POLL_MS = 500;

export function useCryptoMeta() {
  return useQuery({
    queryKey: ["crypto-meta"],
    queryFn: getCryptoMeta,
    refetchInterval: (query) => {
      const data = query.state.data;
      const active = !!(data?.fetch_progress || data?.compute_progress);
      return active ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    },
  });
}

export function useCryptoSignals() {
  const { data: meta } = useCryptoMeta();
  const queryClient = useQueryClient();
  const wasActive = useRef(false);

  const active = !!(meta?.fetch_progress || meta?.compute_progress);

  // Transition detection (was active, now isn't) -- refetch signals once a cycle finishes,
  // instead of polling the full payload on a flat interval.
  useEffect(() => {
    if (wasActive.current && !active) {
      queryClient.invalidateQueries({ queryKey: ["crypto-signals"] });
    }
    wasActive.current = active;
  }, [active, queryClient]);

  return useQuery({
    queryKey: ["crypto-signals"],
    queryFn: () => getCryptoSignals(false),
  });
}

export function useRefreshCryptoSignals() {
  const queryClient = useQueryClient();
  return async () => {
    await refreshCryptoSignals();
    await queryClient.invalidateQueries({ queryKey: ["crypto-meta"] });
  };
}

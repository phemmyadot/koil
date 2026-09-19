import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getCryptoV2Meta, getCryptoV2Signals, refreshCryptoV2Signals } from "../api/cryptoV2";

// Mirrors useCrypto.ts exactly -- same active/idle poll split.
const IDLE_POLL_MS = 20_000;
const ACTIVE_POLL_MS = 500;

export function useCryptoV2Meta() {
  return useQuery({
    queryKey: ["crypto-v2-meta"],
    queryFn: getCryptoV2Meta,
    refetchInterval: (query) => {
      const data = query.state.data;
      const active = !!(data?.fetch_progress || data?.compute_progress);
      return active ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    },
  });
}

export function useCryptoV2Signals() {
  const { data: meta } = useCryptoV2Meta();
  const queryClient = useQueryClient();
  const wasActive = useRef(false);

  const active = !!(meta?.fetch_progress || meta?.compute_progress);

  useEffect(() => {
    if (wasActive.current && !active) {
      queryClient.invalidateQueries({ queryKey: ["crypto-v2-signals"] });
    }
    wasActive.current = active;
  }, [active, queryClient]);

  return useQuery({
    queryKey: ["crypto-v2-signals"],
    queryFn: () => getCryptoV2Signals(false),
  });
}

export function useRefreshCryptoV2Signals() {
  const queryClient = useQueryClient();
  return async () => {
    await refreshCryptoV2Signals();
    await queryClient.invalidateQueries({ queryKey: ["crypto-v2-meta"] });
  };
}

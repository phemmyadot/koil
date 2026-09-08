import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getCryptoSignals, refreshCryptoSignals } from "../api/crypto";

const POLL_MS = 60_000; // crypto's background loop runs every 30 min -- no need for equity's active/idle split

export function useCryptoSignals() {
  return useQuery({
    queryKey: ["crypto-signals"],
    queryFn: () => getCryptoSignals(false),
    refetchInterval: POLL_MS,
  });
}

export function useRefreshCryptoSignals() {
  const queryClient = useQueryClient();
  return async () => {
    await refreshCryptoSignals();
    await queryClient.invalidateQueries({ queryKey: ["crypto-signals"] });
  };
}

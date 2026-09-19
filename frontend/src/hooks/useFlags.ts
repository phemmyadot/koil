import { useQuery } from "@tanstack/react-query";
import { getFlags } from "../api/flags";

// Deploy-time feature flags -- no refetchInterval (unlike useMeta's active-cadence polling for
// progress data): flags only change on a backend restart, so the global 10s staleTime
// (main.tsx) is already more than enough.
export function useFlags() {
  return useQuery({
    queryKey: ["flags"],
    queryFn: getFlags,
  });
}

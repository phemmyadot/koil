import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/positions";
import type { Fill } from "../api/types";

// Single position + its fills + its daily marks -- the 3 calls position.html's `load()` made
// in one Promise.all, now as 3 independent queries sharing a query-key prefix so mutations
// (add fill, edit fill, close, cancel) can invalidate all 3 together via ["position", id].

export function usePosition(id: number) {
  return useQuery({
    queryKey: ["position", id],
    queryFn: () => api.getPosition(id),
    enabled: !!id,
  });
}

export function useFills(id: number) {
  return useQuery({
    queryKey: ["position", id, "fills"],
    queryFn: () => api.listFills(id),
    enabled: !!id,
  });
}

export function useMarks(id: number) {
  return useQuery({
    queryKey: ["position", id, "marks"],
    queryFn: () => api.getMarks(id),
    enabled: !!id,
  });
}

function useInvalidatePosition(id: number) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["position", id] });
    queryClient.invalidateQueries({ queryKey: ["positions"] });
  };
}

export function useUpdatePosition(id: number) {
  const invalidate = useInvalidatePosition(id);
  return useMutation({
    mutationFn: (body: Parameters<typeof api.updatePosition>[1]) => api.updatePosition(id, body),
    onSuccess: invalidate,
  });
}

export function useCancelPosition(id: number) {
  const invalidate = useInvalidatePosition(id);
  return useMutation({
    mutationFn: () => api.cancelPosition(id),
    onSuccess: invalidate,
  });
}

export function useUpdateFill(positionId: number) {
  const invalidate = useInvalidatePosition(positionId);
  return useMutation({
    mutationFn: ({ fillId, body }: { fillId: number; body: Partial<Fill> }) =>
      api.updateFill(positionId, fillId, body),
    onSuccess: invalidate,
  });
}

export function useDeleteFill(positionId: number) {
  const invalidate = useInvalidatePosition(positionId);
  return useMutation({
    mutationFn: (fillId: number) => api.deleteFill(positionId, fillId),
    onSuccess: invalidate,
  });
}

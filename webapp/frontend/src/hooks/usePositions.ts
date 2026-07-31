import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/positions";
import type { AddFillBody, CreatePositionBody, PositionStatus } from "../api/types";

export function usePositions(status?: PositionStatus) {
  return useQuery({
    queryKey: ["positions", status ?? "all"],
    queryFn: () => api.listPositions(status),
  });
}

export function usePositionsSummary() {
  return useQuery({
    queryKey: ["positions", "summary"],
    queryFn: api.getPositionsSummary,
  });
}

function useInvalidatePositions() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["positions"] });
}

export function useCreatePosition() {
  const invalidate = useInvalidatePositions();
  return useMutation({
    mutationFn: (body: CreatePositionBody) => api.createPosition(body),
    onSuccess: invalidate,
  });
}

export function useAddFill(positionId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AddFillBody) => api.addFill(positionId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["positions"] });
      queryClient.invalidateQueries({ queryKey: ["position", positionId] });
    },
  });
}

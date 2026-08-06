import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/positions";
import type { PositionType } from "../api/positions";
import type { AddFillBody, CreatePositionBody, PositionStatus } from "../api/types";

export function usePositions(status?: PositionStatus, type?: PositionType) {
  return useQuery({
    queryKey: ["positions", status ?? "all", type ?? "all"],
    queryFn: () => api.listPositions(status, type),
  });
}

export function usePositionsSummary(type?: PositionType) {
  return useQuery({
    queryKey: ["positions", "summary", type ?? "all"],
    queryFn: () => api.getPositionsSummary(type),
  });
}

export function usePnlSeries(type?: PositionType) {
  return useQuery({
    queryKey: ["positions", "pnl-series", type ?? "all"],
    queryFn: () => api.getPnlSeries(type),
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

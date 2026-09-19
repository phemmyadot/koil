import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/positions";
import type { PositionMarket, PositionType } from "../api/positions";
import type { AddFillBody, CreatePositionBody, PositionStatus } from "../api/types";

export function usePositions(status?: PositionStatus, type?: PositionType, market?: PositionMarket) {
  return useQuery({
    queryKey: ["positions", status ?? "all", type ?? "all", market ?? "all"],
    queryFn: () => api.listPositions(status, type, market),
  });
}

export function usePositionsSummary(type?: PositionType, market?: PositionMarket) {
  return useQuery({
    queryKey: ["positions", "summary", type ?? "all", market ?? "all"],
    queryFn: () => api.getPositionsSummary(type, market),
  });
}

export function usePnlSeries(type?: PositionType, market?: PositionMarket) {
  return useQuery({
    queryKey: ["positions", "pnl-series", type ?? "all", market ?? "all"],
    queryFn: () => api.getPnlSeries(type, market),
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

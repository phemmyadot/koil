import { apiDelete, apiGet, apiPatch, apiPost } from "./client";
import type {
  AddFillBody,
  CreatePositionBody,
  DailyMark,
  Fill,
  PnlSeriesResponse,
  Position,
  PositionsSummary,
  PositionStatus,
} from "./types";

export function createPosition(body: CreatePositionBody): Promise<Position> {
  return apiPost<Position>("/api/positions", body);
}

export function addFill(positionId: number, body: AddFillBody): Promise<Position> {
  return apiPost<Position>(`/api/positions/${positionId}/fills`, body);
}

export type PositionType = "spot" | "options";

function typeQueryParam(type?: PositionType): string {
  return type ? `type=${type}` : "";
}

export function listPositions(status?: PositionStatus, type?: PositionType): Promise<Position[]> {
  const params = [status ? `status=${status}` : "", typeQueryParam(type)].filter(Boolean).join("&");
  return apiGet<Position[]>(`/api/positions${params ? `?${params}` : ""}`);
}

export function getPositionsSummary(type?: PositionType): Promise<PositionsSummary> {
  const qs = typeQueryParam(type);
  return apiGet<PositionsSummary>(`/api/positions/summary${qs ? `?${qs}` : ""}`);
}

export function getPnlSeries(type?: PositionType): Promise<PnlSeriesResponse> {
  const qs = typeQueryParam(type);
  return apiGet<PnlSeriesResponse>(`/api/positions/pnl-series${qs ? `?${qs}` : ""}`);
}

export function getPosition(id: number): Promise<Position> {
  return apiGet<Position>(`/api/positions/${id}`);
}

export function updatePosition(
  id: number,
  body: Partial<Pick<Position, "tp_price" | "stop_price" | "notes">>,
): Promise<Position> {
  return apiPatch<Position>(`/api/positions/${id}`, body);
}

export function cancelPosition(id: number): Promise<{ ok: true }> {
  return apiDelete<{ ok: true }>(`/api/positions/${id}`);
}

export function updateFill(
  positionId: number,
  fillId: number,
  body: Partial<Fill>,
): Promise<Position> {
  return apiPatch<Position>(`/api/positions/${positionId}/fills/${fillId}`, body);
}

export function deleteFill(
  positionId: number,
  fillId: number,
): Promise<{ ok: true; position_deleted: boolean }> {
  return apiDelete<{ ok: true; position_deleted: boolean }>(
    `/api/positions/${positionId}/fills/${fillId}`,
  );
}

export function listFills(positionId: number): Promise<Fill[]> {
  return apiGet<Fill[]>(`/api/positions/${positionId}/fills`);
}

export function getMarks(positionId: number): Promise<DailyMark[]> {
  return apiGet<DailyMark[]>(`/api/positions/${positionId}/marks`);
}

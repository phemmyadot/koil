import { apiPost, apiPostBlob } from "./client";
import type { EstimateEntryResponse, StrategyKey } from "./types";

export function estimateEntry(ticker: string, strategy: StrategyKey): Promise<EstimateEntryResponse> {
  return apiPost<EstimateEntryResponse>("/api/estimate_entry", { ticker, strategy });
}

export interface ExportBody {
  tickers: string[];
  strategy: StrategyKey;
  timezone: string;
}

async function downloadBlob(path: string, body: ExportBody): Promise<void> {
  const { blob, filename } = await apiPostBlob(path, body);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename ?? "export";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function exportPdf(body: ExportBody): Promise<void> {
  return downloadBlob("/api/export/pdf", body);
}

export function exportCsv(body: ExportBody): Promise<void> {
  return downloadBlob("/api/export/csv", body);
}

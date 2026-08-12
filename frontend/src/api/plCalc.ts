import { apiGet, apiPost } from "./client";
import type { EstimateEntryResponse, StrategyKey } from "./types";

export function estimateEntry(ticker: string, strategy: StrategyKey): Promise<EstimateEntryResponse> {
  return apiPost<EstimateEntryResponse>("/api/estimate_entry", { ticker, strategy });
}

// Always the full current pending+open-signal set -- no ticker selection, no format choice.
// See docs/superpowers/specs/2026-08-11-dashboard-md-export-design.md.
export async function getDashboardExportMarkdown(): Promise<string> {
  const { markdown } = await apiGet<{ markdown: string }>("/api/export/dashboard-md");
  return markdown;
}

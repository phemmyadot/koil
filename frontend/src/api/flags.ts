import { apiGet } from "./client";
import type { FlagsResponse } from "./types";

export function getFlags(): Promise<FlagsResponse> {
  return apiGet<FlagsResponse>("/api/flags");
}

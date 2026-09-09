import { apiGet, apiPost } from "./client";

// Mirrors strategy_common.evaluate_strategy()'s shape (see api/types.ts's StrategyResult) --
// crypto only ever runs strategy_vcp, so this is a narrower version of TickerPayload.
export interface CryptoStrategyResult {
  signal_today: boolean;
  open_position: {
    entry_date: string;
    entry_price: number;
    target: number;
    to_tp_pct: number;
    days_held: number;
    unrealized_pct: number;
    mae_pct: number;
    stop: number | null;
  } | null;
  verdict: string;
  verdict_reason: string;
  first_trade_date: string | null;
  n_trades: number;
  win_rate: number;
  profit_factor: number;
  avg_trade_days: number | null;
}

export interface CryptoTickerPayload {
  ticker: string;
  price: number;
  date: string;
  strategy_vcp: CryptoStrategyResult;
}

export interface CryptoSignalsResponse {
  asof: string | null;
  cached: boolean;
  tickers: CryptoTickerPayload[];
  errors: Record<string, string>;
}

export function getCryptoSignals(refresh = false): Promise<CryptoSignalsResponse> {
  return apiGet<CryptoSignalsResponse>(`/api/crypto/signals?refresh=${refresh ? 1 : 0}`);
}

export function refreshCryptoSignals(): Promise<{ started: true }> {
  return apiPost<{ started: true }>("/api/crypto/refresh");
}

// Mirrors equity's MetaResponse fetch_progress/compute_progress shape (api/tickers.ts).
export interface CryptoMetaResponse {
  total_tickers: number;
  last_fetch: string | null;
  fetch_progress: { done: number; total: number } | null;
  compute_progress: { done: number; total: number } | null;
}

export function getCryptoMeta(): Promise<CryptoMetaResponse> {
  return apiGet<CryptoMetaResponse>("/api/crypto/meta");
}

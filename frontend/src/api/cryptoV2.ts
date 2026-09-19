import { apiGet, apiPost } from "./client";

// Mirrors backend/crypto_v2/engine.py's evaluate() return shape exactly (hand-written, no
// OpenAPI codegen in this project -- same convention as api/types.ts).

export interface CryptoV2Signal {
  setup_type: string;
  side: "LONG";
  setup_score: number;
  score_breakdown: Record<string, number>;
  components: Record<string, boolean>;
}

// Same shape as api/types.ts's OpenPosition -- v2's build_open_position() call reuses the exact
// same backend helper v1 does.
export interface CryptoV2OpenPosition {
  entry_date: string;
  entry_price: number;
  target: number;
  to_tp_pct: number;
  days_held: number;
  unrealized_pct: number;
  mae_pct: number;
  stop: number | null;
}

export interface CryptoV2FeatureSnapshot {
  close: number | null;
  atr_pct: number | null;
  rvol: number | null;
  rsi: number | null;
  rs_btc: number | null;
  rs_percentile: number | null;
  bullish_bos: boolean;
}

export interface CryptoV2TickerPayload {
  ticker: string;
  date: string;
  price: number | null;
  strategy_name: string;
  strategy_version: string;
  config_hash: string;
  n_trades: number;
  win_rate: number;
  profit_factor: number;
  avg_trade_days: number | null;
  open_position: CryptoV2OpenPosition | null;
  open_setup_type: string | null;
  signals_today: CryptoV2Signal[];
  execution_score: number;
  feature_snapshot: CryptoV2FeatureSnapshot;
  // Attached by the API route (crypto_v2_signals in app.py) from the last discovery pass, not
  // part of evaluate()'s own payload.
  category: string | null;
}

export interface CryptoV2SignalsResponse {
  asof: string | null;
  cached: boolean;
  tickers: CryptoV2TickerPayload[];
  errors: Record<string, string>;
}

export interface CryptoV2MetaResponse {
  total_tickers: number;
  last_fetch: number | null;
  fetch_progress: { done: number; total: number } | null;
  compute_progress: { done: number; total: number } | null;
}

export function getCryptoV2Signals(refresh = false): Promise<CryptoV2SignalsResponse> {
  return apiGet<CryptoV2SignalsResponse>(`/api/crypto-v2/signals?refresh=${refresh ? 1 : 0}`);
}

export function refreshCryptoV2Signals(): Promise<{ started: true }> {
  return apiPost<{ started: true }>("/api/crypto-v2/refresh");
}

export function getCryptoV2Meta(): Promise<CryptoV2MetaResponse> {
  return apiGet<CryptoV2MetaResponse>("/api/crypto-v2/meta");
}

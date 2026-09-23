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
  // Manual-validation aid: last 5 trades' actual entry/exit dates+prices (not just days/pnl%),
  // plus the bars' fetched date range, so this can be cross-checked against a TradingView chart.
  last5_trades_detailed: {
    entry_date: string;
    entry_price: number;
    exit_date: string;
    exit_price: number;
    pnl_pct: number;
  }[];
  data_range: { start: string; end: string; n_bars: number };
}

export interface CryptoTickerPayload {
  ticker: string;
  price: number;
  date: string;
  strategy_vcp: CryptoStrategyResult;
  // Which venue yfinance's screener attributed this ticker's most recent trade to (e.g.
  // "Coinbase") -- falls back to "CoinMarketCap" itself for thinly-traded coins it can't
  // attribute to a specific exchange. null if not yet captured (e.g. brand-new candidate).
  last_market: string | null;
  // Which API actually supplied this ticker's stored OHLCV bars -- "coinbase" or "yfinance"
  // (fallback for tickers Coinbase doesn't list; unverified against a real exchange).
  price_source: string | null;
  // See TickerPayload.is_new (api/types.ts) -- same one-pass-only "just added" semantics.
  is_new?: boolean;
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

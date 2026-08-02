// Response/request shapes for backend/app.py's /api/* routes. Hand-written from the backend
// source (no OpenAPI codegen in this project) -- keep in sync manually if routes change.

export interface MetaResponse {
  total_tickers: number;
  last_fetch: number | null;
  fetch_progress: { done: number; total: number } | null;
  compute_progress: { done: number; total: number } | null;
  rate_limited_until: number | null;
}

export interface OpenPosition {
  entry_date: string;
  entry_price: number;
  target: number;
  stop: number | null;
  unrealized_pct: number;
  days_held: number;
  to_tp_pct: number;
}

export interface Last5Trade {
  tp_pct: number;
  days: number;
}

export interface StrategyResult {
  n_trades: number;
  profit_factor: number;
  win_rate: number;
  first_trade_date: string | null;
  avg_trade_days: number | null;
  avg_mae_wins_pct: number | null;
  pct_near_zero_mae: number | null;
  avg_mfe_wins_pct: number | null;
  last5_trades: Last5Trade[];
  signal_today: boolean;
  verdict: string;
  verdict_reason: string;
  open_position: OpenPosition | null;
}

export interface PrebreakResult {
  state: string;
  score: number;
  bb_squeeze: boolean;
  vol_dry_up: boolean;
  near_resistance: boolean;
  is_bullish_trend: boolean;
  squeeze_counter: number;
  projected_target: number | null;
  projected_duration: number | null;
}

export type StrategyKey = "vexh" | "strategy_vcp" | "strategy_vcpo";

export interface TickerPayload {
  ticker: string;
  price: number;
  date: string;
  vexh: StrategyResult | null;
  strategy_vcp: StrategyResult | null;
  strategy_vcpo: StrategyResult | null;
  earnings_risk: boolean | null;
  prebreak: PrebreakResult | null;
  setup_score: Partial<Record<StrategyKey, number | null>>;
  _schema_version: number;
}

export interface TickersResponse {
  asof: string | null;
  cached: boolean;
  tickers: TickerPayload[];
  errors: Record<string, string>;
  universe_error: string | null;
}

export interface EstimateEntryResponse {
  mae_floor: number;
  support_used: number;
  support_touches: number | null;
  recommended_limit: number;
  pct_below_current: number;
}

// ---------------- Positions / fills ----------------

export type Instrument = "spot" | "option";
export type FillKind = "entry" | "exit";
export type ExitReason = "tp" | "stop" | "manual" | "expired";
export type PositionStatus = "open" | "closed";

export interface Position {
  id: number;
  ticker: string;
  status: PositionStatus;
  tp_price: number;
  stop_price: number;
  opened_at: string;
  closed_at: string | null;
  last_alert_tp_pct: number | null;
  last_alert_stop_pct: number | null;
  notes: string | null;
  instrument: Instrument;
  units_remaining: number;
  avg_cost: number | null;
  realized_pnl: number;
  fill_count: number;
}

export interface Fill {
  id: number;
  position_id: number;
  strategy_key: string;
  signal_date: string;
  kind: FillKind;
  fill_date: string;
  price: number | null; // spot-only; option fills carry their price in premium instead
  units: number;
  instrument: Instrument;
  exit_reason: ExitReason | null;
  opt_side: "buy" | "sell" | null;
  opt_type: "call" | "put" | null;
  strike: number | null;
  premium: number | null;
  expiry_date: string | null;
  iv_at_entry: number | null;
  notes: string | null;
  created_at: string;
}

export interface DailyMark {
  mark_date: string;
  close_price: number;
  option_value?: number;
}

export interface PositionsSummary {
  open_count: number;
  closed_count: number;
  win_count: number;
  win_rate_pct: number | null;
  avg_return_pct: number | null;
}

export interface CreatePositionBody {
  ticker: string;
  instrument: Instrument;
  strategy_key: string;
  signal_date: string;
  fill_date: string;
  // Spot-only -- options carry their price in premium instead, see
  // docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md.
  price?: number;
  units: number;
  tp_price: number;
  stop_price: number;
  notes?: string | null;
  // option-only
  opt_side?: "buy" | "sell";
  opt_type?: "call" | "put";
  strike?: number;
  premium?: number;
  expiry_date?: string;
  iv_at_entry?: number | null;
}

export interface AddFillBody {
  kind: FillKind;
  instrument: Instrument;
  strategy_key: string;
  signal_date: string;
  fill_date: string;
  // Spot-only -- options carry their price (entry or exit) in premium instead.
  price?: number;
  units: number;
  exit_reason?: ExitReason;
  opt_side?: "buy" | "sell";
  opt_type?: "call" | "put";
  strike?: number;
  premium?: number;
  expiry_date?: string;
  iv_at_entry?: number | null;
}

// ---------------- Notifications ----------------

export interface Notification {
  id: number;
  position_id: number;
  kind: "tp_progress" | "stop_progress";
  pct: number;
  message: string;
  created_at: string;
  read_at: string | null;
}

import { describe, expect, it } from "vitest";
import { hasPendingSignal, maxDaysInTrade, sortCryptoV2Tickers, sortTickers } from "./sorting";
import type { StrategyResult, TickerPayload } from "../api/types";
import type { CryptoV2TickerPayload } from "../api/cryptoV2";

function strat(overrides: Partial<StrategyResult> = {}): StrategyResult {
  return {
    n_trades: 20,
    profit_factor: 2,
    win_rate: 60,
    first_trade_date: null,
    avg_trade_days: null,
    avg_mae_wins_pct: null,
    pct_near_zero_mae: null,
    avg_mfe_wins_pct: null,
    last5_trades: [],
    signal_today: false,
    verdict: "",
    verdict_reason: "",
    open_position: null,
    ...overrides,
  };
}

function ticker(t: string, overrides: Partial<TickerPayload> = {}): TickerPayload {
  return {
    ticker: t,
    price: 100,
    date: "2026-07-31",
    vexh: null,
    strategy_vcp: null,
    strategy_vcpo: null,
    earnings_risk: false,
    days_to_earnings: null,
    prebreak: null,
    setup_score: {},
    _schema_version: 1,
    ...overrides,
  };
}

describe("maxDaysInTrade", () => {
  it("returns null when no strategy has an open position", () => {
    expect(maxDaysInTrade(ticker("A"))).toBeNull();
  });
  it("returns the max days_held across strategies", () => {
    const r = ticker("A", {
      vexh: strat({ open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 3, to_tp_pct: 0 } }),
      strategy_vcp: strat({ open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 7, to_tp_pct: 0 } }),
    });
    expect(maxDaysInTrade(r)).toBe(7);
  });
});

describe("hasPendingSignal", () => {
  it("true when a strategy signaled today with no open position", () => {
    const r = ticker("A", { vexh: strat({ signal_today: true, open_position: null }) });
    expect(hasPendingSignal(r)).toBe(true);
  });
  it("false when the signaling strategy already has an open position", () => {
    const r = ticker("A", {
      vexh: strat({ signal_today: true, open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 1, to_tp_pct: 0 } }),
    });
    expect(hasPendingSignal(r)).toBe(false);
  });
});

describe("sortTickers", () => {
  it("puts pending-signal tickers first", () => {
    const a = ticker("A");
    const b = ticker("B", { vexh: strat({ signal_today: true }) });
    expect(sortTickers([a, b]).map((r) => r.ticker)).toEqual(["B", "A"]);
  });
  it("sorts open-trade tickers by smallest days_held ascending", () => {
    const a = ticker("A", { vexh: strat({ open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 10, to_tp_pct: 0 } }) });
    const b = ticker("B", { vexh: strat({ open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 2, to_tp_pct: 0 } }) });
    expect(sortTickers([a, b]).map((r) => r.ticker)).toEqual(["B", "A"]);
  });
  it("puts tickers with no open trade after ones with an open trade", () => {
    const a = ticker("A");
    const b = ticker("B", { vexh: strat({ open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 2, to_tp_pct: 0 } }) });
    expect(sortTickers([a, b]).map((r) => r.ticker)).toEqual(["B", "A"]);
  });
});

function cryptoV2Ticker(t: string, overrides: Partial<CryptoV2TickerPayload> = {}): CryptoV2TickerPayload {
  return {
    ticker: t,
    date: "2026-07-31",
    price: 100,
    strategy_name: "crypto_v2_multi_setup",
    strategy_version: "2.0.0",
    config_hash: "abc123",
    n_trades: 20,
    win_rate: 60,
    profit_factor: 2,
    avg_trade_days: null,
    open_position: null,
    open_setup_type: null,
    signals_today: [],
    execution_score: 5,
    feature_snapshot: { close: 100, atr_pct: null, rvol: null, rsi: null, rs_btc: null, rs_percentile: null, bullish_bos: false },
    category: "large_cap",
    ...overrides,
  };
}

describe("sortCryptoV2Tickers", () => {
  it("puts tickers with a live signal and no open position first", () => {
    const a = cryptoV2Ticker("A");
    const b = cryptoV2Ticker("B", { signals_today: [{ setup_type: "breakout", side: "LONG", setup_score: 6, score_breakdown: {}, components: {} }] });
    expect(sortCryptoV2Tickers([a, b]).map((r) => r.ticker)).toEqual(["B", "A"]);
  });
  it("sorts open-position tickers by smallest days_held ascending", () => {
    const a = cryptoV2Ticker("A", { open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 10, to_tp_pct: 0, mae_pct: 0 } });
    const b = cryptoV2Ticker("B", { open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 2, to_tp_pct: 0, mae_pct: 0 } });
    expect(sortCryptoV2Tickers([a, b]).map((r) => r.ticker)).toEqual(["B", "A"]);
  });
  it("a live signal on an already-open position doesn't count as pending", () => {
    const a = cryptoV2Ticker("A");
    const b = cryptoV2Ticker("B", {
      open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 2, to_tp_pct: 0, mae_pct: 0 },
      signals_today: [{ setup_type: "breakout", side: "LONG", setup_score: 6, score_breakdown: {}, components: {} }],
    });
    expect(sortCryptoV2Tickers([a, b]).map((r) => r.ticker)).toEqual(["B", "A"]);
  });
});

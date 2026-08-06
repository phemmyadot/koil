import { describe, expect, it } from "vitest";
import { hasPendingSignal, maxDaysInTrade, sortTickers } from "./sorting";
import type { StrategyResult, TickerPayload } from "../api/types";

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

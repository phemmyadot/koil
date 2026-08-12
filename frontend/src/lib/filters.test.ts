import { describe, expect, it } from "vitest";
import {
  activeMinTradesStrats,
  isStrategyActive,
  matchesAdvFilter,
  matchesMinTrades,
  matchesPrebreakFilter,
  matchesTradeOnFilter,
  strategyNTrades,
} from "./filters";
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

function ticker(overrides: Partial<TickerPayload> = {}): TickerPayload {
  return {
    ticker: "AAA",
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

describe("matchesAdvFilter", () => {
  it("is a no-op when both sliders are at their floor", () => {
    const r = ticker();
    expect(matchesAdvFilter(r, { strategy: "vcpo", wrMin: 0, pfMin: 0 })).toBe(true);
  });
  it("excludes a ticker missing data for the selected strategy once a slider moved", () => {
    const r = ticker();
    expect(matchesAdvFilter(r, { strategy: "vcpo", wrMin: 70, pfMin: 0 })).toBe(false);
  });
  it("requires both win rate and profit factor to clear their minimums", () => {
    const r = ticker({ strategy_vcpo: strat({ win_rate: 80, profit_factor: 1 }) });
    expect(matchesAdvFilter(r, { strategy: "vcpo", wrMin: 70, pfMin: 2.5 })).toBe(false);
    expect(matchesAdvFilter(r, { strategy: "vcpo", wrMin: 70, pfMin: 1 })).toBe(true);
  });
});

describe("isStrategyActive / matchesTradeOnFilter", () => {
  it("is active only when the strategy has an open position", () => {
    const r = ticker({ vexh: strat({ open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 1, to_tp_pct: 0 } }) });
    expect(isStrategyActive(r, "vexh")).toBe(true);
    expect(isStrategyActive(r, "vcp")).toBe(false);
  });
  it("matches everything when no strategies are checked", () => {
    expect(matchesTradeOnFilter(ticker(), [])).toBe(true);
  });
  it("ORs across checked strategies", () => {
    const r = ticker({ vexh: strat({ open_position: { entry_date: "x", entry_price: 1, target: 1, stop: null, unrealized_pct: 0, days_held: 1, to_tp_pct: 0 } }) });
    expect(matchesTradeOnFilter(r, ["vcp", "vexh"])).toBe(true);
    expect(matchesTradeOnFilter(r, ["vcp"])).toBe(false);
  });
});

describe("matchesPrebreakFilter", () => {
  const base = { phaseMin: 2, coilMin: 5, switches: {} };
  it("fails open when prebreak data is missing", () => {
    expect(matchesPrebreakFilter(ticker(), base)).toBe(true);
  });
  it("excludes below the phase/coil floors", () => {
    const r = ticker({ prebreak: { state: "NEUTRAL", score: 0, bb_squeeze: false, vol_dry_up: false, near_resistance: false, is_bullish_trend: false, squeeze_counter: 0, projected_target: null, projected_duration: null, last_7_close: [] } });
    expect(matchesPrebreakFilter(r, base)).toBe(false);
  });
  it("requires a checked switch's field to be true", () => {
    const r = ticker({ prebreak: { state: "COILING (BULL)", score: 2, bb_squeeze: false, vol_dry_up: false, near_resistance: false, is_bullish_trend: false, squeeze_counter: 5, projected_target: null, projected_duration: null, last_7_close: [] } });
    expect(matchesPrebreakFilter(r, { ...base, switches: { squeeze: true } })).toBe(false);
    expect(matchesPrebreakFilter(r, { ...base, switches: {} })).toBe(true);
  });
});

describe("strategyNTrades / matchesMinTrades / activeMinTradesStrats", () => {
  it("returns null for a strategy with no data", () => {
    expect(strategyNTrades(ticker(), "vexh")).toBeNull();
  });
  it("min trades 0 is a no-op", () => {
    expect(matchesMinTrades(ticker(), 0, ["vexh"])).toBe(true);
  });
  it("matches when any active strategy clears the threshold", () => {
    const r = ticker({ vexh: strat({ n_trades: 30 }) });
    expect(matchesMinTrades(r, 15, ["vexh"])).toBe(true);
    expect(matchesMinTrades(r, 15, ["vcp"])).toBe(false);
  });
  it("falls back to all 3 strategies when neither filter narrows", () => {
    expect(activeMinTradesStrats([], { strategy: "vcpo", wrMin: 0, pfMin: 0 })).toEqual(["vexh", "vcp", "vcpo"]);
  });
  it("includes the Advance Filter strategy once a slider moved off default", () => {
    expect(activeMinTradesStrats([], { strategy: "vcpo", wrMin: 70, pfMin: 0 })).toEqual(["vcpo"]);
  });
  it("unions trade-on strategies with the advance-filter strategy", () => {
    expect(new Set(activeMinTradesStrats(["vexh"], { strategy: "vcpo", wrMin: 70, pfMin: 0 }))).toEqual(new Set(["vexh", "vcpo"]));
  });
});

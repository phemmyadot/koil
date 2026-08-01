import { describe, expect, it } from "vitest";
import { sortByLatestTrade, statsFor } from "./watchlistSort";
import type { StrategyResult, TickerPayload } from "../api/types";

function makeStrategyResult(overrides: Partial<StrategyResult> = {}): StrategyResult {
  return {
    n_trades: 20,
    profit_factor: 2.5,
    win_rate: 70,
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

function makeRow(ticker: string, overrides: Partial<TickerPayload> = {}): TickerPayload {
  return {
    ticker,
    price: 100,
    date: "2026-01-01",
    vexh: makeStrategyResult(),
    strategy_vcp: null,
    strategy_vcpo: null,
    earnings_risk: false,
    prebreak: null,
    setup_score: {},
    _schema_version: 1,
    ...overrides,
  };
}

describe("statsFor", () => {
  it("returns null when the row has no data for the list's strategy", () => {
    expect(statsFor(makeRow("AAPL", { vexh: null }), "VEXH List")).toBeNull();
  });

  it("returns null for a missing row", () => {
    expect(statsFor(undefined, "VEXH List")).toBeNull();
  });

  it("extracts open-position days when active", () => {
    const row = makeRow("AAPL", {
      vexh: makeStrategyResult({
        open_position: { entry_date: "2026-01-01", entry_price: 100, target: 110, stop: 95, unrealized_pct: 2, days_held: 3, to_tp_pct: 5 },
      }),
    });
    const stats = statsFor(row, "VEXH List");
    expect(stats!.active).toBe(true);
    expect(stats!.days).toBe(3);
  });
});

describe("sortByLatestTrade", () => {
  it("puts active tickers before inactive ones", () => {
    const byTicker = {
      A: makeRow("A", { vexh: makeStrategyResult({ win_rate: 90 }) }),
      B: makeRow("B", {
        vexh: makeStrategyResult({
          open_position: { entry_date: "2026-01-01", entry_price: 100, target: 110, stop: 95, unrealized_pct: 2, days_held: 5, to_tp_pct: 5 },
        }),
      }),
    };
    expect(sortByLatestTrade(["A", "B"], byTicker, "VEXH List")).toEqual(["B", "A"]);
  });

  it("sorts active tickers by days held ascending (freshest first)", () => {
    const byTicker = {
      A: makeRow("A", {
        vexh: makeStrategyResult({
          open_position: { entry_date: "2026-01-01", entry_price: 100, target: 110, stop: 95, unrealized_pct: 2, days_held: 10, to_tp_pct: 5 },
        }),
      }),
      B: makeRow("B", {
        vexh: makeStrategyResult({
          open_position: { entry_date: "2026-01-01", entry_price: 100, target: 110, stop: 95, unrealized_pct: 2, days_held: 2, to_tp_pct: 5 },
        }),
      }),
    };
    expect(sortByLatestTrade(["A", "B"], byTicker, "VEXH List")).toEqual(["B", "A"]);
  });

  it("sorts inactive tickers by win rate descending", () => {
    const byTicker = {
      A: makeRow("A", { vexh: makeStrategyResult({ win_rate: 60 }) }),
      B: makeRow("B", { vexh: makeStrategyResult({ win_rate: 80 }) }),
    };
    expect(sortByLatestTrade(["A", "B"], byTicker, "VEXH List")).toEqual(["B", "A"]);
  });

  it("sorts no-data tickers to the end", () => {
    const byTicker = {
      A: makeRow("A", { vexh: null }),
      B: makeRow("B", { vexh: makeStrategyResult() }),
    };
    expect(sortByLatestTrade(["A", "B"], byTicker, "VEXH List")).toEqual(["B", "A"]);
  });

  it("falls back to alphabetical when neither ticker has data", () => {
    const byTicker = {
      Z: makeRow("Z", { vexh: null }),
      A: makeRow("A", { vexh: null }),
    };
    expect(sortByLatestTrade(["Z", "A"], byTicker, "VEXH List")).toEqual(["A", "Z"]);
  });
});

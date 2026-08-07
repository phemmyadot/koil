import { describe, expect, it } from "vitest";
import { buildTradesExportMarkdown } from "./tradesExport";
import type { Position, PositionsSummary } from "../api/types";

function makePosition(overrides: Partial<Position> = {}): Position {
  return {
    id: 1,
    ticker: "AAPL",
    status: "open",
    tp_price: 120,
    stop_price: 90,
    opened_at: "2026-01-01T00:00:00Z",
    closed_at: null,
    last_alert_tp_pct: null,
    last_alert_stop_pct: null,
    notes: null,
    instrument: "spot",
    units_remaining: 10,
    units_sold: 0,
    strategy_key: "manual",
    avg_cost: 100,
    realized_pnl: 0,
    realized_pnl_pct: null,
    fill_count: 1,
    current_price: null,
    ...overrides,
  };
}

function makeSummary(overrides: Partial<PositionsSummary> = {}): PositionsSummary {
  return {
    open_count: 0,
    closed_count: 0,
    win_count: 0,
    win_rate_pct: null,
    avg_return_pct: null,
    total_realized_pnl: 0,
    total_unrealized_pnl: 0,
    ...overrides,
  };
}

describe("buildTradesExportMarkdown", () => {
  it("marks an open position with a partial exit as partial-realized", () => {
    const md = buildTradesExportMarkdown(
      [makePosition({ ticker: "PWP", units_remaining: 6, units_sold: 4, avg_cost: 10, current_price: 15, realized_pnl: 20, realized_pnl_pct: 50 })],
      [],
      makeSummary({ open_count: 1 }),
      makeSummary(),
    );
    expect(md).toContain("4 units — $20.00 / +50.0%");
  });

  it("does not add a partial-exit prefix for a position with no exits yet", () => {
    const md = buildTradesExportMarkdown(
      [makePosition({ ticker: "PWP", units_remaining: 1, units_sold: 0, avg_cost: 15, current_price: 17.06, realized_pnl: 0, realized_pnl_pct: null })],
      [],
      makeSummary({ open_count: 1 }),
      makeSummary(),
    );
    expect(md).toContain("| $0.00 |");
    expect(md).not.toContain("units —");
  });

  it("shows full realized $/% for a closed position (not partial-labeled)", () => {
    const md = buildTradesExportMarkdown(
      [makePosition({ ticker: "PWP", status: "closed", units_remaining: 0, units_sold: 4, avg_cost: 17.965, realized_pnl: 16.14, realized_pnl_pct: 22.46 })],
      [],
      makeSummary({ closed_count: 1 }),
      makeSummary(),
    );
    expect(md).toContain("| PWP | 4 | $17.96 | $16.14 | +22.5% |");
  });

  it("scales an option position's current/unrealized totals by the 100x contract multiplier", () => {
    const md = buildTradesExportMarkdown(
      [],
      [makePosition({ ticker: "AAPL", instrument: "option", units_remaining: 1, avg_cost: 200, current_price: 3, realized_pnl: 0, realized_pnl_pct: null })],
      makeSummary(),
      makeSummary({ open_count: 1 }),
    );
    // premium = 200/100 = $2.00, current_total = 3*1*100 = $300, unrealized = 300 - 200 = $100 (+50%)
    expect(md).toContain("| AAPL | Manual | 1 | $2.00 | $3.00 | $300.00 | $100.00 | +50.0% |");
  });

  it("shows the strategy label on an open row", () => {
    const md = buildTradesExportMarkdown(
      [makePosition({ ticker: "PWP", strategy_key: "vexh" })],
      [],
      makeSummary({ open_count: 1 }),
      makeSummary(),
    );
    expect(md).toContain("| PWP | VEXH | 10 |");
  });

  it("shows the empty-state note when a section has no matching positions", () => {
    const md = buildTradesExportMarkdown([], [], makeSummary(), makeSummary());
    expect(md).toContain("*No open positions.*");
    expect(md).toContain("*No closed positions.*");
  });
});

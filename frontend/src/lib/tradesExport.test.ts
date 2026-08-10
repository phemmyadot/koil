import { describe, expect, it } from "vitest";
import { buildTradesExportMarkdown } from "./tradesExport";
import type { Fill, Position, PositionsSummary } from "../api/types";

function makeFill(overrides: Partial<Fill> = {}): Fill {
  return {
    id: 1,
    position_id: 1,
    strategy_key: "manual",
    signal_date: "2026-01-01",
    kind: "entry",
    fill_date: "2026-01-01",
    price: 10,
    units: 5,
    instrument: "spot",
    exit_reason: null,
    opt_side: null,
    opt_type: null,
    strike: null,
    premium: null,
    expiry_date: null,
    iv_at_entry: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

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
    current_iv: null,
    iv_at_entry: null,
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
  it("shows a TP row plus the remainder row for an open position with a partial exit", () => {
    const fills = [
      makeFill({ id: 1, position_id: 1, kind: "entry", fill_date: "2026-01-01", price: 10, units: 10 }),
      makeFill({ id: 2, position_id: 1, kind: "exit", fill_date: "2026-01-05", price: 15, units: 4, exit_reason: "tp" }),
    ];
    const md = buildTradesExportMarkdown(
      [makePosition({ id: 1, ticker: "PWP", units_remaining: 6, units_sold: 4, avg_cost: 10, current_price: 15, realized_pnl: 20, realized_pnl_pct: 50 })],
      [],
      makeSummary({ open_count: 1 }),
      makeSummary(),
      { 1: fills },
    );
    expect(md).toContain("| PWP | TP 1 | 4 | — | — | — | $15.00 | $60.00 | — | — | $20.00 / +50.0% |");
    expect(md).toContain("| PWP | — | 6 |");
  });

  it("shows only the remainder row (no TP rows) for a position with no exits yet", () => {
    const md = buildTradesExportMarkdown(
      [makePosition({ ticker: "PWP", units_remaining: 1, units_sold: 0, avg_cost: 15, current_price: 17.06, realized_pnl: 0, realized_pnl_pct: null })],
      [],
      makeSummary({ open_count: 1 }),
      makeSummary(),
    );
    expect(md).toContain("| $0.00 |");
    expect(md).not.toContain("| TP");
  });

  it("falls back to one aggregated row when a closed position's fills aren't loaded", () => {
    const md = buildTradesExportMarkdown(
      [makePosition({ id: 8, ticker: "PWP", status: "closed", units_remaining: 0, units_sold: 4, avg_cost: 17.965, realized_pnl: 16.14, realized_pnl_pct: 22.46 })],
      [],
      makeSummary({ closed_count: 1 }),
      makeSummary(),
      // no fills passed for position id 8 -- closedTable's defensive fallback path
    );
    expect(md).toContain("| PWP | — | 4 | — | $16.14 | +22.5% |");
  });

  it("shows one row per exit fill (TP1, TP2, final close) for a closed position", () => {
    const fills = [
      makeFill({ id: 1, position_id: 8, kind: "entry", fill_date: "2026-01-01", price: 10, units: 5 }),
      makeFill({ id: 2, position_id: 8, kind: "exit", fill_date: "2026-01-05", price: 12, units: 2, exit_reason: "tp" }),
      makeFill({ id: 3, position_id: 8, kind: "exit", fill_date: "2026-01-10", price: 14, units: 2, exit_reason: "tp" }),
      makeFill({ id: 4, position_id: 8, kind: "exit", fill_date: "2026-01-15", price: 13, units: 1, exit_reason: "manual" }),
    ];
    const md = buildTradesExportMarkdown(
      [makePosition({ id: 8, ticker: "PWP", status: "closed", units_remaining: 0, units_sold: 5 })],
      [],
      makeSummary({ closed_count: 1 }),
      makeSummary(),
      { 8: fills },
    );
    expect(md).toContain("| PWP | TP 1 | 2 | $12.00 | $4.00 | +20.0% |");
    expect(md).toContain("| PWP | TP 2 | 2 | $14.00 | $8.00 | +40.0% |");
    expect(md).toContain("| PWP | Close | 1 | $13.00 | $3.00 | +30.0% |");
  });

  it("scales an option position's current/unrealized totals by the 100x contract multiplier", () => {
    const md = buildTradesExportMarkdown(
      [],
      [makePosition({ ticker: "AAPL", instrument: "option", units_remaining: 1, avg_cost: 200, current_price: 3, realized_pnl: 0, realized_pnl_pct: null })],
      makeSummary(),
      makeSummary({ open_count: 1 }),
    );
    // premium = 200/100 = $2.00, total_cost = 200*1 = $200, current_total = 3*1*100 = $300, unrealized = 300 - 200 = $100 (+50%)
    expect(md).toContain("| AAPL | — | 1 | Manual | $2.00 | $200.00 | $3.00 | $300.00 | $100.00 | +50.0% | — | $0.00 |");
  });

  it("flags IV crush and IV spike in the options open table", () => {
    const md = buildTradesExportMarkdown(
      [],
      [
        makePosition({ ticker: "VTRS", instrument: "option", iv_at_entry: 0.303, current_iv: 0.121 }),
        makePosition({ ticker: "SPIKE", instrument: "option", iv_at_entry: 0.2, current_iv: 0.4 }),
      ],
      makeSummary(),
      makeSummary({ open_count: 2 }),
    );
    expect(md).toContain("-18.2% ⚠️ crush");
    expect(md).toContain("+20.0% 📈 spike");
  });

  it("shows the strategy label on an open row", () => {
    const md = buildTradesExportMarkdown(
      [makePosition({ ticker: "PWP", strategy_key: "vexh" })],
      [],
      makeSummary({ open_count: 1 }),
      makeSummary(),
    );
    expect(md).toContain("| PWP | — | 10 | VEXH |");
  });

  it("shows the empty-state note when a section has no matching positions", () => {
    const md = buildTradesExportMarkdown([], [], makeSummary(), makeSummary());
    expect(md).toContain("*No open positions.*");
    expect(md).toContain("*No closed positions.*");
  });
});

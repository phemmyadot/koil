import { describe, expect, it } from "vitest";
import { computePnlSeries, positionDollarUnrealized, replayAsOf } from "./pnlSeries";
import type { DailyMark, Fill, Position } from "../api/types";

function makeFill(overrides: Partial<Fill> = {}): Fill {
  return {
    id: 1,
    position_id: 1,
    strategy_key: "vexh",
    signal_date: "2026-01-01",
    kind: "entry",
    fill_date: "2026-01-01",
    price: 100,
    units: 10,
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
    avg_cost: 100,
    realized_pnl: 0,
    realized_pnl_pct: null,
    fill_count: 1,
    current_price: null,
    ...overrides,
  };
}

describe("replayAsOf", () => {
  it("tracks units and avg cost after a single entry fill", () => {
    const fills = [makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 100, units: 10 })];
    const state = replayAsOf(fills, "2026-01-05");
    expect(state.units).toBe(10);
    expect(state.avgCost).toBe(100);
    expect(state.realized).toBe(0);
  });

  it("realizes P&L proportionally on a partial exit", () => {
    const fills = [
      makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 100, units: 10 }),
      makeFill({ id: 2, kind: "exit", fill_date: "2026-01-05", price: 110, units: 4 }),
    ];
    const state = replayAsOf(fills, "2026-01-10");
    expect(state.units).toBe(6);
    expect(state.avgCost).toBe(100);
    expect(state.realized).toBeCloseTo((110 - 100) * 4, 5);
  });

  it("ignores fills after the as-of date", () => {
    const fills = [
      makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 100, units: 10 }),
      makeFill({ id: 2, kind: "exit", fill_date: "2026-01-20", price: 110, units: 4 }),
    ];
    const state = replayAsOf(fills, "2026-01-10");
    expect(state.units).toBe(10);
    expect(state.realized).toBe(0);
  });

  it("uses a 100x multiplier for option fills", () => {
    const fills = [
      makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 5, premium: 5, units: 2, instrument: "option" }),
    ];
    const state = replayAsOf(fills, "2026-01-05");
    expect(state.multiplier).toBe(100);
    // avgCost is cost/units where cost already includes the 100x multiplier -- $5 premium,
    // 2 contracts -> $1000 total cost / 2 units = $500 "avg cost" in this internal unit.
    expect(state.avgCost).toBe(500);
  });
});

describe("computePnlSeries", () => {
  it("returns empty series when there are no marks or closed positions", () => {
    const result = computePnlSeries([makePosition()], {}, {});
    expect(result.dates).toEqual([]);
  });

  it("realizes P&L on the date of a full-close exit fill", () => {
    const closed = makePosition({ id: 1, status: "closed", closed_at: "2026-01-10T00:00:00Z", realized_pnl: 500 });
    const fills: Record<number, Fill[]> = {
      1: [
        makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 100, units: 10 }),
        makeFill({ id: 2, kind: "exit", fill_date: "2026-01-10", price: 150, units: 10 }),
      ],
    };
    const result = computePnlSeries([closed], {}, fills);
    expect(result.dates).toEqual(["2026-01-10"]);
    expect(result.realized).toEqual([500]);
  });

  // Regression test: a partial exit on a still-open position must show up in the realized
  // series immediately, not only once (if ever) the position fully closes.
  it("realizes P&L on the date of a partial exit, while the position is still open", () => {
    const open = makePosition({ id: 1, status: "open", units_remaining: 6, realized_pnl: 40 });
    const fills: Record<number, Fill[]> = {
      1: [
        makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 100, units: 10 }),
        makeFill({ id: 2, kind: "exit", fill_date: "2026-01-05", price: 110, units: 4 }),
      ],
    };
    const result = computePnlSeries([open], {}, fills);
    expect(result.dates).toEqual(["2026-01-05"]);
    expect(result.realized).toEqual([40]); // (110 - 100) * 4
  });

  it("keeps a partial exit's realized P&L in the running total on later dates too", () => {
    const open = makePosition({ id: 1, status: "open", units_remaining: 6 });
    const marks: Record<number, DailyMark[]> = { 1: [{ mark_date: "2026-01-08", close_price: 105 }] };
    const fills: Record<number, Fill[]> = {
      1: [
        makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 100, units: 10 }),
        makeFill({ id: 2, kind: "exit", fill_date: "2026-01-05", price: 110, units: 4 }),
      ],
    };
    const result = computePnlSeries([open], marks, fills);
    expect(result.dates).toEqual(["2026-01-05", "2026-01-08"]);
    expect(result.realized).toEqual([40, 40]);
    // Remaining 6 units still mark-to-market as unrealized.
    expect(result.unrealized[1]).toBeCloseTo((105 - 100) * 6, 5);
  });

  it("computes mark-to-market unrealized P&L for an open position using replayed state", () => {
    const open = makePosition({ id: 1, status: "open" });
    const marks: Record<number, DailyMark[]> = { 1: [{ mark_date: "2026-01-05", close_price: 110 }] };
    const fills: Record<number, Fill[]> = {
      1: [makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 100, units: 10 })],
    };
    const result = computePnlSeries([open], marks, fills);
    expect(result.unrealized[0]).toBeCloseTo((110 - 100) * 10, 5);
  });

  it("prefers option_value over close_price when marking an options position", () => {
    const open = makePosition({ id: 1, status: "open", instrument: "option", avg_cost: 5 });
    const marks: Record<number, DailyMark[]> = { 1: [{ mark_date: "2026-01-05", close_price: 999, option_value: 8 }] };
    const fills: Record<number, Fill[]> = {
      1: [makeFill({ id: 1, kind: "entry", fill_date: "2026-01-01", price: 5, premium: 5, units: 2, instrument: "option" })],
    };
    const result = computePnlSeries([open], marks, fills);
    // option_value (8) is per-share; entry premium (5) is also per-share -- multiplier applies once.
    expect(result.unrealized[0]).toBeCloseTo((8 - 5) * 2 * 100, 5);
  });
});

describe("positionDollarUnrealized", () => {
  it("returns 0 when avg_cost is null (fully flat)", () => {
    expect(positionDollarUnrealized(makePosition({ avg_cost: null }), 110, 10)).toBe(0);
  });

  it("returns 0 when units remaining is 0", () => {
    expect(positionDollarUnrealized(makePosition({ avg_cost: 100 }), 110, 0)).toBe(0);
  });

  it("scales by 100 for options, after converting avg_cost back to per-share", () => {
    // avg_cost from the backend is per-contract (500 = $5.00/share premium paid, x100) -- the
    // per-share comparison should use 5.00, not the raw 500.
    const p = makePosition({ instrument: "option", avg_cost: 500 });
    expect(positionDollarUnrealized(p, 8, 2)).toBeCloseTo((8 - 5) * 2 * 100, 5);
  });

  it("scales 1:1 for spot", () => {
    const p = makePosition({ instrument: "spot", avg_cost: 100 });
    expect(positionDollarUnrealized(p, 110, 10)).toBeCloseTo(100, 5);
  });
});

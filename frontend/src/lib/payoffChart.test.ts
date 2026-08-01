import { describe, expect, it } from "vitest";
import { buildPayoffChart, priceFromChartX, CHART_W, PAD } from "./payoffChart";
import type { OptFields } from "./plCalc";

function baseOpt(overrides: Partial<OptFields> = {}): OptFields {
  return {
    side: "buy",
    type: "call",
    K: 105,
    premium: 3,
    contracts: 1,
    S: 100,
    iv: 0.35,
    entryDate: "2026-01-01",
    expiryDate: "2026-01-31",
    dte: 30,
    daysElapsed: 0,
    ...overrides,
  };
}

describe("buildPayoffChart", () => {
  it("produces SVG path strings for both curves", () => {
    const f = baseOpt();
    const chart = buildPayoffChart(f, 0, f.S, 108);
    expect(chart.expPath.startsWith("M")).toBe(true);
    expect(chart.modelPath.startsWith("M")).toBe(true);
  });

  it("places the breakeven marker within chart bounds when it falls in the price range", () => {
    const f = baseOpt();
    const chart = buildPayoffChart(f, 0, f.S, 108);
    expect(chart.breakevenPoint).not.toBeNull();
    expect(chart.breakevenPoint!.x).toBeGreaterThan(PAD.l);
    expect(chart.breakevenPoint!.x).toBeLessThan(560 - PAD.r);
  });

  it("omits the breakeven marker when it falls outside the price range", () => {
    const f = baseOpt({ K: 100000, premium: 1, S: 100000 });
    const chart = buildPayoffChart(f, 0, f.S, 1e9);
    expect(chart.breakevenPoint).toBeNull();
  });

  it("marks the eval point as profitable when model P/L is non-negative", () => {
    const f = baseOpt({ K: 90 }); // deep ITM call
    const chart = buildPayoffChart(f, 0, 120, 93);
    expect(chart.evalProfit).toBe(true);
  });
});

describe("priceFromChartX", () => {
  it("round-trips to roughly the input price at the chart center", () => {
    const f = baseOpt();
    const rectLeft = 0;
    const rectWidth = CHART_W;
    // Center pixel of the plot area (in SVG viewBox units, rectWidth === CHART_W here).
    const centerPx = PAD.l + (CHART_W - PAD.l - PAD.r) / 2;
    const price = priceFromChartX(centerPx, rectLeft, rectWidth, f);
    const [lo, hi] = [
      Math.max(0.01, (f.K + f.S) / 2 - Math.max(f.K * 0.22, Math.abs(f.S - f.K) * 2.2, f.premium * 4, 1)),
      (f.K + f.S) / 2 + Math.max(f.K * 0.22, Math.abs(f.S - f.K) * 2.2, f.premium * 4, 1),
    ];
    expect(price).toBeCloseTo((lo + hi) / 2, 1);
  });

  it("clamps to the price range bounds", () => {
    const f = baseOpt();
    expect(priceFromChartX(-1000, 0, CHART_W, f)).toBeGreaterThanOrEqual(0.01);
    expect(priceFromChartX(1e6, 0, CHART_W, f)).toBeLessThan(1e6);
  });
});

import { describe, expect, it } from "vitest";
import { buildBigChart } from "./bigChart";

describe("buildBigChart", () => {
  it("returns null for no values", () => {
    expect(buildBigChart([], [])).toBeNull();
  });

  it("draws a single dot instead of a path for one data point", () => {
    const chart = buildBigChart([100], ["2026-01-01"]);
    expect(chart!.singlePointDot).not.toBeNull();
  });

  it("omits the single-point dot when there are multiple values", () => {
    const chart = buildBigChart([100, 105, 102], ["2026-01-01", "2026-01-02", "2026-01-03"]);
    expect(chart!.singlePointDot).toBeNull();
    expect(chart!.path.startsWith("M")).toBe(true);
  });

  it("produces 5 y-axis ticks", () => {
    const chart = buildBigChart([100, 110], ["2026-01-01", "2026-01-02"]);
    expect(chart!.yTicks).toHaveLength(5);
  });
});

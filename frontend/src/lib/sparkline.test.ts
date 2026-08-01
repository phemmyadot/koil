import { describe, expect, it } from "vitest";
import { sparklinePath } from "./sparkline";

describe("sparklinePath", () => {
  it("returns an empty string for no values", () => {
    expect(sparklinePath([], 100, 30, 3)).toBe("");
  });

  it("starts with M and uses L for subsequent points", () => {
    const path = sparklinePath([1, 2, 3], 100, 30, 3);
    expect(path.startsWith("M")).toBe(true);
    expect(path.split(" ").filter((s) => s.startsWith("L")).length).toBe(2);
  });

  it("produces a flat horizontal line at mid-height for constant values", () => {
    const path = sparklinePath([5, 5, 5], 100, 30, 0);
    const ys = path.split(" ").map((seg) => parseFloat(seg.slice(1).split(",")[1]));
    expect(new Set(ys).size).toBe(1);
  });
});

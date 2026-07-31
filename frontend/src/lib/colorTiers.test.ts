import { describe, expect, it } from "vitest";
import { pfColorClass, tradeCountColorClass, wrColorClass } from "./colorTiers";

describe("wrColorClass", () => {
  it("returns ok at and above 75", () => {
    expect(wrColorClass(75)).toBe("ok");
    expect(wrColorClass(90)).toBe("ok");
  });
  it("returns mid in [70, 75)", () => {
    expect(wrColorClass(70)).toBe("mid");
    expect(wrColorClass(74.9)).toBe("mid");
  });
  it("returns neutral in [65, 70)", () => {
    expect(wrColorClass(65)).toBe("neutral");
  });
  it("returns no below 65", () => {
    expect(wrColorClass(64.9)).toBe("no");
    expect(wrColorClass(0)).toBe("no");
  });
});

describe("pfColorClass", () => {
  it("returns ok at and above 3.5", () => {
    expect(pfColorClass(3.5)).toBe("ok");
  });
  it("returns mid in [2.5, 3.5)", () => {
    expect(pfColorClass(2.5)).toBe("mid");
  });
  it("returns neutral in [2.0, 2.5)", () => {
    expect(pfColorClass(2.0)).toBe("neutral");
  });
  it("returns no below 2.0", () => {
    expect(pfColorClass(1.9)).toBe("no");
  });
});

describe("tradeCountColorClass", () => {
  it("returns ok at and above 25", () => {
    expect(tradeCountColorClass(25)).toBe("ok");
  });
  it("returns mid in [20, 25)", () => {
    expect(tradeCountColorClass(20)).toBe("mid");
  });
  it("returns neutral in [15, 20)", () => {
    expect(tradeCountColorClass(15)).toBe("neutral");
  });
  it("returns no below 15", () => {
    expect(tradeCountColorClass(14)).toBe("no");
  });
});

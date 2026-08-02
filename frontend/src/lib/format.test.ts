import { describe, expect, it } from "vitest";
import { fmtMoney, fmtPct, fmtUnits, plClass } from "./format";

describe("fmtMoney", () => {
  it("formats positive values with a dollar sign", () => {
    expect(fmtMoney(44.93)).toBe("$44.93");
  });
  it("formats negative values with a leading minus before the sign", () => {
    expect(fmtMoney(-12.5)).toBe("-$12.50");
  });
  it("formats zero as $0.00", () => {
    expect(fmtMoney(0)).toBe("$0.00");
  });
  it("always shows 2 decimal places", () => {
    expect(fmtMoney(5)).toBe("$5.00");
  });
});

describe("fmtPct", () => {
  it("prefixes positive values with +", () => {
    expect(fmtPct(12.34)).toBe("+12.3%");
  });
  it("does not double-prefix negative values", () => {
    expect(fmtPct(-5.6)).toBe("-5.6%");
  });
  it("prefixes exactly zero with + (>=0 branch)", () => {
    expect(fmtPct(0)).toBe("+0.0%");
  });
});

describe("fmtUnits", () => {
  it("trims trailing zeros for a whole number", () => {
    expect(fmtUnits(1)).toBe("1");
  });
  it("trims trailing zeros for a simple fraction", () => {
    expect(fmtUnits(2.5)).toBe("2.5");
  });
  it("shows up to 6 decimal places for a fractional share", () => {
    expect(fmtUnits(0.123456)).toBe("0.123456");
  });
  it("rounds beyond 6 decimal places", () => {
    expect(fmtUnits(0.1234567)).toBe("0.123457");
  });
  it("formats zero as 0, not an empty string", () => {
    expect(fmtUnits(0)).toBe("0");
  });
});

describe("plClass", () => {
  it("returns pos for positive values", () => {
    expect(plClass(0.01)).toBe("pos");
  });
  it("returns neg for negative values", () => {
    expect(plClass(-0.01)).toBe("neg");
  });
  it("returns empty string (neutral) for exactly zero -- the resolved cross-page inconsistency", () => {
    expect(plClass(0)).toBe("");
  });
});

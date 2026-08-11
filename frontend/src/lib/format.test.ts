import { describe, expect, it } from "vitest";
import { fmtMoney, fmtPct, fmtUnits, plClass, prebreakSummaryLine } from "./format";
import type { PrebreakResult } from "../api/types";

function makePrebreak(overrides: Partial<PrebreakResult> = {}): PrebreakResult {
  return {
    state: "PRE-BREAKOUT",
    score: 4,
    bb_squeeze: true,
    vol_dry_up: true,
    near_resistance: true,
    is_bullish_trend: true,
    squeeze_counter: 42,
    projected_target: null,
    projected_duration: null,
    ...overrides,
  };
}

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

describe("prebreakSummaryLine", () => {
  it("matches the confirmed example format exactly", () => {
    expect(prebreakSummaryLine(makePrebreak())).toBe("Pre-Breakout: PRE-BREAKOUT (4), COMPRESSED, DRY, COILING, BULLISH, 42 Bars");
  });
  it("always uses the fixed \"Pre-Breakout: \" prefix regardless of state", () => {
    expect(prebreakSummaryLine(makePrebreak({ state: "BULLISH", score: 1 }))).toMatch(/^Pre-Breakout: /);
  });
  it("renders the false branch of every boolean field", () => {
    const line = prebreakSummaryLine(
      makePrebreak({ bb_squeeze: false, vol_dry_up: false, near_resistance: false, is_bullish_trend: false }),
    );
    expect(line).toBe("Pre-Breakout: PRE-BREAKOUT (4), EXPANDED, NORMAL/HIGH, CLEAR, BEARISH, 42 Bars");
  });
  it("includes squeeze_counter as \"N Bars\"", () => {
    expect(prebreakSummaryLine(makePrebreak({ squeeze_counter: 0 }))).toContain("0 Bars");
  });
});

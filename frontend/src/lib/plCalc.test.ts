import { describe, expect, it } from "vitest";
import {
  computeOptSummary,
  computeSpotCalc,
  optFieldsValid,
  plAtExpiry,
  plBreakeven,
  plPriceRange,
  type OptFields,
} from "./plCalc";

describe("computeSpotCalc", () => {
  it("computes gain/loss pct and risk:reward for a valid long setup", () => {
    const r = computeSpotCalc({ entry: 100, target: 115, stop: 93, size: null });
    expect(r).not.toBeNull();
    expect(r!.gainPct).toBeCloseTo(15, 5);
    expect(r!.lossPct).toBeCloseTo(-7, 5);
    expect(r!.riskReward).toBeCloseTo(15 / 7, 5);
  });

  it("scales gain/loss into dollars when a position size is given", () => {
    const r = computeSpotCalc({ entry: 100, target: 110, stop: 95, size: 1000 });
    expect(r!.gainDollars).toBeCloseTo(100, 5);
    expect(r!.lossDollars).toBeCloseTo(-50, 5);
  });

  it("returns null for an invalid entry price", () => {
    expect(computeSpotCalc({ entry: 0, target: 10, stop: 5, size: null })).toBeNull();
    expect(computeSpotCalc({ entry: NaN, target: 10, stop: 5, size: null })).toBeNull();
  });

  it("returns null risk:reward when stop equals entry", () => {
    const r = computeSpotCalc({ entry: 100, target: 110, stop: 100, size: null });
    expect(r!.riskReward).toBeNull();
  });
});

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

describe("optFieldsValid", () => {
  it("accepts a well-formed field set", () => {
    expect(optFieldsValid(baseOpt())).toBe(true);
  });

  it("rejects non-positive strike or spot", () => {
    expect(optFieldsValid(baseOpt({ K: 0 }))).toBe(false);
    expect(optFieldsValid(baseOpt({ S: -1 }))).toBe(false);
  });

  it("rejects a NaN field", () => {
    expect(optFieldsValid(baseOpt({ iv: NaN }))).toBe(false);
  });
});

describe("plAtExpiry", () => {
  it("pays intrinsic value minus premium for a long call, times contract multiplier", () => {
    const f = baseOpt({ side: "buy", type: "call", K: 100, premium: 3, contracts: 2 });
    expect(plAtExpiry(f, 110)).toBeCloseTo((10 - 3) * 200, 5);
  });

  it("caps loss at premium paid when expiring worthless", () => {
    const f = baseOpt({ side: "buy", type: "call", K: 100, premium: 3, contracts: 1 });
    expect(plAtExpiry(f, 90)).toBeCloseTo(-3 * 100, 5);
  });

  it("flips sign for a short position", () => {
    const long = baseOpt({ side: "buy" });
    const short = baseOpt({ side: "sell" });
    expect(plAtExpiry(short, 120)).toBeCloseTo(-plAtExpiry(long, 120), 5);
  });
});

describe("plBreakeven", () => {
  it("is strike + premium for a call", () => {
    expect(plBreakeven(baseOpt({ type: "call", K: 100, premium: 3 }))).toBe(103);
  });
  it("is strike - premium for a put", () => {
    expect(plBreakeven(baseOpt({ type: "put", K: 100, premium: 3 }))).toBe(97);
  });
});

describe("plPriceRange", () => {
  it("returns a range that spans the strike and spot", () => {
    const [lo, hi] = plPriceRange(baseOpt({ K: 105, S: 100 }));
    expect(lo).toBeLessThan(100);
    expect(hi).toBeGreaterThan(105);
  });

  it("never returns a lower bound below 0.01", () => {
    const [lo] = plPriceRange(baseOpt({ K: 1, S: 1, premium: 0.01 }));
    expect(lo).toBeGreaterThanOrEqual(0.01);
  });
});

describe("computeOptSummary", () => {
  it("reports cost as max risk for a long option", () => {
    const s = computeOptSummary(baseOpt({ side: "buy", contracts: 2, premium: 3 }));
    expect(s.cost).toBeCloseTo(600, 5);
    expect(s.maxLoss).toBe("-$600.00");
  });

  it("reports unlimited max profit for a long call", () => {
    const s = computeOptSummary(baseOpt({ side: "buy", type: "call" }));
    expect(s.maxProfit).toBe("Unlimited");
  });

  it("reports unlimited max loss for a short call", () => {
    const s = computeOptSummary(baseOpt({ side: "sell", type: "call" }));
    expect(s.maxLoss).toBe("Unlimited");
  });
});

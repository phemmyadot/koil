import { describe, expect, it } from "vitest";
import { blackScholes, riskFreeRateFor, RISK_FREE_RATE } from "./blackScholes";

describe("blackScholes", () => {
  it("returns intrinsic value at expiry (T=0) for an ITM call", () => {
    const { price, delta } = blackScholes("call", 110, 100, 0, 0.3);
    expect(price).toBe(10);
    expect(delta).toBe(1);
  });

  it("returns 0 at expiry for an OTM call", () => {
    const { price, delta } = blackScholes("call", 90, 100, 0, 0.3);
    expect(price).toBe(0);
    expect(delta).toBe(0);
  });

  it("returns intrinsic value at expiry for an ITM put", () => {
    const { price, delta } = blackScholes("put", 90, 100, 0, 0.3);
    expect(price).toBe(10);
    expect(delta).toBe(-1);
  });

  // Matches the value independently verified against backend/options_pricing.py's Python port
  // during the trade-tracking feature's implementation (same Abramowitz-Stegun approximation).
  it("prices an ATM call ~30 days out at 30% IV in the expected ballpark", () => {
    const { price } = blackScholes("call", 100, 100, 30 / 365, 0.3);
    expect(price).toBeGreaterThan(3.5);
    expect(price).toBeLessThan(3.7);
  });

  it("put-call price relationship holds for identical ATM inputs", () => {
    const call = blackScholes("call", 100, 100, 30 / 365, 0.3);
    const put = blackScholes("put", 100, 100, 30 / 365, 0.3);
    // ATM call and put should be close but not identical (call slightly higher due to r>0)
    expect(call.price).toBeGreaterThan(put.price);
  });

  it("call delta increases monotonically as spot rises through the strike", () => {
    const deep_otm = blackScholes("call", 80, 100, 30 / 365, 0.3).delta;
    const atm = blackScholes("call", 100, 100, 30 / 365, 0.3).delta;
    const deep_itm = blackScholes("call", 120, 100, 30 / 365, 0.3).delta;
    expect(deep_otm).toBeLessThan(atm);
    expect(atm).toBeLessThan(deep_itm);
  });
});

describe("riskFreeRateFor", () => {
  it("returns the 1-month rate at the 30-day boundary and below", () => {
    expect(riskFreeRateFor(1)).toBe(0.0378);
    expect(riskFreeRateFor(30)).toBe(0.0378);
  });

  it("rolls over to the 2-month rate just past the 30-day boundary", () => {
    expect(riskFreeRateFor(31)).toBe(0.0385);
    expect(riskFreeRateFor(60)).toBe(0.0385);
  });

  it("rolls over to the 3-month rate just past 60 days", () => {
    expect(riskFreeRateFor(61)).toBe(0.0377);
    expect(riskFreeRateFor(90)).toBe(0.0377);
  });

  it("rolls over to the 6-month rate just past 90 days", () => {
    expect(riskFreeRateFor(91)).toBe(0.0398);
    expect(riskFreeRateFor(180)).toBe(0.0398);
  });

  it("rolls over to the 1-year rate just past 180 days", () => {
    expect(riskFreeRateFor(181)).toBe(0.0406);
    expect(riskFreeRateFor(365)).toBe(0.0406);
  });

  it("rolls over to the 2-year (LEAPS) rate just past 365 days", () => {
    expect(riskFreeRateFor(366)).toBe(0.043);
    expect(riskFreeRateFor(730)).toBe(0.043);
  });

  it("falls back to the flat RISK_FREE_RATE beyond 2 years", () => {
    expect(riskFreeRateFor(731)).toBe(RISK_FREE_RATE);
    expect(riskFreeRateFor(3650)).toBe(RISK_FREE_RATE);
  });

  it("handles 0 days to expiry (expiry day)", () => {
    expect(riskFreeRateFor(0)).toBe(0.0378);
  });
});

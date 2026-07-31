import { describe, expect, it } from "vitest";
import { blackScholes } from "./blackScholes";

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

// Black-Scholes option pricing. Ported verbatim from index.html's embedded P/L calculator --
// same Abramowitz-Stegun normal-CDF approximation as backend/options_pricing.py (the backend's
// Python port), kept numerically identical on purpose so client-side P/L calc estimates match
// what the backend computes for stored option_value marks.

export type OptionType = "call" | "put";

export interface OptionPricing {
  price: number;
  delta: number;
  theta: number; // per-day (annual theta / 365), the conventional quoting
}

// Fallback for daysToExpiry beyond every tier below (>2y) -- also the value this project used
// everywhere before the tiered lookup existed.
export const RISK_FREE_RATE = 0.045;

// (days-to-expiry upper bound, rate) -- ordered ascending, first match wins. Mirrors
// backend/options_pricing.py's RISK_FREE_RATE_TIERS exactly (kept numerically identical, same
// convention as the Black-Scholes math itself) -- U.S. Treasury bill/note yields at tenors
// closest to common option expiries. See
// docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md.
export const RISK_FREE_RATE_TIERS: [number, number][] = [
  [30, 0.0378], // ~1 month -- 4-Week T-Bill
  [60, 0.0385], // ~2 months -- 8-Week T-Bill
  [90, 0.0377], // ~3 months -- 13-Week T-Bill
  [180, 0.0398], // ~6 months -- 26-Week T-Bill
  [365, 0.0406], // ~1 year -- 52-Week T-Note
  [730, 0.043], // ~2 years (LEAPS) -- 2-Year T-Note
];

// Rate for whichever tier's upper bound is the first to cover daysToExpiry. Beyond the longest
// tier (2y), falls back to RISK_FREE_RATE rather than extrapolating or erroring.
export function riskFreeRateFor(daysToExpiry: number): number {
  for (const [upperBound, rate] of RISK_FREE_RATE_TIERS) {
    if (daysToExpiry <= upperBound) return rate;
  }
  return RISK_FREE_RATE;
}

function normCdf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x) / Math.SQRT2;
  const a1 = 0.254829592,
    a2 = -0.284496736,
    a3 = 1.421413741,
    a4 = -1.453152027,
    a5 = 1.061405429,
    p = 0.3275911;
  const t = 1 / (1 + p * ax);
  const y = 1 - (((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax));
  return 0.5 * (1 + sign * y);
}

function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

// T in years, iv/r as decimals (0.30 = 30%).
export function blackScholes(
  type: OptionType,
  S: number,
  K: number,
  T: number,
  iv: number,
  r: number = RISK_FREE_RATE,
): OptionPricing {
  if (T <= 0) {
    const intrinsic = type === "call" ? Math.max(S - K, 0) : Math.max(K - S, 0);
    const delta = type === "call" ? (S > K ? 1 : 0) : S < K ? -1 : 0;
    return { price: intrinsic, delta, theta: 0 };
  }
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT);
  const d2 = d1 - iv * sqrtT;
  const disc = Math.exp(-r * T);
  let price: number, delta: number, theta: number;
  if (type === "call") {
    price = S * normCdf(d1) - K * disc * normCdf(d2);
    delta = normCdf(d1);
    theta = (-(S * normPdf(d1) * iv)) / (2 * sqrtT) - r * K * disc * normCdf(d2);
  } else {
    price = K * disc * normCdf(-d2) - S * normCdf(-d1);
    delta = normCdf(d1) - 1;
    theta = (-(S * normPdf(d1) * iv)) / (2 * sqrtT) + r * K * disc * normCdf(-d2);
  }
  return { price, delta, theta: theta / 365 };
}

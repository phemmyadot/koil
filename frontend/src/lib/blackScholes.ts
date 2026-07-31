// Black-Scholes option pricing. Ported verbatim from index.html's embedded P/L calculator --
// same Abramowitz-Stegun normal-CDF approximation as webapp/options_pricing.py (the backend's
// Python port), kept numerically identical on purpose so client-side P/L calc estimates match
// what the backend computes for stored option_value marks.

export type OptionType = "call" | "put";

export interface OptionPricing {
  price: number;
  delta: number;
  theta: number; // per-day (annual theta / 365), the conventional quoting
}

// Fixed assumption, not user-configurable -- minor P&L impact either way.
export const RISK_FREE_RATE = 0.045;

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

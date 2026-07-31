"""Black-Scholes option pricing, stdlib-only (no scipy dependency, matching the project's
existing constraint -- see docs/superpowers/specs/2026-07-29-estimate-entry-design.md).
Python port of the same Abramowitz-Stegun normal-CDF approximation already used client-side in
backend/static/index.html's blackScholes()/normCdf() -- kept numerically identical so a trade's
option value computed here matches what the P/L Calculator would show for the same inputs.
"""
import math

RISK_FREE_RATE = 0.045  # fixed assumption, matches static/index.html's RISK_FREE_RATE


def _norm_cdf(x: float) -> float:
    sign = -1 if x < 0 else 1
    x = abs(x) / math.sqrt(2)
    a1, a2, a3, a4, a5, p = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429, 0.3275911
    t = 1 / (1 + p * x)
    y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1 + sign * y)


def option_price(opt_type: str, S: float, K: float, T: float, iv: float,
                  r: float = RISK_FREE_RATE) -> float:
    """opt_type: 'call' | 'put'. T in years, iv/r as decimals (0.30 = 30%). T <= 0 returns
    intrinsic value (matches blackScholes()'s expiry-day behavior in the frontend)."""
    if T <= 0:
        return max(S - K, 0.0) if opt_type == "call" else max(K - S, 0.0)
    if iv <= 0:
        return max(S - K, 0.0) if opt_type == "call" else max(K - S, 0.0)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    disc = math.exp(-r * T)
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
    return K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)

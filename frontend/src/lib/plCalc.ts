// Pure P/L calculator math. Ported verbatim from index.html's spot/options P/L calculator
// (updateSpotCalc/plAtExpiry/plModelAt/plPriceRange) -- kept separate from the chart/DOM
// concerns in the component so it's independently testable.

import { blackScholes, type OptionType } from "./blackScholes";

export interface SpotCalcInput {
  entry: number;
  target: number;
  stop: number;
  size: number | null;
}

export interface SpotCalcResult {
  gainPct: number;
  lossPct: number;
  riskReward: number | null;
  gainDollars: number | null;
  lossDollars: number | null;
}

export function computeSpotCalc(input: SpotCalcInput): SpotCalcResult | null {
  const { entry, target, stop, size } = input;
  if ([entry, target, stop].some((v) => Number.isNaN(v)) || entry <= 0) return null;
  const gainPct = ((target - entry) / entry) * 100;
  const lossPct = ((stop - entry) / entry) * 100;
  const riskReward = lossPct !== 0 ? Math.abs(gainPct / lossPct) : null;
  const hasSize = size != null && !Number.isNaN(size) && size > 0;
  return {
    gainPct,
    lossPct,
    riskReward,
    gainDollars: hasSize ? (size as number) * (gainPct / 100) : null,
    lossDollars: hasSize ? (size as number) * (lossPct / 100) : null,
  };
}

export interface OptFields {
  side: "buy" | "sell";
  type: OptionType;
  K: number;
  premium: number;
  contracts: number;
  S: number;
  iv: number;
  entryDate: string;
  expiryDate: string;
  dte: number;
  daysElapsed: number;
}

export function optFieldsValid(f: OptFields): boolean {
  return (
    ![f.K, f.premium, f.contracts, f.S, f.iv, f.dte].some((v) => Number.isNaN(v)) &&
    f.K > 0 &&
    f.S > 0 &&
    f.premium >= 0
  );
}

export function plMult(f: OptFields): number {
  return f.contracts * 100;
}
export function plSign(f: OptFields): 1 | -1 {
  return f.side === "buy" ? 1 : -1;
}

export function plAtExpiry(f: OptFields, S: number): number {
  const intrinsic = f.type === "call" ? Math.max(S - f.K, 0) : Math.max(f.K - S, 0);
  return plSign(f) * (intrinsic - f.premium) * plMult(f);
}

// daysFromEntry: position on the entry->expiry timeline (0 = entry date, f.dte = expiration).
export function plModelAt(f: OptFields, S: number, daysFromEntry: number): number {
  const T = Math.max(f.dte - daysFromEntry, 0) / 365;
  return plSign(f) * (blackScholes(f.type, S, f.K, T, f.iv).price - f.premium) * plMult(f);
}

export function plOptionPriceAt(f: OptFields, S: number, daysFromEntry: number): number {
  const T = Math.max(f.dte - daysFromEntry, 0) / 365;
  return blackScholes(f.type, S, f.K, T, f.iv).price;
}

export function plPriceRange(f: OptFields): [number, number] {
  const center = (f.K + f.S) / 2;
  const span = Math.max(f.K * 0.22, Math.abs(f.S - f.K) * 2.2, f.premium * 4, 1);
  return [Math.max(0.01, center - span), center + span];
}

export function plBreakeven(f: OptFields): number {
  return f.type === "call" ? f.K + f.premium : f.K - f.premium;
}

export interface OptSummary {
  cost: number;
  breakeven: number;
  maxProfit: string;
  maxLoss: string;
}

export function computeOptSummary(f: OptFields): OptSummary {
  const cost = f.premium * plMult(f);
  const maxLossLong = cost;
  const maxProfitPut = (f.K - f.premium) * plMult(f);
  const maxProfit =
    f.side === "buy" ? (f.type === "call" ? "Unlimited" : `$${maxProfitPut.toFixed(2)}`) : `$${maxLossLong.toFixed(2)}`;
  const maxLoss =
    f.side === "buy" ? `-$${maxLossLong.toFixed(2)}` : f.type === "call" ? "Unlimited" : `-$${maxProfitPut.toFixed(2)}`;
  return { cost, breakeven: plBreakeven(f), maxProfit, maxLoss };
}

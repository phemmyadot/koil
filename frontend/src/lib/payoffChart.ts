// Payoff-chart geometry for the P/L Calculator's options mode. Ported from index.html's
// buildPayoffChart -- pure coordinate math extracted so it's testable without touching the DOM.

import { plAtExpiry, plModelAt, plPriceRange, type OptFields } from "./plCalc";

export const CHART_W = 560;
export const CHART_H = 240;
export const PAD = { l: 50, r: 14, t: 16, b: 26 };

export interface ChartPoint {
  x: number;
  y: number;
}

export interface PayoffChartData {
  loS: number;
  hiS: number;
  yMin: number;
  yMax: number;
  expPath: string;
  modelPath: string;
  zeroY: number;
  yTicks: { y: number; label: string }[];
  xTicks: { x: number; label: string }[];
  breakevenPoint: ChartPoint | null;
  breakevenLabel: string;
  evalPoint: ChartPoint;
  evalProfit: boolean;
}

function niceStep(range: number): number {
  const raw = range / 5 || 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => range / s <= 6) ?? raw;
}

export function buildPayoffChart(f: OptFields, evalDays: number, evalPrice: number, breakeven: number): PayoffChartData {
  const [loS, hiS] = plPriceRange(f);
  const iw = CHART_W - PAD.l - PAD.r;
  const ih = CHART_H - PAD.t - PAD.b;
  const N = 100;
  const expPts: [number, number][] = [];
  const modelPts: [number, number][] = [];
  let yMin = Infinity;
  let yMax = -Infinity;
  for (let i = 0; i <= N; i++) {
    const S = loS + ((hiS - loS) * i) / N;
    const a = plAtExpiry(f, S);
    const b = plModelAt(f, S, evalDays);
    expPts.push([S, a]);
    modelPts.push([S, b]);
    yMin = Math.min(yMin, a, b);
    yMax = Math.max(yMax, a, b);
  }
  const pad = Math.max((yMax - yMin) * 0.12, 20);
  yMin -= pad;
  yMax += pad;

  const xOf = (S: number) => PAD.l + ((S - loS) / (hiS - loS)) * iw;
  const yOf = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin || 1)) * ih;
  const pathOf = (pts: [number, number][]) =>
    pts.map(([S, v], i) => `${i ? "L" : "M"}${xOf(S).toFixed(1)},${yOf(v).toFixed(1)}`).join(" ");
  const zeroY = Math.min(Math.max(yOf(0), PAD.t), PAD.t + ih);

  const evalX = xOf(Math.min(hiS, Math.max(loS, evalPrice)));
  const evalPL = plModelAt(f, evalPrice, evalDays);
  const evalY = Math.min(Math.max(yOf(evalPL), PAD.t), PAD.t + ih);

  const yTicks: { y: number; label: string }[] = [];
  {
    const step = niceStep(yMax - yMin);
    for (let v = Math.ceil(yMin / step) * step; v <= yMax; v += step) {
      yTicks.push({ y: yOf(v), label: `${v < 0 ? "-" : ""}$${Math.round(Math.abs(v))}` });
    }
  }
  const xTicks: { x: number; label: string }[] = [];
  for (let i = 1; i < 6; i++) {
    const S = loS + ((hiS - loS) * i) / 6;
    xTicks.push({ x: xOf(S), label: `$${S.toFixed(0)}` });
  }

  const breakevenPoint = breakeven > loS && breakeven < hiS ? { x: xOf(breakeven), y: zeroY } : null;

  return {
    loS,
    hiS,
    yMin,
    yMax,
    expPath: pathOf(expPts),
    modelPath: pathOf(modelPts),
    zeroY,
    yTicks,
    xTicks,
    breakevenPoint,
    breakevenLabel: `BE $${breakeven.toFixed(2)}`,
    evalPoint: { x: evalX, y: evalY },
    evalProfit: evalPL >= 0,
  };
}

// Maps a client X pixel (relative to the SVG's bounding rect) back to a price, for drag-to-scrub.
export function priceFromChartX(clientX: number, rectLeft: number, rectWidth: number, f: OptFields): number {
  const px = ((clientX - rectLeft) / rectWidth) * CHART_W;
  const [loS, hiS] = plPriceRange(f);
  const iw = CHART_W - PAD.l - PAD.r;
  const S = loS + ((px - PAD.l) / iw) * (hiS - loS);
  return Math.min(hiS, Math.max(loS, S));
}

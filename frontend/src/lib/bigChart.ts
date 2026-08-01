// Position-detail price/value chart geometry. Ported from position.html's bigChartHtml.

export const CHART_W = 860;
export const CHART_H = 240;
export const PAD = { l: 46, r: 14, t: 14, b: 24 };

export interface BigChartTick {
  y: number;
  label: string;
}
export interface BigChartXTick {
  x: number;
  label: string;
}

export interface BigChartData {
  path: string;
  singlePointDot: { x: number; y: number } | null;
  yTicks: BigChartTick[];
  xTicks: BigChartXTick[];
}

export function buildBigChart(values: number[], dates: string[]): BigChartData | null {
  if (!values.length) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const yPad = (hi - lo) * 0.1 || Math.max(Math.abs(values[0]) * 0.1, 1);
  const yMin = lo - yPad;
  const yMax = hi + yPad;
  const iw = CHART_W - PAD.l - PAD.r;
  const ih = CHART_H - PAD.t - PAD.b;
  const xOf = (i: number) => PAD.l + (i / Math.max(values.length - 1, 1)) * iw;
  const yOf = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * ih;
  const path = values.map((v, i) => `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`).join(" ");
  const singlePointDot = values.length === 1 ? { x: xOf(0), y: yOf(values[0]) } : null;

  const yTicks: BigChartTick[] = [];
  for (let i = 0; i <= 4; i++) {
    const v = yMin + ((yMax - yMin) * i) / 4;
    yTicks.push({ y: yOf(v), label: `$${v.toFixed(2)}` });
  }
  const xTickEvery = Math.max(Math.ceil(values.length / 6), 1);
  const xTicks: BigChartXTick[] = [];
  dates.forEach((d, i) => {
    if (i % xTickEvery === 0) xTicks.push({ x: xOf(i), label: d.slice(5) });
  });

  return { path, singlePointDot, yTicks, xTicks };
}

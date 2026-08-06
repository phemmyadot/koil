import { fmtMoney } from "../../lib/format";
import type { PnlSeriesResponse } from "../../api/types";
import "./PnlChart.css";

const W = 860;
const H = 220;
const PAD = { l: 54, r: 14, t: 14, b: 24 };

export function PnlChart({ series }: { series: PnlSeriesResponse }) {
  const dates = series.dates;
  if (!dates.length) {
    return (
      <svg className="pnl-chart" viewBox={`0 0 ${W} ${H}`}>
        <text x={14} y={20} fill="var(--muted)" fontSize={12}>
          No P&amp;L data yet.
        </text>
      </svg>
    );
  }

  const { realized, unrealized } = series;
  const allVals = [...realized, ...unrealized, 0];
  const yMin = Math.min(...allVals);
  const yMax = Math.max(...allVals);
  const yPad = (yMax - yMin) * 0.1 || 10;
  const yLo = yMin - yPad;
  const yHi = yMax + yPad;
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const xOf = (i: number) => PAD.l + (i / Math.max(dates.length - 1, 1)) * iw;
  const yOf = (v: number) => PAD.t + (1 - (v - yLo) / (yHi - yLo)) * ih;
  const pathOf = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`).join(" ");

  const evenTicks = [0, 1, 2, 3, 4].map((i) => yLo + ((yHi - yLo) * i) / 4);
  const zeroEps = (yHi - yLo) * 0.01;
  const yTicks = evenTicks.some((v) => Math.abs(v) < zeroEps) ? evenTicks : [...evenTicks, 0].sort((a, b) => a - b);
  const xTickEvery = Math.max(Math.ceil(dates.length / 7), 1);
  const zeroY = yOf(0);

  return (
    <>
      <svg className="pnl-chart" viewBox={`0 0 ${W} ${H}`}>
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={yOf(v)} y2={yOf(v)} stroke="var(--line)" strokeWidth={1} />
            <text className="axis-label" x={PAD.l - 6} y={yOf(v) + 3} textAnchor="end">
              {fmtMoney(v)}
            </text>
          </g>
        ))}
        {dates.map((d, i) =>
          i % xTickEvery === 0 ? (
            <text key={d} className="axis-label" x={xOf(i)} y={H - 6} textAnchor="middle">
              {d.slice(5)}
            </text>
          ) : null,
        )}
        <line x1={PAD.l} x2={W - PAD.r} y1={zeroY} y2={zeroY} stroke="var(--muted)" strokeWidth={1} opacity={0.5} />
        <path d={pathOf(unrealized)} fill="none" stroke="var(--accent)" strokeWidth={2} />
        <path d={pathOf(realized)} fill="none" stroke="var(--gold)" strokeWidth={2} />
      </svg>
      <div className="pnl-legend">
        <span>
          <span className="swatch" style={{ background: "var(--accent)" }} /> Unrealized
        </span>
        <span>
          <span className="swatch" style={{ background: "var(--gold)" }} /> Realized
        </span>
      </div>
    </>
  );
}

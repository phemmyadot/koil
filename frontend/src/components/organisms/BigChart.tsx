import { buildBigChart, CHART_H, CHART_W, PAD } from "../../lib/bigChart";
import "./PnlChart.css";

export function BigChart({ values, dates }: { values: number[]; dates: string[] }) {
  const chart = buildBigChart(values, dates);
  if (!chart) {
    return (
      <svg className="pnl-chart" viewBox={`0 0 ${CHART_W} ${CHART_H}`}>
        <text x={14} y={20} fill="var(--muted)" fontSize={12}>
          No daily marks recorded yet.
        </text>
      </svg>
    );
  }
  return (
    <svg className="pnl-chart" viewBox={`0 0 ${CHART_W} ${CHART_H}`}>
      {chart.yTicks.map((t, i) => (
        <g key={i}>
          <line x1={PAD.l} x2={CHART_W - PAD.r} y1={t.y} y2={t.y} stroke="var(--line)" strokeWidth={1} />
          <text className="axis-label" x={PAD.l - 6} y={t.y + 3} textAnchor="end">
            {t.label}
          </text>
        </g>
      ))}
      {chart.xTicks.map((t, i) => (
        <text key={i} className="axis-label" x={t.x} y={CHART_H - 6} textAnchor="middle">
          {t.label}
        </text>
      ))}
      <path d={chart.path} fill="none" stroke="var(--accent)" strokeWidth={2} />
      {chart.singlePointDot && <circle cx={chart.singlePointDot.x} cy={chart.singlePointDot.y} r={4} fill="var(--accent)" />}
    </svg>
  );
}

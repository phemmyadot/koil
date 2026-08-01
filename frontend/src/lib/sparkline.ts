// Tiny SVG sparkline path builder. Ported from trades.html's sparklinePath.

export function sparklinePath(values: number[], w: number, h: number, pad: number): string {
  if (!values.length) return "";
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const iw = w - pad * 2;
  const ih = h - pad * 2;
  return values
    .map((v, i) => {
      const x = pad + (i / Math.max(values.length - 1, 1)) * iw;
      const y = pad + (1 - (v - lo) / span) * ih;
      return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

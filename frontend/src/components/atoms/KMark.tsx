// KOIL's standalone K-mark: a coiled spring settling into a stem, releasing into a breakout
// diagonal -- see docs/superpowers/specs/assets/koil-k-mark.svg (source of truth for the path
// data; this is a 1:1 port as a React component so it themes/scales without an asset request).
export function KMark({ size = 32 }: { size?: number }) {
  return (
    <svg className="k-mark" width={size} height={size} viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="koilGrad" x1="10%" y1="100%" x2="90%" y2="0%">
          <stop offset="0%" stopColor="#0B3191" />
          <stop offset="100%" stopColor="#4D9DFF" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="116" height="116" rx="26" fill="#0A0E17" />
      <g fill="none" stroke="url(#koilGrad)" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M 36 20 L 36 30 L 48 36 L 36 42 L 48 48 L 36 54 L 36 58" />
        <path d="M 36 58 L 36 98" />
        <path d="M 36 58 L 92 24" />
        <path d="M 42 62 L 88 98" />
      </g>
      <circle cx="92" cy="24" r="6.5" fill="#4D9DFF" />
    </svg>
  );
}

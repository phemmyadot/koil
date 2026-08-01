// KOIL's lockup: the K-mark tile followed by "OIL" set tight against it -- see
// docs/superpowers/specs/assets/koil-wordmark.svg (source of truth for the geometry; this is a
// 1:1 port as a React component so it themes/scales without an asset request). Used on desktop
// where there's room for a full wordmark; mobile uses the standalone KMark instead.
export function WordMark({ height = 32 }: { height?: number }) {
  const width = (height * 370) / 120;
  return (
    <svg className="word-mark" width={width} height={height} viewBox="0 0 370 120" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="KOIL">
      <defs>
        <linearGradient id="koilGradWordmark" x1="10%" y1="100%" x2="90%" y2="0%">
          <stop offset="0%" stopColor="#0B3191" />
          <stop offset="100%" stopColor="#4D9DFF" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="116" height="116" rx="26" fill="#0A0E17" />
      <g fill="none" stroke="url(#koilGradWordmark)" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M 36 20 L 36 30 L 48 36 L 36 42 L 48 48 L 36 54 L 36 58" />
        <path d="M 36 58 L 36 98" />
        <path d="M 36 58 L 92 24" />
        <path d="M 42 62 L 88 98" />
      </g>
      <circle cx="92" cy="24" r="6.5" fill="#4D9DFF" />
      <text
        x="128"
        y="86"
        fontFamily="Futura, 'Century Gothic', 'Segoe UI', system-ui, sans-serif"
        fontSize="64"
        fontWeight="700"
        letterSpacing="2"
        fill="currentColor"
      >
        OIL
      </text>
    </svg>
  );
}

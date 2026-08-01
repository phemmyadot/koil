// Setup-quality score, 0-10 (see backend/score.py / scoring.md). Fixed semantic scale, not a
// design token -- ported as-is per the design doc's explicit carve-out for this function.
export function scoreColor(score: number): string {
  if (score >= 9) return "#00b050"; // exceptional
  if (score >= 7) return "#92d050"; // strong
  if (score >= 5) return "#ffbf00"; // acceptable
  if (score >= 3) return "#ff8c00"; // borderline
  return "#ff0000"; // pass
}

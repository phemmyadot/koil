import { useParams } from "react-router-dom";

export function PositionDetailPage() {
  const { positionId } = useParams();
  return (
    <div>
      <h1>Position #{positionId}</h1>
      <p style={{ color: "var(--muted)" }}>Stat grid, chart, fills table -- coming next.</p>
    </div>
  );
}

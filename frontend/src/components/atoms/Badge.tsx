import "./Badge.css";

export interface BadgeProps {
  children: React.ReactNode;
  tone?: "open" | "closed" | "entry" | "exit";
}

// Replaces .statustag/.kindtag from trades.html/position.html.
export function Badge({ children, tone = "open" }: BadgeProps) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

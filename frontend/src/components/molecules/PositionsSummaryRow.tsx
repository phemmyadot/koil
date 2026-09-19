import { StatBox } from "../atoms/StatBox";
import type { PositionsSummary } from "../../api/types";
import { fmtMoney, fmtPct } from "../../lib/format";
import "../../pages/TradesPage.css";

// Shared by the Equity and Crypto Trades pages -- same KPI shape either way (positions/summary
// returns the same fields regardless of market=equity/crypto, see backend/app.py).
export function PositionsSummaryRow({ label, summary }: { label: string; summary: PositionsSummary | undefined }) {
  return (
    <div className="trades-summary-grid">
      <StatBox label={`${label} — Open`} value={summary?.open_count ?? 0} />
      <StatBox label="Closed" value={summary?.closed_count ?? 0} />
      <StatBox label="Win rate" value={summary?.win_rate_pct != null ? `${summary.win_rate_pct}%` : "—"} />
      <StatBox
        label="Avg return"
        value={summary?.avg_return_pct != null ? fmtPct(summary.avg_return_pct) : "—"}
        tone={summary?.avg_return_pct ?? undefined}
      />
      <StatBox
        label="Total unrealized"
        value={summary?.total_unrealized_pnl != null ? fmtMoney(summary.total_unrealized_pnl) : "—"}
        tone={summary?.total_unrealized_pnl ?? undefined}
      />
      <StatBox
        label="Total realized"
        value={summary?.total_realized_pnl != null ? fmtMoney(summary.total_realized_pnl) : "—"}
        tone={summary?.total_realized_pnl ?? undefined}
      />
    </div>
  );
}

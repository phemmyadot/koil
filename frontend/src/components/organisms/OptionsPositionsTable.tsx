import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listFills } from "../../api/positions";
import type { ExitReason, Fill, Position } from "../../api/types";
import { exitLabel, fmtMoney, fmtPct, fmtUnits, plClass } from "../../lib/format";
import { exitBreakdown } from "../../lib/pnlSeries";
import { StrategyCellLink } from "../molecules/StrategyCellLink";
import "./PositionsTable.css";

export interface OptionsPositionsTableProps {
  positions: Position[];
  onExit: (positionId: number, lastFill: Fill, premium: number, units: number, exitReason: ExitReason) => Promise<void>;
  onCancel: (positionId: number) => Promise<void>;
}

// A completed exit fill belongs in the Closed table regardless of whether its parent position
// still has units open (e.g. a partial TP on a position that's 1-of-2 sold) -- this is a
// row-level split, not a position-level one. Fetches fills for every position with at least one
// exit fill (units_sold > 0 OR status closed, covering the "closed with a single full exit"
// case where units_sold still equals units entered).
function ClosedExitsTable({ positions }: { positions: Position[] }) {
  const candidates = positions.filter((p) => p.status === "closed" || p.units_sold > 0);
  const fillsQueries = useQuery({
    queryKey: ["options-closed-exits-fills", candidates.map((p) => p.id)],
    queryFn: async () => {
      const entries = await Promise.all(candidates.map(async (p) => [p.id, await listFills(p.id)] as const));
      return Object.fromEntries(entries) as Record<number, Fill[]>;
    },
    enabled: candidates.length > 0,
  });

  const rows: { p: Position; row: ReturnType<typeof exitBreakdown>[number] }[] = [];
  if (fillsQueries.data) {
    for (const p of candidates) {
      const fills = fillsQueries.data[p.id];
      if (!fills) continue;
      for (const row of exitBreakdown(fills)) rows.push({ p, row });
    }
  }

  return (
    <div className="table-scroll">
      <table className="postable">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Exit</th>
            <th>Units</th>
            <th>Premium</th>
            <th>Total</th>
            <th>Realized $ / %</th>
          </tr>
        </thead>
        <tbody>
          {candidates.length === 0 ? (
            <tr>
              <td colSpan={6} className="empty">
                No closed exits yet.
              </td>
            </tr>
          ) : fillsQueries.isLoading ? (
            <tr>
              <td colSpan={6}>Loading…</td>
            </tr>
          ) : (
            rows.map(({ p, row }) => (
              <tr key={row.fillId}>
                <td>
                  <Link className="tk-link" to={`/trades/${p.id}`}>
                    {p.ticker}
                  </Link>
                </td>
                <td>
                  <span className="status-tag closed">{exitLabel(row.exitReason, row.tpIndex)}</span>
                </td>
                <td>{fmtUnits(row.units)}</td>
                <td>{fmtMoney(row.exitValue)}</td>
                <td>{fmtMoney(row.exitValue * row.units * 100)}</td>
                <td>
                  <b className={plClass(row.realizedDollar)}>
                    {fmtMoney(row.realizedDollar)}
                    {row.realizedPct != null && (
                      <>
                        <br />
                        {fmtPct(row.realizedPct)}
                      </>
                    )}
                  </b>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function PositionRow({
  p,
  onExit,
  onCancel,
}: {
  p: Position;
  onExit: OptionsPositionsTableProps["onExit"];
  onCancel: OptionsPositionsTableProps["onCancel"];
}) {
  const [exitOpen, setExitOpen] = useState(false);
  const [exitPremium, setExitPremium] = useState("");
  const [exitUnits, setExitUnits] = useState(String(p.units_remaining));
  const [exitReason, setExitReason] = useState<ExitReason>("tp");
  const [submitting, setSubmitting] = useState(false);

  // Fetched only when the Exit form is actually opened -- handleExit needs the position's last
  // fill (strategy_key, opt_side/opt_type/strike/expiry_date -- required on every option fill).
  const fillsQuery = useQuery({
    queryKey: ["position", p.id, "fills"],
    queryFn: () => listFills(p.id),
    enabled: exitOpen,
  });
  const lastFill = fillsQuery.data?.[fillsQuery.data.length - 1];

  // avg_cost has the 100x contract multiplier baked in (see replay_fills) -- unwind to
  // per-share premium before comparing against current_price, which is the modeled per-share
  // option value for option positions (backend's _position_with_state).
  const premium = p.avg_cost != null ? p.avg_cost / 100 : null;
  const unrealizedPct = p.current_price != null && premium ? ((p.current_price - premium) / premium) * 100 : 0;
  const currentTotal = p.current_price != null ? p.current_price * p.units_remaining * 100 : null;
  const totalCost = p.avg_cost != null ? p.avg_cost * p.units_remaining : null;
  const unrealizedDollar = totalCost != null && currentTotal != null ? currentTotal - totalCost : null;
  // Percentage points, e.g. -18.2 -- see PositionDetailPage's own IV crush/spike thresholds
  // (kept in sync manually, no shared constant since this is the only other call site).
  const ivChangePts = p.current_iv != null && p.iv_at_entry != null ? (p.current_iv - p.iv_at_entry) * 100 : null;
  const ivFlag = ivChangePts == null ? null : ivChangePts < -10 ? "crush" : ivChangePts > 15 ? "spike" : null;

  async function submitExit() {
    const premium = parseFloat(exitPremium);
    const units = parseFloat(exitUnits);
    if (!Number.isFinite(premium)) {
      window.alert("Enter a valid exit option price");
      return;
    }
    if (!Number.isFinite(units) || units <= 0) {
      window.alert("Enter valid units to exit");
      return;
    }
    if (!lastFill) {
      window.alert("Still loading this position's fill history -- try again in a moment.");
      return;
    }
    setSubmitting(true);
    try {
      await onExit(p.id, lastFill, premium, units, exitReason);
      setExitOpen(false);
    } catch (e) {
      window.alert(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCancel() {
    if (!window.confirm("Cancel this position? This permanently deletes it and all its fills, and cannot be undone.")) return;
    await onCancel(p.id);
  }

  return (
    <>
      <tr>
        <td>
          <Link className="tk-link" to={`/trades/${p.id}`}>
            {p.ticker}
          </Link>
        </td>
        <td>
          <span className={`status-tag ${p.status}`}>{p.status}</span>
        </td>
        <td>
          <StrategyCellLink ticker={p.ticker} strategyKey={p.strategy_key} />
        </td>
        <td>{fmtUnits(p.units_remaining)}</td>
        <td>{premium != null ? fmtMoney(premium) : "—"}</td>
        <td>{totalCost != null ? fmtMoney(totalCost) : "—"}</td>
        <td>
          {p.current_price != null ? fmtMoney(p.current_price) : "—"}
          {ivFlag && (
            <div className={`iv-flag-label ${ivFlag}`}>
              {ivFlag === "crush" ? "⚠️ IV crush" : "📈 IV spike"} ({fmtPct(ivChangePts as number)})
            </div>
          )}
        </td>
        <td>{currentTotal != null ? fmtMoney(currentTotal) : "—"}</td>
        <td>
          {unrealizedDollar != null ? (
            <b className={plClass(unrealizedDollar)}>
              {fmtMoney(unrealizedDollar)}
              <br />
              {fmtPct(unrealizedPct)}
            </b>
          ) : (
            "—"
          )}
        </td>
        <td className="actions-cell">
          <button type="button" className="small-btn" onClick={() => setExitOpen((v) => !v)}>
            Exit
          </button>{" "}
          <button type="button" className="small-btn danger" onClick={handleCancel}>
            Cancel
          </button>
        </td>
      </tr>
      {exitOpen && (
        <tr>
          <td colSpan={9}>
            {fillsQuery.isLoading ? (
              <div className="exit-form">Loading…</div>
            ) : (
              <div className="exit-form">
                <input
                  type="number"
                  step={0.01}
                  placeholder="Exit option price"
                  value={exitPremium}
                  onChange={(e) => setExitPremium(e.target.value)}
                />
                <input type="number" step={1} placeholder="Units" value={exitUnits} onChange={(e) => setExitUnits(e.target.value)} />
                <select value={exitReason} onChange={(e) => setExitReason(e.target.value as ExitReason)}>
                  <option value="tp">TP</option>
                  <option value="stop">Stop</option>
                  <option value="manual">Manual</option>
                </select>
                <button type="button" className="small-btn danger" disabled={submitting} onClick={submitExit}>
                  Confirm exit
                </button>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export function OptionsPositionsTable({
  positions,
  onExit,
  onCancel,
  showOpen = true,
}: OptionsPositionsTableProps & { showOpen?: boolean }) {
  // Open remainder rows: still-open quantity only, regardless of whether this position already
  // had partial exits -- those exits show in ClosedExitsTable below instead. Every position
  // passed in (of this instrument) is eligible to appear here as long as it has units left.
  const openRows = positions.filter((p) => p.units_remaining > 0);

  return (
    <>
      {showOpen && (
        <div className="table-scroll">
          <table className="postable">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Status</th>
                <th>Strategy</th>
                <th>Units</th>
                <th>Premium</th>
                <th>Total Cost</th>
                <th>Last</th>
                <th>Current Total</th>
                <th>Unrealized $ / %</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {openRows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="empty">
                    No open option positions.
                  </td>
                </tr>
              ) : (
                openRows.map((p) => <PositionRow key={p.id} p={p} onExit={onExit} onCancel={onCancel} />)
              )}
            </tbody>
          </table>
        </div>
      )}

      <h2>Closed Exits</h2>
      <ClosedExitsTable positions={positions} />
    </>
  );
}

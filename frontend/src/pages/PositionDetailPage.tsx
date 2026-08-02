import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useCancelPosition, useDeleteFill, useFills, useMarks, usePosition, useUpdateFill, useUpdatePosition } from "../hooks/usePosition";
import { useAddFill } from "../hooks/usePositions";
import { StatBox } from "../components/atoms/StatBox";
import { BigChart } from "../components/organisms/BigChart";
import { AddFillForm } from "../components/organisms/AddFillForm";
import { EditPositionForm } from "../components/organisms/EditPositionForm";
import { FillsTable } from "../components/organisms/FillsTable";
import { Pagination } from "../components/organisms/TickerCardGrid";
import { fmtMoney, fmtPct, fmtUnits, plClass } from "../lib/format";
import "./PositionDetailPage.css";

const MARKS_PAGE_SIZE = 20;

export function PositionDetailPage() {
  const { positionId } = useParams();
  const id = Number(positionId);
  const navigate = useNavigate();

  const { data: position, isError } = usePosition(id);
  const { data: marks } = useMarks(id);
  const { data: fills } = useFills(id);
  const addFill = useAddFill(id);
  const updatePosition = useUpdatePosition(id);
  const cancelPosition = useCancelPosition(id);
  const updateFill = useUpdateFill(id);
  const deleteFill = useDeleteFill(id);

  const [showFillForm, setShowFillForm] = useState(false);
  const [showEditForm, setShowEditForm] = useState(false);
  const [marksPage, setMarksPage] = useState(1);

  if (isError) {
    return (
      <div className="position-page">
        <header>
          <h1>Position not found</h1>
          <Link className="back" to="/trades">
            &larr; Back to Trades
          </Link>
        </header>
        <p className="empty">Failed to load position {positionId}.</p>
      </div>
    );
  }
  if (!position) {
    return (
      <div className="position-page">
        <header>
          <h1>Loading&hellip;</h1>
        </header>
      </div>
    );
  }

  const marksList = marks ?? [];
  const fillsList = fills ?? [];
  // Newest-first, same order the table already rendered in -- paginating a long-running
  // position's daily marks instead of dumping every row on one page.
  const marksDesc = marksList.slice().reverse();
  const marksPageCount = Math.max(1, Math.ceil(marksDesc.length / MARKS_PAGE_SIZE));
  const clampedMarksPage = Math.min(Math.max(1, marksPage), marksPageCount);
  const marksPageRows = marksDesc.slice(
    (clampedMarksPage - 1) * MARKS_PAGE_SIZE,
    clampedMarksPage * MARKS_PAGE_SIZE,
  );
  const isOption = position.instrument === "option";
  const hasOptionValues = isOption && marksList.length > 0 && marksList[0].option_value != null;
  const values = marksList.map((m) => (hasOptionValues ? (m.option_value as number) : m.close_price));
  const last = marksList.length ? values[values.length - 1] : (position.avg_cost ?? 0);
  // position.avg_cost from the backend has the 100x contract multiplier baked in for options
  // (see replay_fills's own docstring) -- option_value/last is per-share, so avg_cost needs to
  // be scaled back down to per-share before comparing, or the pct comes out ~100x too negative.
  // See docs/superpowers/specs/2026-08-01-separate-spot-option-pnl-design.md.
  const avgCostPerShare = position.avg_cost != null && isOption ? position.avg_cost / 100 : position.avg_cost;
  const pctLive = avgCostPerShare ? ((last - avgCostPerShare) / avgCostPerShare) * 100 : 0;
  const lastLabel = hasOptionValues ? "Option value" : "Last price";

  async function handleCancelPosition() {
    if (!window.confirm("Cancel this position? This permanently deletes it and all its fills, and cannot be undone.")) return;
    await cancelPosition.mutateAsync();
    navigate("/trades");
  }

  async function handleDeleteFill(fillId: number) {
    if (!window.confirm("Delete this fill? This cannot be undone and will recompute the position's status/avg cost.")) return;
    const result = await deleteFill.mutateAsync(fillId);
    if (result.position_deleted) navigate("/trades");
  }

  return (
    <div className="position-page">
      <header>
        <h1>
          {position.ticker} <span className={`position-badge ${position.status}`}>{position.status.toUpperCase()}</span>
        </h1>
        <Link className="back" to="/trades">
          &larr; Back to Trades
        </Link>
      </header>

      <div className="info-grid">
        <StatBox label="Units remaining" value={fmtUnits(position.units_remaining)} />
        <StatBox label="Avg cost" value={position.avg_cost != null ? fmtMoney(position.avg_cost) : "—"} />
        <StatBox label="Take Profit" value={fmtMoney(position.tp_price)} />
        <StatBox label="Stop" value={fmtMoney(position.stop_price)} />
        <StatBox label="Realized P&L" value={fmtMoney(position.realized_pnl)} tone={position.realized_pnl} />
        {position.status === "open" &&
          position.avg_cost != null &&
          (isOption ? (
            <>
              {marksList.length > 0 && <StatBox label="Current price" value={fmtMoney(marksList[marksList.length - 1].close_price)} />}
              <StatBox
                label="Current option price"
                value={fmtMoney(last)}
                sub={<span className={plClass(pctLive)}>{fmtPct(pctLive)}</span>}
              />
            </>
          ) : (
            <StatBox label={lastLabel} value={fmtMoney(last)} sub={<span className={plClass(pctLive)}>{fmtPct(pctLive)}</span>} />
          ))}
        {position.status === "closed" && <StatBox label="Closed" value={position.closed_at ? position.closed_at.slice(0, 10) : "—"} />}
      </div>

      <BigChart values={values} dates={marksList.map((m) => m.mark_date)} />

      <div className="actions">
        {position.status === "open" && (
          <button type="button" className="small-btn" onClick={() => setShowFillForm((v) => !v)}>
            Add Fill
          </button>
        )}
        <button type="button" className="small-btn" onClick={() => setShowEditForm((v) => !v)}>
          Edit Position
        </button>
        <button type="button" className="small-btn danger" onClick={handleCancelPosition}>
          Cancel Position
        </button>
      </div>

      {position.status === "open" && showFillForm && (
        <AddFillForm
          instrument={position.instrument}
          onCancel={() => setShowFillForm(false)}
          onSubmit={async (body) => {
            await addFill.mutateAsync({ ...body, instrument: position.instrument });
            setShowFillForm(false);
          }}
        />
      )}
      {showEditForm && (
        <EditPositionForm
          position={position}
          onCancel={() => setShowEditForm(false)}
          onSubmit={async (body) => {
            await updatePosition.mutateAsync(body);
            setShowEditForm(false);
          }}
        />
      )}

      <h2>Fills</h2>
      <FillsTable
        fills={fillsList}
        onEdit={async (fillId, body) => {
          await updateFill.mutateAsync({ fillId, body });
        }}
        onDelete={handleDeleteFill}
      />

      <h2>Daily marks</h2>
      {marksDesc.length ? (
        <>
          <table className="markstable">
            <thead>
              <tr>
                <th>Date</th>
                <th>Stock close</th>
                {hasOptionValues && <th>Option value</th>}
              </tr>
            </thead>
            <tbody>
              {marksPageRows.map((m) => (
                <tr key={m.mark_date}>
                  <td>{m.mark_date}</td>
                  <td>{fmtMoney(m.close_price)}</td>
                  {hasOptionValues && <td>{fmtMoney(m.option_value as number)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination
            page={clampedMarksPage}
            pageCount={marksPageCount}
            onPrev={() => setMarksPage((p) => p - 1)}
            onNext={() => setMarksPage((p) => p + 1)}
          />
        </>
      ) : (
        <p className="empty">No daily marks recorded yet.</p>
      )}

      {position.notes && (
        <>
          <h2>Notes</h2>
          <p>{position.notes}</p>
        </>
      )}
    </div>
  );
}

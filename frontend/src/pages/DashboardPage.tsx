import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useMeta, useRefreshTickers, useTickers } from "../hooks/useTickers";
import { useWatchlists } from "../hooks/useWatchlists";
import { listPositions } from "../api/positions";
import { exportCsv, exportPdf } from "../api/plCalc";
import type { Position, StrategyKey, TickerPayload } from "../api/types";
import { FilterBar, defaultFilterBarState, type FilterBarState } from "../components/organisms/FilterBar";
import { Pagination, TickerCardGrid } from "../components/organisms/TickerCardGrid";
import { NotificationBell } from "../components/organisms/NotificationBell";
import { PLCalculatorModal } from "../components/molecules/PLCalculatorModal";
import { StrategyDetailModal } from "../components/molecules/StrategyDetailModal";
import { TradeConfirmModal } from "../components/molecules/TradeConfirmModal";
import { AddFillModal } from "../components/molecules/AddFillModal";
import { WatchlistPickerModal } from "../components/molecules/WatchlistPickerModal";
import { ExportPickerModal } from "../components/molecules/ExportPickerModal";
import { todayIsoDate } from "../lib/dates";
import {
  activeMinTradesStrats,
  matchesAdvFilter,
  matchesMinTrades,
  matchesPrebreakFilter,
  matchesTradeOnFilter,
} from "../lib/filters";
import { sortTickers, maxDaysInTrade } from "../lib/sorting";
import { ADV_STRAT_KEY } from "../constants/strategy";
import "./DashboardPage.css";

const PAGE_SIZE = 9;

type ModalState = { kind: "strategy"; ticker: string; stratKey: StrategyKey } | { kind: "plCalc" } | { kind: "watchlistPicker" } | { kind: "export" } | null;

// Trade/AddFill are looked up async (need the ticker's open-position status), so they get
// their own bit of state rather than folding into ModalState -- see openTradeFlow().
interface TradeFlowState {
  ticker: string;
  stratKey: StrategyKey;
  signalDate: string;
  currentPrice: number;
}

export function DashboardPage() {
  const { data, isLoading } = useTickers();
  const { data: meta } = useMeta();
  const refresh = useRefreshTickers();
  const queryClient = useQueryClient();
  const { addToList } = useWatchlists();

  const [filterState, setFilterState] = useState<FilterBarState>(defaultFilterBarState);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [modal, setModal] = useState<ModalState>(null);
  const [tradeFlow, setTradeFlow] = useState<TradeFlowState | null>(null);
  const [existingPosition, setExistingPosition] = useState<Position | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState(false);

  const filteredRows = useMemo(() => {
    if (!data) return [];
    const minTrades = filterState.minTrades;
    const query = filterState.tickerSearch.trim().toUpperCase();
    const minTradesStrats = activeMinTradesStrats(filterState.tradeOnStrats, filterState.adv);
    const rows = data.tickers
      .filter((r) => !query || r.ticker.toUpperCase().includes(query))
      .filter((r) => matchesMinTrades(r, minTrades, minTradesStrats))
      .filter((r) => matchesAdvFilter(r, filterState.adv))
      .filter((r) => matchesTradeOnFilter(r, filterState.tradeOnStrats))
      .filter((r) => matchesPrebreakFilter(r, filterState.prebreak));
    return sortTickers(rows);
  }, [data, filterState]);

  const pageCount = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const clampedPage = Math.min(Math.max(1, page), pageCount);
  const pageRows = filteredRows.slice((clampedPage - 1) * PAGE_SIZE, clampedPage * PAGE_SIZE);

  const realErrors = Object.entries(data?.errors ?? {}).filter(([, err]) => err !== "insufficient history");
  const showErrors = clampedPage === pageCount;

  const openCount = filteredRows.filter((r) => maxDaysInTrade(r) != null).length;
  const advActive = filterState.adv.wrMin > 0 || filterState.adv.pfMin > 0;
  const filtered = filterState.minTrades > 0 || !!filterState.tickerSearch.trim() || advActive || filterState.tradeOnStrats.length > 0;
  const countText = data && filtered ? `${filteredRows.length} of ${data.tickers.length} tickers (filtered)` : `${data?.tickers.length ?? 0} tickers`;
  const metaText = data
    ? `as of ${data.asof ? new Date(data.asof).toLocaleString() : "—"} · ${countText} · ${openCount} open trade${openCount === 1 ? "" : "s"}${data.cached ? " · cached" : " · live"}`
    : "loading…";

  const active = !!(meta?.fetch_progress || meta?.compute_progress);
  const progressPct = (() => {
    if (meta?.compute_progress && meta.compute_progress.total > 0) {
      return 50 + Math.min(50 - 0.1, (meta.compute_progress.done / meta.compute_progress.total) * 50);
    }
    if (meta?.fetch_progress && meta.fetch_progress.total > 0) {
      return Math.min(50, (meta.fetch_progress.done / meta.fetch_progress.total) * 50);
    }
    return 0;
  })();
  const progressLabel = meta?.compute_progress
    ? `Now computing… ${meta.compute_progress.done} of ${meta.compute_progress.total} tickers`
    : meta?.fetch_progress
      ? `Now fetching tickers… ${meta.fetch_progress.done} of ${meta.fetch_progress.total}`
      : "";

  function updateFilters(next: FilterBarState) {
    setFilterState(next);
    setPage(1);
  }

  function toggleSelect(ticker: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(ticker)) next.delete(ticker);
      else next.add(ticker);
      return next;
    });
  }

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  }

  async function openTradeFlow(ticker: string, stratKey: StrategyKey) {
    const row = data?.tickers.find((t) => t.ticker === ticker);
    const s = row?.[stratKey];
    if (!row || !s) return;
    const op = s.open_position;
    const signalDate = op ? op.entry_date : todayIsoDate();
    setModal(null);
    try {
      const openPositions = await listPositions("open");
      setExistingPosition(openPositions.find((p) => p.ticker === ticker) ?? null);
    } catch {
      setExistingPosition(null);
    }
    setTradeFlow({ ticker, stratKey, signalDate, currentPrice: row.price });
  }

  function onTradeSubmitted() {
    setTradeFlow(null);
    queryClient.invalidateQueries({ queryKey: ["tickers"] });
  }

  async function runExport(format: "pdf" | "csv") {
    setModal(null);
    setExporting(true);
    try {
      const body = {
        tickers: Array.from(selected),
        strategy: ADV_STRAT_KEY[filterState.adv.strategy],
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      };
      if (format === "pdf") await exportPdf(body);
      else await exportCsv(body);
    } finally {
      setExporting(false);
    }
  }

  const tradeRow: TickerPayload | undefined = tradeFlow ? data?.tickers.find((t) => t.ticker === tradeFlow.ticker) : undefined;
  const tradeStrategy = tradeFlow && tradeRow ? tradeRow[tradeFlow.stratKey] : null;

  return (
    <div>
      <div className="dashboard-header">
        <h1>Exhaustion Dashboard</h1>
        <span className="dashboard-meta">{metaText}</span>
        <div className="dashboard-header-actions">
          {selected.size === 0 ? (
            <button type="button" onClick={() => setSelected(new Set(pageRows.map((r) => r.ticker)))}>
              Select all
            </button>
          ) : (
            <>
              <span className="dashboard-meta">{selected.size} selected</span>
              <button type="button" onClick={() => setSelected(new Set())}>
                Clear
              </button>
              <button type="button" onClick={() => setModal({ kind: "watchlistPicker" })}>
                Add
              </button>
              <button type="button" disabled={exporting} onClick={() => setModal({ kind: "export" })}>
                {exporting ? "Exporting…" : "Export"}
              </button>
            </>
          )}
          {selected.size === 0 && (
            <button type="button" disabled={refreshing || active} onClick={handleRefresh}>
              Refresh
            </button>
          )}
        </div>
      </div>

      {active && (
        <div className="dashboard-progress">
          <div className="dashboard-progress-bar">
            <div className="dashboard-progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <span className="dashboard-progress-label">{progressLabel}</span>
        </div>
      )}

      <FilterBar state={filterState} onChange={updateFilters} />

      {isLoading ? (
        <p style={{ color: "var(--muted)" }}>Fetching tickers&hellip; first load may take a minute or two for a large universe</p>
      ) : (
        <TickerCardGrid
          rows={pageRows}
          errors={showErrors ? realErrors : []}
          showErrors={showErrors}
          scoreStrategy={filterState.adv.strategy}
          selected={selected}
          onToggleSelect={toggleSelect}
          onOpenStrategy={(ticker, stratKey) => setModal({ kind: "strategy", ticker, stratKey })}
        />
      )}

      <Pagination page={clampedPage} pageCount={pageCount} onPrev={() => setPage((p) => p - 1)} onNext={() => setPage((p) => p + 1)} />

      <p className="dashboard-foot">
        <button type="button" onClick={() => setModal({ kind: "plCalc" })}>
          P/L Calculator
        </button>
      </p>

      {modal?.kind === "strategy" &&
        (() => {
          const row = data?.tickers.find((t) => t.ticker === modal.ticker);
          const s = row?.[modal.stratKey];
          if (!s) return null;
          return (
            <StrategyDetailModal
              ticker={modal.ticker}
              stratKey={modal.stratKey}
              s={s}
              onClose={() => setModal(null)}
              onTrade={() => openTradeFlow(modal.ticker, modal.stratKey)}
            />
          );
        })()}

      {modal?.kind === "plCalc" && <PLCalculatorModal onClose={() => setModal(null)} />}

      {modal?.kind === "watchlistPicker" && (
        <WatchlistPickerModal
          count={selected.size}
          onClose={() => setModal(null)}
          onPick={(name) => {
            for (const tk of selected) addToList(name, tk);
            setModal(null);
          }}
        />
      )}

      {modal?.kind === "export" && <ExportPickerModal count={selected.size} onClose={() => setModal(null)} onPick={runExport} />}

      {tradeFlow &&
        tradeRow &&
        (existingPosition ? (
          <AddFillModal
            position={existingPosition}
            stratKey={tradeFlow.stratKey}
            signalDate={tradeFlow.signalDate}
            currentPrice={tradeFlow.currentPrice}
            onClose={() => setTradeFlow(null)}
            onSubmitted={onTradeSubmitted}
          />
        ) : (
          <TradeConfirmModal
            ticker={tradeFlow.ticker}
            stratKey={tradeFlow.stratKey}
            signalDate={tradeFlow.signalDate}
            currentPrice={tradeFlow.currentPrice}
            openPosition={tradeStrategy?.open_position ?? null}
            avgMaeWinsPct={tradeStrategy?.avg_mae_wins_pct ?? null}
            onClose={() => setTradeFlow(null)}
            onSubmitted={onTradeSubmitted}
          />
        ))}
    </div>
  );
}

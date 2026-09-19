import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useCryptoV2Meta, useCryptoV2Signals, useRefreshCryptoV2Signals } from "../hooks/useCryptoV2";
import type { CryptoV2TickerPayload } from "../api/cryptoV2";
import { listPositions } from "../api/positions";
import type { Position } from "../api/types";
import { Chip } from "../components/atoms/Chip";
import { StrategyBadgeRow } from "../components/molecules/StrategyBadgeRow";
import { CryptoV2SignalModal } from "../components/molecules/CryptoV2SignalModal";
import { TradeConfirmModal } from "../components/molecules/TradeConfirmModal";
import { AddFillModal } from "../components/molecules/AddFillModal";
import { CryptoFilterBar, defaultCryptoFilterBarState, type CryptoFilterBarState } from "../components/organisms/CryptoFilterBar";
import { Pagination } from "../components/organisms/TickerCardGrid";
import { toTradingViewUrl } from "./CryptoPage";
import { todayIsoDate } from "../lib/dates";
import { sortCryptoV2Tickers } from "../lib/sorting";
import "../components/organisms/TickerCard.css";
import "../components/organisms/TickerCardGrid.css";
import "../pages/DashboardPage.css";
import "./CryptoPage.css";

// Real crypto v2 Scanner, replacing the "coming soon" placeholder -- structurally mirrors
// CryptoPage.tsx (meta polling, filter bar, pagination, trade-flow state), but v2 has no single
// verdict string the way v1's strategy_vcp.verdict does: a ticker can have several setups
// trigger the same day, each independently scored, so the card derives its own display state
// instead (IN TRADE / a chip per live setup / NO SIGNAL).

interface CryptoV2TradeFlowState {
  ticker: string;
  signalDate: string;
  currentPrice: number;
}

const PAGE_SIZE = 9;

function titleCase(setupType: string): string {
  return setupType.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

function CryptoV2Card({ row, onOpen }: { row: CryptoV2TickerPayload; onOpen: () => void }) {
  const op = row.open_position;
  const daysHeld = op ? op.days_held : null;
  const pending = row.signals_today.length > 0 && !op;
  return (
    <div className="tickercard">
      <div className="cardhead">
        <a className="tklink tk" href={toTradingViewUrl(row.ticker, null)} target="_blank" rel="noopener noreferrer">
          {row.ticker}
        </a>
        <span className="price num">${row.price ?? "—"}</span>
      </div>
      {row.category && <div className="crypto-source">Category: {row.category.replace("_", " ")}</div>}
      <div className="crypto-source">Execution score: {row.execution_score} / 10</div>

      {op ? (
        <div className={`crypto-verdict crypto-verdict-in-trade`}>IN TRADE</div>
      ) : row.signals_today.length > 0 ? (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", margin: "6px 0" }}>
          {row.signals_today.map((sig) => (
            <Chip key={sig.setup_type} tone="pending" onClick={onOpen}>
              {titleCase(sig.setup_type)} ({sig.setup_score})
            </Chip>
          ))}
        </div>
      ) : (
        <div className="crypto-verdict crypto-verdict-none">NO SIGNAL</div>
      )}

      <hr className="divider" />
      <StrategyBadgeRow
        active={!!op}
        daysHeld={daysHeld}
        pending={pending}
        nTrades={row.n_trades}
        winRate={row.win_rate}
        profitFactor={row.profit_factor}
        onClick={onOpen}
      />
      {op && (
        <div className="crypto-open-position">
          <div>
            Open since {op.entry_date} · {op.unrealized_pct >= 0 ? "+" : ""}
            {op.unrealized_pct}% · {op.days_held}d held
          </div>
          <div>
            Entry ${op.entry_price} · Target ${op.target}
            {op.stop != null && <> · Stop ${op.stop}</>}
          </div>
        </div>
      )}
    </div>
  );
}

export function CryptoV2Page() {
  const { data, isLoading } = useCryptoV2Signals();
  const { data: meta } = useCryptoV2Meta();
  const refresh = useRefreshCryptoV2Signals();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [filterState, setFilterState] = useState<CryptoFilterBarState>(defaultCryptoFilterBarState);
  const [page, setPage] = useState(1);
  const [openTicker, setOpenTicker] = useState<string | null>(null);
  const [tradeFlow, setTradeFlow] = useState<CryptoV2TradeFlowState | null>(null);
  const [existingPosition, setExistingPosition] = useState<Position | null>(null);

  const filteredRows = useMemo(() => {
    if (!data) return [];
    const query = filterState.tickerSearch.trim().toUpperCase();
    const rows = data.tickers
      .filter((r) => !query || r.ticker.toUpperCase().includes(query))
      .filter((r) => filterState.minTrades <= 0 || r.n_trades >= filterState.minTrades)
      .filter((r) => (filterState.wrMin <= 0 && filterState.pfMin <= 0)
        ? true
        : r.win_rate >= filterState.wrMin && r.profit_factor >= filterState.pfMin);
    return sortCryptoV2Tickers(rows);
  }, [data, filterState]);

  const pageCount = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const clampedPage = Math.min(Math.max(1, page), pageCount);
  const pageRows = filteredRows.slice((clampedPage - 1) * PAGE_SIZE, clampedPage * PAGE_SIZE);

  function updateFilters(next: CryptoFilterBarState) {
    setFilterState(next);
    setPage(1);
  }

  function goToPage(next: number) {
    setPage(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

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

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  }

  // Mirrors CryptoPage.tsx's openTradeFlow -- stratKey is always "manual" here since v2's setup
  // types (breakout/momentum_continuation/...) have no StrategyKey union member; StrategyCellLink
  // already renders "manual" safely everywhere it's displayed later (Trades table, position detail).
  async function openTradeFlow(ticker: string, currentPrice: number, signalDate: string) {
    setOpenTicker(null);
    try {
      const openPositions = await listPositions("open", undefined, "crypto");
      setExistingPosition(openPositions.find((p) => p.ticker === ticker) ?? null);
    } catch {
      setExistingPosition(null);
    }
    setTradeFlow({ ticker, signalDate, currentPrice });
  }

  function onTradeSubmitted() {
    setTradeFlow(null);
    queryClient.invalidateQueries({ queryKey: ["positions"] });
  }

  const metaText = data
    ? `as of ${data.asof ? new Date(data.asof).toLocaleString() : "—"} · ${filteredRows.length} of ${data.tickers.length} tickers`
    : "loading…";

  const realErrors = Object.entries(data?.errors ?? {}).filter(([, err]) => err !== "insufficient history" && err !== "no data");

  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <h1>Crypto v2 Signals</h1>
        <span className="dashboard-meta">{metaText}</span>
        <div className="dashboard-header-actions">
          <button type="button" onClick={handleRefresh} disabled={refreshing || active}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
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

      <CryptoFilterBar state={filterState} onChange={updateFilters} />

      {isLoading ? (
        <p style={{ color: "var(--muted)" }}>Fetching crypto v2 signals&hellip;</p>
      ) : (
        <div className="cardgrid">
          {pageRows.map((row) => (
            <CryptoV2Card key={row.ticker} row={row} onOpen={() => setOpenTicker(row.ticker)} />
          ))}
        </div>
      )}

      <Pagination page={clampedPage} pageCount={pageCount} onPrev={() => goToPage(clampedPage - 1)} onNext={() => goToPage(clampedPage + 1)} />

      {realErrors.length > 0 && (
        <div className="crypto-errors">
          {realErrors.map(([ticker, err]) => (
            <div key={ticker}>
              {ticker}: {err}
            </div>
          ))}
        </div>
      )}

      {openTicker && (() => {
        const row = data?.tickers.find((r) => r.ticker === openTicker);
        if (!row) return null;
        return (
          <CryptoV2SignalModal
            row={row}
            onClose={() => setOpenTicker(null)}
            onTrade={() => openTradeFlow(row.ticker, row.price ?? 0, row.open_position ? row.open_position.entry_date : todayIsoDate())}
          />
        );
      })()}

      {tradeFlow &&
        (existingPosition ? (
          <AddFillModal
            position={existingPosition}
            stratKey="manual"
            signalDate={tradeFlow.signalDate}
            currentPrice={tradeFlow.currentPrice}
            onClose={() => setTradeFlow(null)}
            onSubmitted={onTradeSubmitted}
          />
        ) : (
          <TradeConfirmModal
            ticker={tradeFlow.ticker}
            stratKey="manual"
            signalDate={tradeFlow.signalDate}
            currentPrice={tradeFlow.currentPrice}
            openPosition={data?.tickers.find((r) => r.ticker === tradeFlow.ticker)?.open_position ?? null}
            avgMaeWinsPct={null}
            onClose={() => setTradeFlow(null)}
            onSubmitted={onTradeSubmitted}
          />
        ))}
    </div>
  );
}

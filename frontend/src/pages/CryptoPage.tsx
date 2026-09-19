import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useCryptoMeta, useCryptoSignals, useRefreshCryptoSignals } from "../hooks/useCrypto";
import type { CryptoTickerPayload } from "../api/crypto";
import { listPositions } from "../api/positions";
import type { Position } from "../api/types";
import { StrategyBadgeRow } from "../components/molecules/StrategyBadgeRow";
import { CryptoValidationModal } from "../components/molecules/CryptoValidationModal";
import { TradeConfirmModal } from "../components/molecules/TradeConfirmModal";
import { AddFillModal } from "../components/molecules/AddFillModal";
import { CryptoFilterBar, defaultCryptoFilterBarState, type CryptoFilterBarState } from "../components/organisms/CryptoFilterBar";
import { Pagination } from "../components/organisms/TickerCardGrid";
import { todayIsoDate } from "../lib/dates";
import { sortCryptoTickers } from "../lib/sorting";
import "../components/organisms/TickerCard.css";
import "../components/organisms/TickerCardGrid.css";
import "../pages/DashboardPage.css";
import "./CryptoPage.css";

// Trade/AddFill are looked up async (need the ticker's open-position status), so they get their
// own bit of state rather than folding into a modal union -- mirrors DashboardPage's own
// TradeFlowState/openTradeFlow for the equity side.
interface CryptoTradeFlowState {
  ticker: string;
  signalDate: string;
  currentPrice: number;
}

const PAGE_SIZE = 9;

const VERDICT_CLASS: Record<string, string> = {
  TAKE: "crypto-verdict-take",
  "IN TRADE": "crypto-verdict-in-trade",
  "TP HIT": "crypto-verdict-tp-hit",
  SKIP: "crypto-verdict-skip",
  "NO SIGNAL": "crypto-verdict-none",
};

// yfinance's lastMarket (crypto_universe.py's discovery) names the real venue for most tickers --
// only "Coinbase" shows up in practice (the other observed value, "CoinMarketCap" itself, isn't a
// tradeable exchange, just yfinance's fallback attribution for thin coins it can't pin to one).
// When it maps to a TradingView exchange prefix, link straight to that chart; otherwise fall back
// to TradingView's /symbols/ page, which auto-resolves a bare pair to whichever exchange lists it.
const TRADINGVIEW_EXCHANGE: Record<string, string> = {
  Coinbase: "COINBASE",
};

function toTradingViewUrl(yahooTicker: string, lastMarket: string | null): string {
  const base = yahooTicker.replace(/-USD$/, "");
  const exchange = lastMarket ? TRADINGVIEW_EXCHANGE[lastMarket] : null;
  if (exchange) {
    return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`${exchange}:${base}USD`)}`;
  }
  return `https://www.tradingview.com/symbols/${encodeURIComponent(base)}USD/`;
}

// Same tickercard shell/badge-row as the equity dashboard's TickerCard, sized down to crypto's
// single-strategy payload (no setup_score/earnings/prebreak fields to show).
function CryptoCard({ row, onValidate }: { row: CryptoTickerPayload; onValidate: () => void }) {
  const s = row.strategy_vcp;
  const verdictClass = VERDICT_CLASS[s.verdict] ?? "crypto-verdict-none";
  const daysHeld = s.open_position ? s.open_position.days_held : null;
  const pending = !!(s.signal_today && !s.open_position);
  return (
    <div className="tickercard">
      <div className="cardhead">
        <a
          className="tklink tk"
          href={toTradingViewUrl(row.ticker, row.last_market)}
          target="_blank"
          rel="noopener noreferrer"
        >
          {row.ticker}
        </a>
        <span className="price num">${row.price}</span>
      </div>
      {row.last_market && <div className="crypto-source">Source: {row.last_market}</div>}
      {row.price_source === "yfinance" && (
        <div className="crypto-unverified-badge">yfinance (unverified)</div>
      )}
      <div className={`crypto-verdict ${verdictClass}`}>{s.verdict}</div>
      <div className="crypto-reason">{s.verdict_reason}</div>
      <hr className="divider" />
      <StrategyBadgeRow
        active={!!s.open_position}
        daysHeld={daysHeld}
        pending={pending}
        nTrades={s.n_trades}
        winRate={s.win_rate}
        profitFactor={s.profit_factor}
        onClick={onValidate}
      />
      {s.open_position && (
        <div className="crypto-open-position">
          <div>
            Open since {s.open_position.entry_date} · {s.open_position.unrealized_pct >= 0 ? "+" : ""}
            {s.open_position.unrealized_pct}% · {s.open_position.days_held}d held
          </div>
          <div>
            Entry ${s.open_position.entry_price} · Target ${s.open_position.target}
            {s.open_position.stop != null && <> · Stop ${s.open_position.stop}</>}
          </div>
        </div>
      )}
    </div>
  );
}

export function CryptoPage() {
  const { data, isLoading } = useCryptoSignals();
  const { data: meta } = useCryptoMeta();
  const refresh = useRefreshCryptoSignals();
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [filterState, setFilterState] = useState<CryptoFilterBarState>(defaultCryptoFilterBarState);
  const [page, setPage] = useState(1);
  const [validateTicker, setValidateTicker] = useState<string | null>(null);
  const [tradeFlow, setTradeFlow] = useState<CryptoTradeFlowState | null>(null);
  const [existingPosition, setExistingPosition] = useState<Position | null>(null);

  const filteredRows = useMemo(() => {
    if (!data) return [];
    const query = filterState.tickerSearch.trim().toUpperCase();
    const rows = data.tickers
      .filter((r) => !query || r.ticker.toUpperCase().includes(query))
      .filter((r) => filterState.minTrades <= 0 || r.strategy_vcp.n_trades >= filterState.minTrades)
      .filter((r) => filterState.wrMin <= 0 && filterState.pfMin <= 0
        ? true
        : r.strategy_vcp.win_rate >= filterState.wrMin && r.strategy_vcp.profit_factor >= filterState.pfMin);
    return sortCryptoTickers(rows);
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

  // Mirrors DashboardPage's own openTradeFlow -- the strategy's own open_position (a backtest
  // replay, no real money behind it) is a completely separate thing from whether this ticker
  // already has a REAL tracked position in the positions table, so that has to be checked here
  // too rather than inferred from the signal payload.
  async function openTradeFlow(ticker: string, currentPrice: number, signalDate: string) {
    setValidateTicker(null);
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

  // A newly-listed/thinly-traded ticker not yet having enough bars for VCP's lookback window is
  // expected, not a real failure -- equity's DashboardPage filters this same error out too.
  const realErrors = Object.entries(data?.errors ?? {}).filter(([, err]) => err !== "insufficient history");

  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <h1>Crypto Signals</h1>
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
        <p style={{ color: "var(--muted)" }}>Fetching crypto signals&hellip;</p>
      ) : (
        <div className="cardgrid">
          {pageRows.map((row) => (
            <CryptoCard key={row.ticker} row={row} onValidate={() => setValidateTicker(row.ticker)} />
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

      {validateTicker && (() => {
        const row = data?.tickers.find((r) => r.ticker === validateTicker);
        if (!row) return null;
        const op = row.strategy_vcp.open_position;
        return (
          <CryptoValidationModal
            row={row}
            onClose={() => setValidateTicker(null)}
            onTrade={() => openTradeFlow(row.ticker, row.price, op ? op.entry_date : todayIsoDate())}
          />
        );
      })()}

      {tradeFlow &&
        (existingPosition ? (
          <AddFillModal
            position={existingPosition}
            stratKey="strategy_vcp"
            signalDate={tradeFlow.signalDate}
            currentPrice={tradeFlow.currentPrice}
            onClose={() => setTradeFlow(null)}
            onSubmitted={onTradeSubmitted}
          />
        ) : (
          <TradeConfirmModal
            ticker={tradeFlow.ticker}
            stratKey="strategy_vcp"
            signalDate={tradeFlow.signalDate}
            currentPrice={tradeFlow.currentPrice}
            openPosition={data?.tickers.find((r) => r.ticker === tradeFlow.ticker)?.strategy_vcp.open_position ?? null}
            avgMaeWinsPct={null}
            onClose={() => setTradeFlow(null)}
            onSubmitted={onTradeSubmitted}
          />
        ))}
    </div>
  );
}

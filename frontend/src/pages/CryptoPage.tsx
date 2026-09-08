import { useState } from "react";
import { useCryptoSignals, useRefreshCryptoSignals } from "../hooks/useCrypto";
import type { CryptoTickerPayload } from "../api/crypto";
import { StrategyBadgeRow } from "../components/molecules/StrategyBadgeRow";
import "../components/organisms/TickerCard.css";
import "../components/organisms/TickerCardGrid.css";
import "./CryptoPage.css";

const VERDICT_CLASS: Record<string, string> = {
  TAKE: "crypto-verdict-take",
  "IN TRADE": "crypto-verdict-in-trade",
  "TP HIT": "crypto-verdict-tp-hit",
  SKIP: "crypto-verdict-skip",
  "NO SIGNAL": "crypto-verdict-none",
};

// Crypto tickers are Yahoo-style ("BTC-USD") -- not every one is listed on Binance (e.g.
// WOJAK-USD isn't), so a hardcoded "BINANCE:" prefix 404s for those. TradingView's /symbols/
// page auto-resolves a bare pair to whichever exchange actually lists it instead.
function toTradingViewUrl(yahooTicker: string): string {
  const base = yahooTicker.replace(/-USD$/, "");
  return `https://www.tradingview.com/symbols/${encodeURIComponent(base)}USD/`;
}

// Same tickercard shell/badge-row as the equity dashboard's TickerCard, sized down to crypto's
// single-strategy payload (no setup_score/earnings/prebreak fields to show).
function CryptoCard({ row }: { row: CryptoTickerPayload }) {
  const s = row.strategy_vcp;
  const verdictClass = VERDICT_CLASS[s.verdict] ?? "crypto-verdict-none";
  const daysHeld = s.open_position ? s.open_position.days_held : null;
  const pending = !!(s.signal_today && !s.open_position);
  return (
    <div className="tickercard">
      <div className="cardhead">
        <a
          className="tklink tk"
          href={toTradingViewUrl(row.ticker)}
          target="_blank"
          rel="noopener noreferrer"
        >
          {row.ticker}
        </a>
        <span className="price num">${row.price}</span>
      </div>
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
        onClick={() => {}}
      />
      {s.open_position && (
        <div className="crypto-open-position">
          Open since {s.open_position.entry_date} · {s.open_position.unrealized_pct >= 0 ? "+" : ""}
          {s.open_position.unrealized_pct}% · {s.open_position.days_held}d held
        </div>
      )}
    </div>
  );
}

export function CryptoPage() {
  const { data, isLoading } = useCryptoSignals();
  const refresh = useRefreshCryptoSignals();
  const [refreshing, setRefreshing] = useState(false);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="crypto-page">
      <div className="crypto-header">
        <h1>Crypto Signals</h1>
        <button onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <div className="crypto-meta">
        {data ? `as of ${data.asof ? new Date(data.asof).toLocaleString() : "—"} · ${data.tickers.length} tickers` : "loading…"}
      </div>
      {isLoading && <div className="crypto-loading">Loading…</div>}
      <div className="cardgrid">
        {data?.tickers.map((row) => (
          <CryptoCard key={row.ticker} row={row} />
        ))}
      </div>
      {data && Object.keys(data.errors).length > 0 && (
        <div className="crypto-errors">
          {Object.entries(data.errors).map(([ticker, err]) => (
            <div key={ticker}>
              {ticker}: {err}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

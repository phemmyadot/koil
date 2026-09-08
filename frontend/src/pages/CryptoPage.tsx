import { useState } from "react";
import { useCryptoSignals, useRefreshCryptoSignals } from "../hooks/useCrypto";
import type { CryptoTickerPayload } from "../api/crypto";
import "./CryptoPage.css";

const VERDICT_CLASS: Record<string, string> = {
  TAKE: "crypto-verdict-take",
  "IN TRADE": "crypto-verdict-in-trade",
  "TP HIT": "crypto-verdict-tp-hit",
  SKIP: "crypto-verdict-skip",
  "NO SIGNAL": "crypto-verdict-none",
};

function CryptoCard({ row }: { row: CryptoTickerPayload }) {
  const s = row.strategy_vcp;
  const verdictClass = VERDICT_CLASS[s.verdict] ?? "crypto-verdict-none";
  return (
    <div className="crypto-card">
      <div className="crypto-card-header">
        <span className="crypto-ticker">{row.ticker}</span>
        <span className="crypto-price">${row.price}</span>
      </div>
      <div className={`crypto-verdict ${verdictClass}`}>{s.verdict}</div>
      <div className="crypto-reason">{s.verdict_reason}</div>
      <div className="crypto-stats">
        <span>{s.n_trades} trades</span>
        <span>{s.win_rate}% WR</span>
        <span>PF {s.profit_factor}</span>
      </div>
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
      <div className="crypto-grid">
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

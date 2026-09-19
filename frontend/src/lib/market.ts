// Mirrors backend/app.py's _ticker_market: crypto tickers are yfinance's "-USD" pairs
// (data_crypto.py/crypto_universe.py's own convention), everything else is equity.
export function isCryptoTicker(ticker: string): boolean {
  return ticker.toUpperCase().endsWith("-USD");
}

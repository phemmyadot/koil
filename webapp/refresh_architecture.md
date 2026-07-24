# Refresh Architecture

## Build
Nothing happens. No fetch, no compute.

## App load
Read whatever's already in the DB and show it immediately. No fetch, no compute, no staleness check. If the DB is empty, this is what the background pass (below) fills in — the loader shown to the user covers that wait.

## Background loop (runs every 2 hours)
Three independent checks, each on its own rule:

- **Tickers**: if the list was last screened within 2 hours, skip. Otherwise re-screen fully (no partial version of this exists — it's all-or-nothing).
- **Prices**: for each ticker, fetch only the days missing since its last stored date. Never re-pulls history that's already stored.
- **Earnings**: if a ticker's earnings dates were fetched within the last 24 hours, skip. Otherwise fetch fresh (also all-or-nothing, one small request).

- **Compute**: for each ticker, only recompute if its price data actually changed since the last compute. If unchanged, reuse the stored result. Runs in the background — never blocks a page load.

## Manual refresh (user clicks the button)
Forces tickers and prices to re-check right now, ignoring their normal skip windows. That forces every ticker's compute to re-run too, since compute always re-runs when price data changes. Earnings are **not** force-refreshed — they stay on their own 24-hour timer even during a manual refresh.

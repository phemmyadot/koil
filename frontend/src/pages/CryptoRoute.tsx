import { CryptoPage } from "./CryptoPage";
import { CryptoTradesPage } from "./CryptoTradesPage";
import { CryptoV2Page } from "./CryptoV2Page";

// /crypto (v1) and /crypto/v2 are both always routable, same defense-in-depth pattern as
// /analyzer (router.tsx's own comment): the nav entry for v2 is what's feature-flag-gated
// (AppShell, via /api/flags's crypto_v2_enabled), not the route itself.
export function CryptoV1Route() {
  return <CryptoPage />;
}

export function CryptoV2Route() {
  return <CryptoV2Page />;
}

// /crypto/trades has no v1/v2 split -- positions/fills are already market-generic (market=crypto,
// not tied to a strategy version), so a v2-sourced trade is stored and displayed exactly like
// any other crypto position. Same page either way.
export function CryptoTradesRoute() {
  return <CryptoTradesPage />;
}

import { useFlags } from "../hooks/useFlags";
import { CryptoPage } from "./CryptoPage";
import { CryptoTradesPage } from "./CryptoTradesPage";
import { CryptoV2Page } from "./CryptoV2Page";
import { CryptoV2TradesPage } from "./CryptoV2TradesPage";

// Same URLs (/crypto, /crypto/trades) render v1 or v2 depending on ENABLE_CRYPTO_V2 (see
// /api/flags's crypto_v2_enabled) -- a switch, not two separate route trees, matching AppShell's
// own nav-label switch. Defaults to v1 while flags are still loading, same latency/behavior
// daily_review_enabled's Analyzer nav item already has.
export function CryptoDashboardRoute() {
  const { data: flags } = useFlags();
  return flags?.crypto_v2_enabled ? <CryptoV2Page /> : <CryptoPage />;
}

export function CryptoTradesRoute() {
  const { data: flags } = useFlags();
  return flags?.crypto_v2_enabled ? <CryptoV2TradesPage /> : <CryptoTradesPage />;
}

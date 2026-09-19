import { useFlags } from "../hooks/useFlags";
import { CryptoPage } from "./CryptoPage";
import { CryptoTradesPage } from "./CryptoTradesPage";
import { CryptoV2Page } from "./CryptoV2Page";

// /crypto renders v1 or v2 depending on ENABLE_CRYPTO_V2 (see /api/flags's crypto_v2_enabled) --
// a switch, not two separate route trees, matching AppShell's own nav-label switch. Defaults to
// v1 while flags are still loading, same latency/behavior daily_review_enabled's Analyzer nav
// item already has.
export function CryptoDashboardRoute() {
  const { data: flags } = useFlags();
  return flags?.crypto_v2_enabled ? <CryptoV2Page /> : <CryptoPage />;
}

// /crypto/trades has no v1/v2 split -- positions/fills are already market-generic (market=crypto,
// not tied to a strategy version), so a v2-sourced trade is stored and displayed exactly like
// any other crypto position. Same page either way.
export function CryptoTradesRoute() {
  return <CryptoTradesPage />;
}

import "./DashboardPage.css";

// Placeholder for the future crypto v2 rebuild (different data shape/strategy, its own tables --
// see docs/superpowers/specs, not yet written). Rendered instead of CryptoPage whenever
// ENABLE_CRYPTO_V2=true (see CryptoRoute.tsx) -- v1 itself is untouched either way.
export function CryptoV2Page() {
  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <h1>Crypto v2</h1>
      </div>
      <p style={{ color: "var(--muted)" }}>Coming soon -- ENABLE_CRYPTO_V2 is on, but v2 itself isn't built yet.</p>
    </div>
  );
}

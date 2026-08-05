import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "./components/organisms/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { TradesPage } from "./pages/TradesPage";
import { PositionDetailPage } from "./pages/PositionDetailPage";
import { WatchlistsPage } from "./pages/WatchlistsPage";
import { AnalyzerPage } from "./pages/AnalyzerPage";

// Route table per docs/superpowers/specs/2026-07-31-react-spa-rewrite-design.md's §Routing.
// Modals (strategy detail, P/L calculator, trade confirm, notifications) are NOT routes --
// they're contextual overlays on the Dashboard, matching current behavior.
// /analyzer (daily review chatbot, see
// docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md) is always routable --
// the nav entry is what's feature-flag-gated (AppShell); the page itself 404s its own API calls
// when the flag is off (backend's own guard), same defense-in-depth as every other flag check.
export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <DashboardPage /> },
      { path: "/trades", element: <TradesPage /> },
      { path: "/trades/:positionId", element: <PositionDetailPage /> },
      { path: "/watchlists", element: <WatchlistsPage /> },
      { path: "/analyzer", element: <AnalyzerPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

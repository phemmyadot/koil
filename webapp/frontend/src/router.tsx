import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "./components/organisms/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { TradesPage } from "./pages/TradesPage";
import { PositionDetailPage } from "./pages/PositionDetailPage";
import { WatchlistsPage } from "./pages/WatchlistsPage";

// Route table per docs/superpowers/specs/2026-07-31-react-spa-rewrite-design.md's §Routing.
// Modals (strategy detail, P/L calculator, trade confirm, notifications) are NOT routes --
// they're contextual overlays on the Dashboard, matching current behavior.
export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <DashboardPage /> },
      { path: "/trades", element: <TradesPage /> },
      { path: "/trades/:positionId", element: <PositionDetailPage /> },
      { path: "/watchlists", element: <WatchlistsPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

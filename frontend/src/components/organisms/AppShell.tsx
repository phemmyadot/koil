import { NavLink, Outlet } from "react-router-dom";
import { KMark } from "../atoms/KMark";
import { NotificationBell } from "./NotificationBell";
import { PLCalcFab } from "./PLCalcFab";
import "./AppShell.css";

// Responsive nav shell per docs/superpowers/specs/2026-07-31-react-spa-rewrite-design.md:
// top bar >= 1080px (matches the old app's existing breakpoint), bottom tab bar on mobile.
// Both render the same 3 nav links (Dashboard/Trades/Watchlists); CSS media queries switch
// which one is visible rather than branching in JS, so there's exactly one source of truth
// for "what are the nav destinations."
const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: "\u{1F4C8}" },
  { to: "/trades", label: "Trades", icon: "\u{1F4CA}" },
  { to: "/watchlists", label: "Watchlists", icon: "⭐" },
];

export function AppShell() {
  return (
    <div className="app-shell">
      <header className="app-topbar">
        <NavLink to="/" className="app-brand" end>
          <KMark size={28} />
          <span className="app-brand-name">KOIL</span>
        </NavLink>
        <nav className="app-toplinks">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <NotificationBell />
      </header>

      <main className="app-content">
        <Outlet />
      </main>

      <PLCalcFab />

      <nav className="app-bottomnav">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === "/"} className="app-bottomnav-item">
            <span className="app-bottomnav-icon" aria-hidden="true">
              {item.icon}
            </span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

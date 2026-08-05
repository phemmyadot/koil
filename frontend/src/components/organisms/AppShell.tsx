import { NavLink, Outlet } from "react-router-dom";
import { KMark } from "../atoms/KMark";
import { WordMark } from "../atoms/WordMark";
import { NotificationBell } from "./NotificationBell";
import { PLCalcFab } from "./PLCalcFab";
import { useMeta } from "../../hooks/useTickers";
import "./AppShell.css";

// Responsive nav shell per docs/superpowers/specs/2026-07-31-react-spa-rewrite-design.md:
// top bar >= 1080px (matches the old app's existing breakpoint), bottom tab bar on mobile.
// Both render the same nav links; CSS media queries switch which one is visible rather than
// branching in JS, so there's exactly one source of truth for "what are the nav destinations."
const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: "\u{1F4C8}" },
  { to: "/trades", label: "Trades", icon: "\u{1F4CA}" },
  { to: "/watchlists", label: "Watchlists", icon: "⭐" },
];

// Analyzer is feature-flagged (ENABLE_DAILY_REVIEW) -- see
// docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md. Appended, not merged
// into NAV_ITEMS, since it's conditional on /api/meta's daily_review_enabled rather than always
// present.
const ANALYZER_NAV_ITEM = { to: "/analyzer", label: "Analyzer", icon: "\u{1F9E0}" };

export function AppShell() {
  const { data: meta } = useMeta();
  const navItems = meta?.daily_review_enabled ? [...NAV_ITEMS, ANALYZER_NAV_ITEM] : NAV_ITEMS;
  return (
    <div className="app-shell">
      <header className="app-topbar">
        <NavLink to="/" className="app-brand" end>
          <WordMark height={28} />
          <KMark size={28} />
        </NavLink>
        <nav className="app-toplinks">
          {navItems.map((item) => (
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
        {navItems.map((item) => (
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

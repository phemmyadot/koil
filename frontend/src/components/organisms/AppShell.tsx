import { NavLink, Outlet, useLocation } from "react-router-dom";
import { KMark } from "../atoms/KMark";
import { WordMark } from "../atoms/WordMark";
import { NotificationBell } from "./NotificationBell";
import { PLCalcFab } from "./PLCalcFab";
import { useMeta } from "../../hooks/useTickers";
import "./AppShell.css";

// Two-tier nav: a top-level section switcher (Equity / Crypto) plus a persistent secondary row
// of that section's sub-pages. Still one source of truth per tier -- SECTIONS.map() produces
// both the top-level links and the section picker, and each section's own `items` produces both
// the desktop secondary row and (via the section-aware bottom nav below) the mobile tab bar. CSS
// media queries switch which rendering is visible, matching the original single-tier approach.
const SECTIONS = [
  {
    key: "equity",
    label: "Equity",
    to: "/",
    icon: "\u{1F4C8}",
    isActive: (pathname: string) => !pathname.startsWith("/crypto"),
    items: [
      { to: "/", label: "Dashboard", icon: "\u{1F4C8}" },
      { to: "/trades", label: "Trades", icon: "\u{1F4CA}" },
      { to: "/watchlists", label: "Watchlists", icon: "⭐" },
    ],
  },
  {
    key: "crypto",
    label: "Crypto",
    to: "/crypto",
    icon: "\u{1FA99}",
    isActive: (pathname: string) => pathname.startsWith("/crypto"),
    items: [{ to: "/crypto", label: "Dashboard", icon: "\u{1FA99}" }],
  },
];

// Analyzer is feature-flagged (ENABLE_DAILY_REVIEW) -- see
// docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md. Appended to Equity's
// sub-items, not merged into SECTIONS, since it's conditional on /api/meta's
// daily_review_enabled rather than always present.
const ANALYZER_NAV_ITEM = { to: "/analyzer", label: "Analyzer", icon: "\u{1F9E0}" };

export function AppShell() {
  const { data: meta } = useMeta();
  const location = useLocation();
  const activeSection = SECTIONS.find((s) => s.isActive(location.pathname)) ?? SECTIONS[0];
  const subItems =
    activeSection.key === "equity" && meta?.daily_review_enabled
      ? [...activeSection.items, ANALYZER_NAV_ITEM]
      : activeSection.items;
  const isHome = location.pathname === "/";
  return (
    <div className="app-shell">
      <header className="app-topbar">
        <NavLink to="/" className="app-brand" end>
          <WordMark height={28} />
          <KMark size={28} />
        </NavLink>
        <nav className="app-toplinks app-sections">
          {SECTIONS.map((section) => (
            <NavLink
              key={section.key}
              to={section.to}
              className={() => (activeSection.key === section.key ? "active" : "")}
            >
              {section.label}
            </NavLink>
          ))}
        </nav>
        <NotificationBell />
      </header>

      <nav className="app-subnav">
        {subItems.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === "/" || item.to === "/crypto"}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <main className="app-content">
        <div className="app-content-inner">
          <Outlet />
        </div>
      </main>

      {isHome && <PLCalcFab />}

      <nav className="app-bottomnav">
        {SECTIONS.map((section) => (
          <NavLink
            key={section.key}
            to={section.to}
            end={section.to === "/"}
            className={() => `app-bottomnav-item${activeSection.key === section.key ? " active" : ""}`}
          >
            <span className="app-bottomnav-icon" aria-hidden="true">
              {section.icon}
            </span>
            <span>{section.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

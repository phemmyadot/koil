import { useState } from "react";
import { useUnreadNotifications } from "../../hooks/useNotifications";
import { NotificationPanel } from "./NotificationPanel";
import "./NotificationBell.css";

export function NotificationBell() {
  const { data: unread } = useUnreadNotifications();
  const [open, setOpen] = useState(false);
  const count = unread?.length ?? 0;

  return (
    <>
      <button
        type="button"
        className="notif-bell"
        onClick={() => setOpen(true)}
        aria-label={count > 0 ? `${count} unread notifications` : "Notifications"}
      >
        {"\u{1F514}"}
        {count > 0 && <span className="notif-bell-count">{count > 99 ? "99+" : count}</span>}
      </button>
      {open && <NotificationPanel onClose={() => setOpen(false)} />}
    </>
  );
}

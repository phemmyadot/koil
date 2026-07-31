import { Modal } from "../atoms/Modal";
import { useAllNotifications, useMarkNotificationRead } from "../../hooks/useNotifications";
import "./NotificationPanel.css";

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function NotificationPanel({ onClose }: { onClose: () => void }) {
  const { data: notifications, isLoading } = useAllNotifications();
  const markRead = useMarkNotificationRead();

  return (
    <Modal title="Notifications" onClose={onClose}>
      {isLoading && <div className="notif-empty">Loading&hellip;</div>}
      {!isLoading && (!notifications || notifications.length === 0) && (
        <div className="notif-empty">No notifications yet.</div>
      )}
      <div className="notif-list">
        {notifications?.map((n) => (
          <div
            key={n.id}
            className={`notif-row ${n.read_at ? "" : "notif-unread"}`}
            onClick={() => !n.read_at && markRead.mutate(n.id)}
          >
            <span className="notif-msg">{n.message}</span>
            <span className="notif-when">{timeAgo(n.created_at)}</span>
          </div>
        ))}
      </div>
    </Modal>
  );
}

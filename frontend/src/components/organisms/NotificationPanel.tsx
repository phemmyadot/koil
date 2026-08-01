import { useNavigate } from "react-router-dom";
import { Modal } from "../atoms/Modal";
import { useAllNotifications, useMarkNotificationRead } from "../../hooks/useNotifications";
import { usePush } from "../../hooks/usePush";
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
  const push = usePush();
  const navigate = useNavigate();

  function openNotification(n: NonNullable<typeof notifications>[number]) {
    if (!n.read_at) markRead.mutate(n.id);
    onClose();
    navigate(`/trades/${n.position_id}`);
  }

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
            className={`notif-row notif-clickable ${n.read_at ? "" : "notif-unread"}`}
            onClick={() => openNotification(n)}
          >
            <span className="notif-msg">{n.message}</span>
            <span className="notif-when">{timeAgo(n.created_at)}</span>
          </div>
        ))}
      </div>
      {push.support !== "unsupported" && (
        <div className="notif-push-toggle">
          <div>
            <div className="notif-push-label">Push notifications</div>
            <div className="notif-push-sub">
              {push.support === "denied"
                ? "Blocked in browser settings"
                : push.subscribed
                  ? "Enabled on this device"
                  : "Get alerts even when the app isn't open"}
            </div>
          </div>
          <label className="notif-push-switch">
            <input
              type="checkbox"
              checked={push.subscribed}
              disabled={push.loading || push.support === "denied"}
              onChange={(e) => (e.target.checked ? push.subscribe() : push.unsubscribe())}
            />
            <span className="notif-push-track" />
          </label>
        </div>
      )}
      {push.error && <div className="notif-push-error">{push.error}</div>}
    </Modal>
  );
}

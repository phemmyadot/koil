import { apiGet, apiPost } from "./client";
import type { Notification } from "./types";

export function listNotifications(unreadOnly = false): Promise<Notification[]> {
  return apiGet<Notification[]>(`/api/notifications?unread=${unreadOnly ? 1 : 0}`);
}

export function markNotificationRead(id: number): Promise<{ ok: true }> {
  return apiPost<{ ok: true }>(`/api/notifications/${id}/read`);
}

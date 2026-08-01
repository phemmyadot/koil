import { apiGet, apiPost } from "./client";

export function getVapidPublicKey(): Promise<{ public_key: string }> {
  return apiGet<{ public_key: string }>("/api/push/vapid-public-key");
}

export function subscribePush(subscription: PushSubscriptionJSON): Promise<{ ok: true }> {
  return apiPost<{ ok: true }>("/api/push/subscribe", subscription);
}

export function unsubscribePush(endpoint: string): Promise<{ ok: true }> {
  return apiPost<{ ok: true }>("/api/push/unsubscribe", { endpoint });
}

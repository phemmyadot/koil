import { useCallback, useEffect, useState } from "react";
import { getVapidPublicKey, subscribePush, unsubscribePush } from "../api/push";
import { urlBase64ToUint8Array } from "../lib/vapid";

export type PushSupport = "unsupported" | "denied" | "available";

// Wraps the full install+subscribe flow from docs/superpowers/specs/2026-07-31-pwa-push-design.md:
// Notification.requestPermission() (must be behind a user action, never on page load) ->
// pushManager.subscribe() with the server's VAPID key -> POST /api/push/subscribe.
export function usePush() {
  const [subscribed, setSubscribed] = useState(false);
  const [support, setSupport] = useState<PushSupport>("available");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      setSupport("unsupported");
      return;
    }
    if (Notification.permission === "denied") setSupport("denied");

    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((sub) => setSubscribed(!!sub))
      .catch(() => {
        // No registration yet / not ready -- treat as not subscribed, not an error state.
      });
  }, []);

  const subscribe = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setSupport("denied");
        return;
      }
      const { public_key } = await getVapidPublicKey();
      const registration = await navigator.serviceWorker.ready;
      const sub = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(public_key) as BufferSource,
      });
      await subscribePush(sub.toJSON() as PushSubscriptionJSON);
      setSubscribed(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const unsubscribe = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const registration = await navigator.serviceWorker.ready;
      const sub = await registration.pushManager.getSubscription();
      if (sub) {
        await unsubscribePush(sub.endpoint);
        await sub.unsubscribe();
      }
      setSubscribed(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  return { subscribed, support, loading, error, subscribe, unsubscribe };
}

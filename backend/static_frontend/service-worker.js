// KOIL service worker. Handles installability (no offline caching -- this app is read/write
// against a live backend, there's no meaningful offline mode, see
// docs/superpowers/specs/2026-07-31-pwa-push-design.md's "Not in scope") and Web Push delivery.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = { title: "KOIL", body: "You have a new alert." };
  if (event.data) {
    try {
      payload = event.data.json();
    } catch {
      payload.body = event.data.text();
    }
  }
  const url = payload.position_id ? `/trades/${payload.position_id}` : "/";
  event.waitUntil(
    self.registration.showNotification(payload.title || "KOIL", {
      body: payload.body || "",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    (async () => {
      const clientsList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      // Focus an already-open tab and navigate it, rather than always opening a new one --
      // most real usage is "I have the app installed and it's already running somewhere."
      for (const client of clientsList) {
        if ("focus" in client) {
          await client.focus();
          if ("navigate" in client) await client.navigate(url);
          return;
        }
      }
      await self.clients.openWindow(url);
    })(),
  );
});

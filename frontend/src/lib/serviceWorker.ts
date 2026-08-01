// Service worker registration. Split out from main.tsx so it's callable/testable independently
// -- registration itself has no meaningful return value to assert on, but keeping it as a named
// function (not inlined at module scope) means it doesn't run during Vitest component tests,
// where `navigator.serviceWorker` doesn't exist in jsdom.

export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {
      // Registration failures (e.g. served over plain HTTP in a non-localhost dev setup)
      // shouldn't break the app -- push/install just won't be available.
    });
  });
}

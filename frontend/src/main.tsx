import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./theme.css";
import App from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The old app treated a page load as "read whatever's cached, no per-request network
      // call is required for correctness" (see backend/app.py's own docstring on this) -- a
      // short staleTime avoids refetching on every component mount as route changes happen,
      // without going stale for long since useTickers/useMeta/useUnreadNotifications already
      // poll on their own cadence regardless.
      staleTime: 10_000,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);

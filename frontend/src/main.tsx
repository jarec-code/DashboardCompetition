import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";

import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { ApiError, setCsrfToken } from "./api/client";
import { App } from "./App";
import { SESSION_KEY } from "./auth/auth";
import { theme } from "./theme";

/** A 401 anywhere means the session ended (e.g. password changed elsewhere): show the login page. */
function onApiError(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    setCsrfToken(null);
    queryClient.setQueryData(SESSION_KEY, null);
  }
}

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onApiError }),
  mutationCache: new MutationCache({ onError: onApiError }),
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: (count, error) =>
    !(error instanceof ApiError && error.status < 500) && count < 1 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Notifications position="top-right" />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);

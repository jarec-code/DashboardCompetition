import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const SESSION = {
  user: { id: 1, email: "admin@example.com", role: "admin", is_active: true, last_login_at: null,
          created_at: "2026-10-18T10:00:00Z" },
  csrf_token: "token-123",
};

const DASHBOARD = {
  competitors_total: 1, competitors_active: 1, offers_tracked: 1, checks_24h: 0, failed_checks_24h: 0,
  missing_or_stale: 0, price_changes_24h: 0, price_changes_7d: 0, cheaper_competitor_rows: 0, comparable_rows: 0,
  biggest_drops: [], recent_changes: [], recent_runs: [],
};
const MONITORING = { problem_competitors: [], problem_offers: [] };

/** Pages call these endpoints after login; everything else returns the session. */
function appData(url: string) {
  if (url === "/api/dashboard") return json(200, DASHBOARD);
  if (url === "/api/monitoring") return json(200, MONITORING);
  return json(200, SESSION);
}

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  }));
}

function renderApp(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("redirects to login when not signed in and shows the API error on a wrong password", async () => {
    const fetchMock = vi.fn((url: string) =>
      url === "/api/auth/me" ? json(401, { detail: "Nie ste prihlásený." })
        : json(401, { detail: "Nesprávny e-mail alebo heslo." }));
    vi.stubGlobal("fetch", fetchMock);
    renderApp("/konkurencia");

    await userEvent.type(await screen.findByLabelText(/E-mail/), "admin@example.com");
    await userEvent.type(screen.getByLabelText(/Heslo/), "zle-heslo");
    await userEvent.click(screen.getByRole("button", { name: "Prihlásiť sa" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Nesprávny e-mail alebo heslo.");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", expect.objectContaining({ method: "POST" }));
  });

  it("logs in and shows the navigation", async () => {
    let loggedIn = false;
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/auth/login") { loggedIn = true; return json(200, SESSION); }
      return loggedIn ? appData(url) : json(401, { detail: "Nie ste prihlásený." });
    }));
    renderApp("/");

    await userEvent.type(await screen.findByLabelText(/E-mail/), "admin@example.com");
    await userEvent.type(screen.getByLabelText(/Heslo/), "spravne-heslo-123");
    await userEvent.click(screen.getByRole("button", { name: "Prihlásiť sa" }));

    for (const label of ["Dashboard", "Konkurencia", "Produkty", "Cenové porovnanie", "Cenová história",
                         "Aktualizácie a logy", "Nastavenia"]) {
      expect(await screen.findByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByText("admin@example.com")).toBeInTheDocument();
  });

  it("sends the CSRF token when logging out", async () => {
    let loggedIn = true;
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/auth/logout") { loggedIn = false; return json(204, undefined); }
      return loggedIn ? appData(url) : json(401, { detail: "Nie ste prihlásený." });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp("/");

    await userEvent.click(await screen.findByRole("button", { name: "Odhlásiť" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", expect.objectContaining({
      method: "POST", headers: expect.objectContaining({ "X-CSRF-Token": "token-123" }),
    })));
    expect(await screen.findByRole("button", { name: "Prihlásiť sa" })).toBeInTheDocument();
  });
});

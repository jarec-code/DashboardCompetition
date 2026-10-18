import { defineConfig, devices } from "@playwright/test";

// Browser tests against the real backend and the production build (run `npm run build` first).
// Files run in order (01-…, 02-…) against one database that is reset when the server starts.
// The server resets its own e2e database on every start, see backend/tests/e2e_server.py.
const PORT = 8010;
const python = process.env.E2E_PYTHON ?? ".venv/bin/python";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: "sk-SK",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `${python} -m tests.e2e_server`,
    cwd: "../backend",
    url: `http://127.0.0.1:${PORT}/readyz`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { E2E_PORT: String(PORT) },
  },
});

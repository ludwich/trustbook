import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end smoke test (SPEC §13). Runs against a real Trustbook started with
 * TRUSTBOOK_MOCK_LLM=1, e.g. `docker compose -f compose.dev.yaml up` or uvicorn on :8484.
 * The test configures the "mock" chat preset itself. Never points at a real chat endpoint.
 *
 *   E2E_BASE_URL=http://localhost:8484 pnpm e2e
 *
 * No test touches the internet unless E2E_INTERNET=1 is set (public web page and YouTube in
 * job-status.spec.ts); release.yml never sets it.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8484",
    viewport: { width: 1280, height: 800 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

import { defineConfig, devices } from "@playwright/test";

// 2026 best practices:
// - Run against the production build via `vite preview` so we test the
//   same bundle we ship.
// - All backend traffic is intercepted with `page.route` inside the specs
//   (see tests/e2e/fixtures/mocks.ts). The dev/preview server NEVER talks
//   to the FastAPI backend during e2e — tests are hermetic.
// - Headless by default; CI gets retries + traces on first retry only.

const PORT = Number(process.env.E2E_PORT ?? 4173);
const HOST = "127.0.0.1";

export default defineConfig({
    testDir: "./tests/e2e",
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    workers: process.env.CI ? 2 : undefined,
    reporter: process.env.CI
        ? [["github"], ["html", { open: "never" }]]
        : [["list"], ["html", { open: "never" }]],
    use: {
        baseURL: `http://${HOST}:${PORT}`,
        trace: "on-first-retry",
        screenshot: "only-on-failure",
        video: "retain-on-failure",
    },
    expect: {
        timeout: 5_000,
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
    webServer: {
        command: `bun run preview -- --port ${PORT} --host ${HOST} --strictPort`,
        url: `http://${HOST}:${PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        stdout: "ignore",
        stderr: "pipe",
    },
});

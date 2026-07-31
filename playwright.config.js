import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

const python =
  process.env.PYTHON ??
  (existsSync("venv/bin/python") ? "venv/bin/python" : "python");

export default defineConfig({
  testDir: "tests/frontend",
  testMatch: "*.spec.js",
  fullyParallel: false,
  retries: 1,
  reporter: "line",
  snapshotPathTemplate:
    "{testDir}/{testFilePath}-snapshots/{arg}-{projectName}{ext}",
  use: {
    baseURL: "http://127.0.0.1:5010",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], browserName: "chromium" },
    },
  ],
  webServer: {
    command: `PORT=5010 COOKIE_SECURE=0 ${python} web_app.py`,
    url: "http://127.0.0.1:5010/health/live",
    reuseExistingServer: true,
    timeout: 120000,
  },
});

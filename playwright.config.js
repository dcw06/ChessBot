import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

const python =
  process.env.PYTHON ??
  (existsSync(".venv/Scripts/python.exe")
    ? ".venv/Scripts/python.exe"
    : existsSync(".venv/bin/python")
      ? ".venv/bin/python"
      : "python");

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
    command: `"${python}" web_app.py`,
    env: {
      ...process.env,
      CHESS_USERNAME: "yuandan",
      COOKIE_SECURE: "0",
      PORT: "5010",
    },
    url: "http://127.0.0.1:5010/health/live",
    reuseExistingServer: true,
    timeout: 120000,
  },
});

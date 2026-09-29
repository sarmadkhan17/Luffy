import { defineConfig } from "@playwright/test";
import { homedir } from "node:os";
export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 6000 },
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile:
          process.env.PW_RESULTS ?? "evidence/browser-results.json",
      },
    ],
  ],
  use: {
    baseURL: "http://127.0.0.1:4174",
    viewport: { width: 1440, height: 1080 },
    headless: true,
    launchOptions: {
      executablePath:
        process.env.PREVIEW_CHROMIUM ??
        `${homedir()}/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`,
    },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // DEMO (fixture) build: the reviewed preview suites run against fixtures
      // only; the LIVE build is exercised against the FastAPI test server.
      command:
        "npx vite build --mode demo --outDir .demo-dist && npx vite preview --mode demo --outDir .demo-dist --host 127.0.0.1 --port 4174 --strictPort",
      url: "http://127.0.0.1:4174",
      reuseExistingServer: false,
      timeout: 120000,
    },
    {
      command:
        "npx vite build --mode test-fixtures --outDir .test-dist && npx vite preview --mode test-fixtures --outDir .test-dist --host 127.0.0.1 --port 4175 --strictPort",
      url: "http://127.0.0.1:4175/tests/harness/index.html",
      reuseExistingServer: false,
      timeout: 120000,
    },
    {
      // LIVE build + the real FastAPI app over a temporary fixture ROOT
      // (tests/owner_frontend_server.py). Fake kernel Owner Interface and chat.
      command: `npx vite build && cd .. && ${process.env.LUFFY_PYTHON ?? "./venv/bin/python"} -m tests.owner_frontend_server 4176`,
      url: "http://127.0.0.1:4176/",
      reuseExistingServer: false,
      timeout: 120000,
    },
  ],
});

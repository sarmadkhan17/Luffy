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
    ["json", { outputFile: "evidence/browser-results.json" }],
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
      command: "npx vite preview --host 127.0.0.1 --port 4174 --strictPort",
      url: "http://127.0.0.1:4174",
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command:
        "npx vite build --mode test-fixtures --outDir .test-dist && npx vite preview --outDir .test-dist --host 127.0.0.1 --port 4175 --strictPort",
      url: "http://127.0.0.1:4175/tests/harness/index.html",
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});

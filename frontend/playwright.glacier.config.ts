/** Static frontend only. Every backend request is intercepted by test fixtures.
 * Never starts Dashboard, Kernel, IPC or an external provider. */
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser", testMatch: "glacier-corrections.spec.ts", workers: 1,
  timeout: 30000, outputDir: "/mnt/luffy-data/test-tmp/glacier-browser-results",
  reporter: [["list"], ["json", { outputFile: "/mnt/luffy-data/test-tmp/glacier-browser-results.json" }]],
  use: { baseURL: "http://127.0.0.1:4180", viewport: { width: 1440, height: 1080 }, headless: true,
    launchOptions: { executablePath: process.env.PREVIEW_CHROMIUM }, trace: "retain-on-failure" },
  webServer: { command: "npx vite preview --outDir dist --host 127.0.0.1 --port 4180 --strictPort", url: "http://127.0.0.1:4180", reuseExistingServer: false },
});

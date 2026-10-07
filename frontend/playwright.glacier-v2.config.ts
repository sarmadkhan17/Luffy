/** Isolated production assets: every API read is an intercepted fixture.
 * No backend, provider, venue or kernel is started by these browser tests. */
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser", testMatch: ["glacier-corrections.spec.ts", "glacier-v2.spec.ts"], workers: 1,
  timeout: 60000, outputDir: "/mnt/luffy-data/test-tmp/glacier-v2/browser-results",
  reporter: [["list"], ["json", { outputFile: "/mnt/luffy-data/test-tmp/glacier-v2/browser-results.json" }]],
  use: { baseURL: "http://127.0.0.1:4180", viewport: { width: 1440, height: 900 }, headless: true,
    launchOptions: { executablePath: process.env.PREVIEW_CHROMIUM }, trace: "retain-on-failure" },
  webServer: { command: "npx vite preview --outDir dist --host 127.0.0.1 --port 4180 --strictPort", url: "http://127.0.0.1:4180", reuseExistingServer: false },
});

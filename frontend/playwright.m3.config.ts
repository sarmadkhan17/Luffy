/** M3 evidence capture: only the LIVE build + the temporary FastAPI test
 * server (tests/owner_frontend_server.py, synthetic seeded fixture journal).
 * Used for final M3 screenshots/measurements; the full suite keeps
 * playwright.config.ts. */
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testMatch: /m3-.*\.spec\.ts/,
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile: process.env.PW_RESULTS ?? "/tmp/luffy-m3-pw-results.json",
      },
    ],
  ],
  webServer: Array.isArray(base.webServer)
    ? [base.webServer[2]]
    : base.webServer,
});

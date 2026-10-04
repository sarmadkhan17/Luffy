import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testMatch: /m4-.*\.spec\.ts/,
  reporter: [["list"], ["json", { outputFile: "evidence/m4/browser-results.json" }]],
});

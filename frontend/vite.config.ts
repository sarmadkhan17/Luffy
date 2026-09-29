import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
/** production → LIVE build served by FastAPI at /owner-preview/ (no fixtures);
 * demo → fixture build for isolated development and the reviewed browser tests;
 * test-fixtures → the regression harness. */
export default defineConfig(({ mode }) => ({
  base: mode === "production" ? "/owner-preview/" : "/",
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.tsx", "tests/**/*.test.ts"],
  },
  build: {
    sourcemap: mode !== "production",
    ...(mode === "test-fixtures"
      ? { rollupOptions: { input: "tests/harness/index.html" } }
      : {}),
  },
}));

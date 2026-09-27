import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.tsx", "tests/**/*.test.ts"],
  },
  build: {
    sourcemap: true,
    ...(mode === "test-fixtures"
      ? { rollupOptions: { input: "tests/harness/index.html" } }
      : {}),
  },
}));

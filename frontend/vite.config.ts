import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// DEV_API_TARGET is server-only; it is never injected into the client bundle.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    proxy: {
      "/api": loadEnv(mode, ".", "").DEV_API_TARGET || "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
}));

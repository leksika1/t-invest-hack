import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В dev-режиме /api проксируется на локальный бэкенд (uvicorn на :8000).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});

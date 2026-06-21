import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for the GridLock command-center SPA. The dev server proxies
// /api/* and /api/ws/* to the FastAPI gateway on :8000 so the browser can
// use same-origin fetch() + WebSocket() — no CORS gymnastics in dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    target: "es2020",
    sourcemap: true,
    outDir: "dist",
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Dev: 5173 (UI) proxies /api -> 8000 (FastAPI).
// Prod: nginx (see Dockerfile.frontend) serves the built static files;
// the runtime API base is injected via window.APTIRO_API on the host.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.APTIRO_API || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});

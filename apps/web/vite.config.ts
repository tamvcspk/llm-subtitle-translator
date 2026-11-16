import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The build is copied into the Python image as /app/static, so every asset URL is absolute from "/".
// In development the worker answers /api and /login; `npm run dev` proxies both to it.
const worker = process.env.GST_WORKER_URL ?? "http://localhost:6789";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "safari16",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": worker,
      "/login": worker,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});

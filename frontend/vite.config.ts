import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API + WebSocket to the FastAPI backend so the app uses
// same-origin relative paths (which also works in production, where
// CloudFront routes these paths to the ALB/API origin).
const BACKEND = process.env.VITE_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/health": BACKEND,
      "/incidents": BACKEND,
      "/history": BACKEND,
      "/events": BACKEND,
      "/live": { target: BACKEND, ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});

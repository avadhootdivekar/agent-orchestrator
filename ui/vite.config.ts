import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands INSIDE the Python package so the wheel ships the dashboard and
// `ao ui` can serve it with no extra install step. agent_orchestrator/ui/app.py reads
// the same directory (its STATIC_DIR constant) — keep the two in sync.
const PYTHON_PACKAGE_STATIC = "../src/agent_orchestrator/ui/static";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: PYTHON_PACKAGE_STATIC,
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    // `npm run dev` serves the UI on 5173 and forwards API calls to a separately
    // running `ao ui`, so the frontend gets hot reload without a production build.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
});

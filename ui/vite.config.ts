import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands INSIDE the Python package so the wheel ships the dashboard and
// `ao ui` can serve it with no extra install step. agent_orchestrator/ui/app.py reads
// the same directory (its STATIC_DIR constant) — keep the two in sync.
const PYTHON_PACKAGE_STATIC = "../src/agent_orchestrator/ui/static";

// Dev proxy target (`ao ui` default port). Also what the dev-only Origin rewrite points at.
const DEV_API_TARGET = "http://127.0.0.1:8765";

/** The slice of the dev proxy's event API used below. */
interface ProxyEvents {
  on(
    event: "proxyReq",
    listener: (proxyReq: { setHeader(name: string, value: string): void }) => void,
  ): void;
}

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
        target: DEV_API_TARGET,
        changeOrigin: true,
        // Dev only (HLD §15 #8): the browser sends `Origin: http://localhost:5173`, which the
        // server's same-origin CSRF check rejects for POSTs when auth is on. Rewrite it to the
        // proxy target so `npm run dev` can log in. Never applies to the production build.
        configure: (proxy) => {
          // The proxy is an EventEmitter, but its typings need @types/node (not installed here).
          (proxy as unknown as ProxyEvents).on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("origin", DEV_API_TARGET);
          });
        },
      },
    },
  },
});

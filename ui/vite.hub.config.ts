import { defineConfig } from "vite";

// The hub login-page script (HLD §17.6, §16 row 14). A separate library build: the page is
// served by the Python hub and must not depend on the SPA bundle. Fixed file names, no hashing
// and no sourcemap keep the committed output reproducible (the CI rebuild-diff gate).
// agent_orchestrator/auth serves this directory; keep the path in sync with its asset constant.
const HUB_ASSETS_DIR = "../src/agent_orchestrator/auth/assets";

export default defineConfig({
  build: {
    outDir: HUB_ASSETS_DIR,
    // The directory also holds other committed package data; never wipe it.
    emptyOutDir: false,
    sourcemap: false,
    minify: true,
    cssCodeSplit: false,
    lib: {
      entry: "src/hub/hubAuth.ts",
      // The entry exports nothing; Vite still wants a global name for an IIFE.
      name: "AoHubAuth",
      formats: ["iife"],
      fileName: () => "hub-auth.js",
      cssFileName: "hub-auth",
    },
  },
});

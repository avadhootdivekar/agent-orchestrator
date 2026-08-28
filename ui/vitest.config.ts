import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config";

// Kept separate from vite.config.ts: as of Vite 8 / Vitest 4 the `test` key is no longer
// part of Vite's own config type, so co-locating it fails `tsc -b`.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  }),
);

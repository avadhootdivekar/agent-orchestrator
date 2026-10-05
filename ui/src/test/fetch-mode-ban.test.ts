/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

/**
 * dev-security #13 / HLD §2.1, §17.3: the SPA (and the hub page script) must call `fetch()` with
 * NO `mode` option, and must not use another transport that would bypass the session-proof header.
 * A same-origin fetch() sends the real `Origin` the server's CSRF check needs; the proof header is
 * added in exactly one place (`api.ts` `request()`, mirrored by `hubAuthCore.ts`).
 */

/** Source-path prefix used in reports (the glob keys are relative to this test file). */
const SRC_PREFIX = "ui/src/";
/** The only files allowed to call `fetch(`. `hub/hubAuthCore.ts` lands with T-R7JhTL (absent is fine). */
const FETCH_ALLOWLIST: ReadonlySet<string> = new Set(["ui/src/api.ts", "ui/src/hub/hubAuthCore.ts"]);

const FETCH_CALL = /\bfetch\(/;
const MODE_OPTION = /\bmode\s*:/;
const BANNED_TRANSPORTS = /\bXMLHttpRequest\b|\bnavigator\s*\.\s*sendBeacon\b/;

/** Pure checker over a `{ "ui/src/x.ts": source }` map; returns human-readable violations. */
export function findViolations(
  files: Record<string, string>,
  allowlist: ReadonlySet<string> = FETCH_ALLOWLIST,
): string[] {
  const violations: string[] = [];
  for (const [path, source] of Object.entries(files)) {
    const callsFetch = FETCH_CALL.test(source);
    if (callsFetch && !allowlist.has(path)) {
      violations.push(`${path}: calls fetch( but is not in the allowlist`);
    }
    if (allowlist.has(path) && MODE_OPTION.test(source)) {
      violations.push(`${path}: contains a \`mode:\` option`);
    }
    if (BANNED_TRANSPORTS.test(source)) {
      violations.push(`${path}: uses XMLHttpRequest or navigator.sendBeacon (bypasses the proof header)`);
    }
  }
  return violations;
}

// Every non-test source file, as raw text. `!../test/**` keeps fixtures/tests out of the scan.
const modules = import.meta.glob(["../**/*.ts", "../**/*.tsx", "!../test/**"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const scanned: Record<string, string> = Object.fromEntries(
  Object.entries(modules).map(([key, source]) => [key.replace(/^\.\.\//, SRC_PREFIX), source]),
);

describe("fetch-mode-ban", () => {
  it("scans the real source tree (and excludes the tests)", () => {
    const paths = Object.keys(scanned);
    expect(paths).toContain("ui/src/api.ts");
    expect(paths).toContain("ui/src/auth/AuthGate.tsx");
    expect(paths.some((p) => p.startsWith("ui/src/test/"))).toBe(false);
  });

  it("only allowlisted files call fetch(, none has a `mode:` option, none uses XHR/sendBeacon", () => {
    expect(findViolations(scanned)).toEqual([]);
  });

  it("no auth source renders raw HTML (no dangerouslySetInnerHTML under ui/src/auth)", () => {
    const authFiles = Object.entries(scanned).filter(([path]) => path.startsWith("ui/src/auth/"));
    expect(authFiles.length).toBeGreaterThan(5);
    for (const [path, source] of authFiles) {
      expect(source, path).not.toContain("dangerouslySetInnerHTML");
    }
  });

  it("api.ts really is the fetch caller being checked", () => {
    expect(FETCH_CALL.test(scanned["ui/src/api.ts"])).toBe(true);
  });

  describe("negative controls (the checker is not vacuous)", () => {
    it("flags a mode option in an allowlisted file", () => {
      const out = findViolations({ "ui/src/api.ts": 'fetch(u, { mode: "cors" })' });
      expect(out).toHaveLength(1);
      expect(out[0]).toContain("mode");
    });

    it("flags fetch( in a file outside the allowlist", () => {
      expect(findViolations({ "ui/src/other.ts": "await fetch(u)" })).toHaveLength(1);
    });

    it("flags XMLHttpRequest and sendBeacon anywhere", () => {
      expect(findViolations({ "ui/src/a.ts": "new XMLHttpRequest()" })).toHaveLength(1);
      expect(findViolations({ "ui/src/b.ts": "navigator.sendBeacon(u)" })).toHaveLength(1);
    });

    it("accepts a clean allowlisted fetch", () => {
      expect(findViolations({ "ui/src/api.ts": "fetch(u, { method: 'POST' })" })).toEqual([]);
    });
  });
});

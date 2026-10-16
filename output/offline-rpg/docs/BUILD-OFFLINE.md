# Build, run and offline guarantees

Status: describes the build and guard as shipped in `scripts/` (every command and path below was run or checked in w06-08; `build`, `check-offline` and `preview` work, and `npm test` / `check-all` currently report 2 failing unit tests from other units, not a build problem). Rules or flags marked **(planned)** are not implemented yet.

## 1. Tooling

- **Zero dependencies.** `package.json` has no `dependencies` or `devDependencies`; `npm install` is not needed. Requires `node >= 18`.
- Scripts (from `package.json`):
  - `npm run build` → `node scripts/build.mjs`: writes `dist/index.html`, then runs the offline guard on it.
  - `npm run preview` → `node scripts/preview.mjs`: static server for `dist/`.
  - `npm test` → `node scripts/build.mjs --verify && npm run test:unit`: builds twice in memory and fails unless the output is byte-identical, then runs the unit suites.
  - `npm run test:unit` → `node --test 'tests/**/*.test.js' 'tests/scripts/*.test.mjs'`: unit suites only.
  - `npm run check-offline` → `node scripts/check-offline.mjs dist/index.html`: the offline guard.
  - `npm run bench` → `node scripts/bench.mjs`: headless timing of sim and draw against a canvas stub (`--entities`, `--frames`, `--warmup`, `--seed`, `--json`). Not part of `check-all`.
  - `npm run check-all` → `node scripts/run-all-checks.mjs`: runs the `build`, `test` and `check-offline` steps in order, prints one `PASS`/`FAIL` line per step, and exits 1 if any step failed.
- Source of truth for file order: `JS_ORDER` in `scripts/build.mjs` (see §2). Only the JavaScript order is listed there;
  CSS is concatenated from `styles/*.css` in sorted order.

## 2. Single-file build (`scripts/build.mjs`)

Input: the template `index.html`, which must contain the markers `<!--@CSS-->` and `<!--@JS-->` exactly once each.

Algorithm (as implemented):

```
css  = concat(read(f) for f in styles/**/*.css, sorted by name), joined with '\n'
       → refuse if the CSS contains "</style"
js   = for f in orderedJsFiles(): "/* ==== <f> ==== */\n" + read(f) + "\n;\n"
       orderedJsFiles() = files under src/**/*.js that appear in JS_ORDER, in JS_ORDER order,
                          then any unlisted files (sorted, with a warning on stderr)
       → refuse any file that contains "<!--"
       → replace "</script" with "<\/script" (case-insensitive)
html = template with <!--@CSS--> → <style>css</style> and <!--@JS--> → <script>js</script>
write dist/index.html, then run scripts/check-offline.mjs on it; a finding fails the build
```

- **One classic script block, no `type="module"`, no `src=`.** Every source file is an IIFE that attaches to `window.RPG`.
- **Deterministic.** There are no timestamps in the output. `npm test` checks this on every run.
- **Not written yet (planned):** `dist/BUILD-INFO.json` (version, git sha, sha256, size) and a `--min` flag.
- **Keeping `JS_ORDER` current:** a file on disk that is missing from `JS_ORDER` is still bundled, but it is appended last with a warning, and `npm test` (`build.mjs --verify`) fails on it, on a duplicate entry, and on a listed file that is missing. Load order matters only for load-time dependencies between files.

## 3. file:// strategy

`file://` works for loading `dist/index.html` because:

1. All JS and CSS are inlined in classic (non-module) `<script>` and `<style>` blocks, so there are no cross-file loads.
2. The runtime code avoids `fetch()`, `XMLHttpRequest`, `WebSocket`, `import()` and module scripts. The guard enforces the first four and dynamic import (see §4).
3. Data is JavaScript, not JSON loaded at runtime. Fonts use the system stack (no `@font-face` or `url()`).
4. Graphics are drawn on Canvas 2D. Audio is WebAudio-synthesised (`src/audio/audio.js`), started on the first key or pointer press.
5. **`localStorage` under `file://` is browser-dependent.** Firefox keys `file://` storage per file path, and moving or renaming the file can lose saves. Chrome shares storage across `file://` URLs. The mitigation is Export/Import in the Save/Load menu (`src/ui/saveload.js`, `src/persistence/files.js`); when `localStorage` is unusable the storage adapter falls back to memory for the session.
   For stable saves, serve the folder with `npm run preview` or `python3 -m http.server -d dist`.
6. Export uses a Blob download and import uses a file picker, both started from a key press in the Save/Load menu (`src/persistence/files.js`). There is no clipboard path.

## 4. Offline guard (`scripts/check-offline.mjs`)

Usage: `node scripts/check-offline.mjs [path ...]`. With no paths it scans the default roots `src/`, `styles/`, `index.html` and `dist/`, skipping any that do not exist. Exit codes: `0` clean, `1` findings (printed as `file:line: rule: match`), `2` bad usage or unreadable path.

Rules currently enforced (rule ids are the ones printed by the script):

| Id | Matches | Why |
|---|---|---|
| `no-remote-url` | `https?://`, `wss?://`, `ftp://` | remote assets and APIs |
| `no-protocol-relative` | protocol-relative `//host` references | remote loads |
| `no-css-import` | `@import` | remote stylesheets |
| `no-network-api` | `fetch(`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `sendBeacon` | runtime network |
| `no-dynamic-import` | `import(` | breaks `file://` |
| `no-cdn-or-font-host` | known CDN and font host names (substring, case-insensitive) | remote fonts and scripts |

Allow-list: the XML and SVG namespace URIs listed in `check-offline.mjs` (for example `http://www.w3.org/2000/svg`). These are compared as strings and never fetched.

**Not enforced yet (planned):** static `import`/`export` statements and `type="module"`, `eval` and `new Function`, telemetry identifiers, external `<link>`/`<img>`/`<iframe>`/`<audio>` tags, and a size budget. Until they are added, the CSP in `index.html` is the only guard against these in the built page.

`dist/index.html` carries this Content-Security-Policy, so the browser blocks network requests the scan might miss:

```
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:;
media-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'
```

## 5. Static preview (`scripts/preview.mjs`)

`node scripts/preview.mjs [--port 8777] [--host 127.0.0.1]`. It serves `dist/` only, over `node:http` with no dependencies:

- Default bind is `127.0.0.1:8777`. Pass `--host` to bind elsewhere.
- Responds with `Cache-Control: no-store` and a content type from the file extension.
- Path-traversal safe: the resolved path must stay inside `dist/`.
- Run `npm run build` first. Without `dist/`, it warns and returns 404 for files.

**Not written yet (planned):** a development page that loads each source file with its own `<script>` tag, a `/dist/` mount, and a `--lan` flag for testing on a phone on the same network.

## 6. Manual offline checklist

The full checklist with status is in [`TEST-CHECKLIST.md`](TEST-CHECKLIST.md). The short form:

1. Load `dist/index.html` online, then with the network disabled, via `file://` and via `npm run preview`.
2. Reload mid-game, save, reload, load, export, delete and import.
3. Check the browser's network log shows no requests and the console shows no CSP violations.

## 7. Repository hygiene

- `dist/` is git-ignored (`.gitignore` contains `dist/`). The build reproduces it from source with `npm run build`.
- **Not written yet (planned):** a `release` script and a committed `release/offline-rpg-<version>.html` copy.

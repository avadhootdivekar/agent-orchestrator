# Offline RPG

A single-player, top-down 2D hack-and-slash action RPG that runs in a desktop browser with **no internet
connection** after the files are obtained. Gameplay is drawn on a Canvas 2D surface; the game is plain JavaScript
with no runtime dependencies.

> **Status: in progress.** Shipped so far: the build (`npm run build` writes `dist/index.html`), the static preview
> server, the offline guard, a Canvas 2D map with circle-vs-tile collision, and player movement with aiming. The
> save/load and export/import code is written and tested, but no UI calls it yet. Data definitions for weapons,
> skills, monsters, items and difficulties are shipped, but the simulation does not use them yet, and the menu
> screens are not written. See [`docs/GAMEPLAY-SYSTEMS.md`](docs/GAMEPLAY-SYSTEMS.md) for what is wired and what is not.
> Test status per scenario is tracked in [`docs/TEST-CHECKLIST.md`](docs/TEST-CHECKLIST.md).

## Requirements

- A modern desktop browser (Chromium- or Firefox-based; Safari is expected to work but is not a primary target).
- Node.js `>= 18` **only** for the build, preview and test scripts. Playing the built game needs no Node.
- No `npm install` step: the project has zero dependencies.

## Running the game

### Option 1: open the built file directly (`file://`)

Open `dist/index.html` in the browser. The built file inlines all JavaScript and CSS into one classic script and
one style block, so it does not load any other file at runtime and works from `file://`.

Caveat: browsers scope `localStorage` for `file://` pages differently (Firefox keys it per file path; moving or
renaming the file can make existing saves disappear). For reliable saves, serve the folder over a local HTTP server
instead, or use **Export save** / **Import save** to move progress between copies.

### Option 2: local static server

Use this when saves must stay put across moves, or when the browser behaves differently under `file://`:

```bash
python3 -m http.server -d dist 8000     # then open http://127.0.0.1:8000/
```

`npm run preview` serves `dist/` from `127.0.0.1:8777` (run `npm run build` first). It does not yet serve a development
page that loads each source file separately.

## Commands

Run from `output/offline-rpg/`. There are no dependencies to install.

| Command | What it does |
|---|---|
| `npm run build` | Runs `node scripts/build.mjs`: writes `dist/index.html`, then runs the offline scan on it. |
| `npm run preview` | Runs `node scripts/preview.mjs`: static server with no external requests. |
| `npm test` | Runs `node scripts/build.mjs --verify` (two in-memory builds must be byte-identical), then `npm run test:unit`. |
| `npm run test:unit` | Runs `node --test` over `tests/**/*.test.js` and `tests/scripts/*.test.mjs`, without the build check. |
| `npm run check-offline` | Runs `node scripts/check-offline.mjs dist/index.html`: fails on any remote URL, network API, module import or telemetry reference. |
| `npm run check-all` | Runs `node scripts/run-all-checks.mjs`: the `build`, `test` and `check-offline` steps in order. Prints one `PASS`/`FAIL` line per step and exits 1 if any step failed. |

`dist/` is git-ignored. The build is deterministic: the same sources produce byte-identical HTML.

## Offline behaviour

- **No runtime network use.** No CDN, Google Fonts, remote images, audio, APIs, telemetry or authentication. Fonts
  are the system font stack; sprites, sounds and effects are drawn in code (Canvas 2D and Web Audio).
- **Enforced by tooling.** The offline scan (`npm run check-offline`) rejects remote URLs, CDN and font hosts,
  `fetch`/`XMLHttpRequest`/`WebSocket` and dynamic `import()`. The built page also carries a Content-Security-Policy
  with `connect-src 'none'`, so the browser blocks any network request the scan might miss. ES module syntax and
  `eval` are not checked by the scan yet (see [`docs/BUILD-OFFLINE.md`](docs/BUILD-OFFLINE.md) §4).
- **Playable with the network removed.** The intended check is: load the game, disconnect the network, reload, and
  play through save, reload, export, delete and import. The checklist is in
  [`docs/TEST-CHECKLIST.md`](docs/TEST-CHECKLIST.md).
- **`file://` works for loading.** Headless Google Chrome 138 loads `dist/index.html` from `file://` with no page
  exceptions, no console errors and no non-local requests (wave 1 verify, Chrome check over CDP). Only the page load
  was checked, not gameplay or saves. Saves under `file://` are still browser-dependent, as described above.

## Saves

Progress is saved locally in the browser (`localStorage`). Saves carry a schema version and can be exported to a
human-readable JSON file and imported on another machine. Format and migration rules are in
[`docs/SAVE-SCHEMA.md`](docs/SAVE-SCHEMA.md). If `localStorage` is unavailable, the game is intended to show a
banner and let the player use Export instead.

## Controls (planned defaults)

Movement `WASD` (or arrow keys), aim and basic attack with the mouse, skills `1`–`5`, `Q`/`E`, dash `Space`, weapon
switch `Tab`, inventory `I`, character `C`, skills tree `K`, settings `O`, save/load menu `M`, quick save `F5`,
quick load `F9`, pause `Esc`. All keys can be rebound in Settings. The full map is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §9.

## Repository layout

```
output/offline-rpg/
  README.md               this file
  docs/                   design and test documents
    ARCHITECTURE.md       module boundaries, game loop, public API contract (shipped vs planned marked)
    DATA-DEFINITIONS.md   weapon, skill, monster, item and difficulty data definitions
    GAMEPLAY-SYSTEMS.md   the five gameplay systems: what is shipped as data and what is wired
    SAVE-SCHEMA.md        save-state schema and migration rules
    BUILD-OFFLINE.md      build pipeline, file:// strategy and offline-scan rules
    TEST-CHECKLIST.md     8 offline test scenarios and 22 acceptance steps with status
  package.json            npm scripts only, no dependencies
  scripts/                build, preview, offline-scan and run-all-checks scripts
  src/                    game source: core, data, input, sim, world, persistence, render, audio, main.js
  styles/                 stylesheet source
  tests/                  unit suites per area, plus offline-scan fixtures
  dist/                   generated by npm run build (git-ignored)
```

Game state, input, simulation, rendering and persistence are kept in separate modules so a later multiplayer mode
can reuse the simulation. Multiplayer is **not** implemented.

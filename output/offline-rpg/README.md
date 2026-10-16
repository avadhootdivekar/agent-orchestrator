# Offline RPG

A single-player, top-down 2D hack-and-slash action RPG that runs in a desktop browser with **no internet
connection** after the files are obtained. Gameplay is drawn on a Canvas 2D surface; the game is plain JavaScript
with no runtime dependencies.

> **Status: early scaffold.** This directory currently holds the design documents in [`docs/`](docs/). The source
> tree, build script, preview server and tests are specified there but **not implemented yet**. Commands below are
> the planned interface and are marked as such until they exist. Test status per scenario is tracked in
> [`docs/TEST-CHECKLIST.md`](docs/TEST-CHECKLIST.md).

## Requirements

- A modern desktop browser (Chromium- or Firefox-based; Safari is expected to work but is not a primary target).
- Node.js `>= 18` **only** for the planned build, preview and test scripts. Playing the built game needs no Node.
- No `npm install` step: the project is planned to have zero dependencies.

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

The planned `npm run preview` will serve the same files from `127.0.0.1:8777` and also a development page that loads
each source file separately.

## Build and test (planned)

| Command | What it will do |
|---|---|
| `npm run build` | Runs `node build/build.js`: inlines `index.src.html` + sources into `dist/index.html` and writes `dist/BUILD-INFO.json`. |
| `npm run preview` | Runs `node build/preview.js`: static server on `127.0.0.1:8777`, no external requests. |
| `npm test` | Runs `node tests/run.js`: headless unit and simulation tests (no browser needed). |
| `npm run scan` | Runs `node build/scan-offline.js dist/index.html` plus the `src/` tree: fails on any remote URL, network API, module import or telemetry reference. |
| `npm run check` | Build, then test, then scan. |

The build is deterministic: the same sources produce byte-identical HTML.

## Offline behaviour

- **No runtime network use.** No CDN, Google Fonts, remote images, audio, APIs, telemetry or authentication. Fonts
  are the system font stack; sprites, sounds and effects are drawn in code (Canvas 2D and Web Audio).
- **Enforced by tooling.** The offline scan rejects remote URLs, `fetch`/`XMLHttpRequest`/`WebSocket`, ES modules
  and `eval`. The built page also carries a Content-Security-Policy with `connect-src 'none'`, so the browser blocks
  any network request the scan might miss.
- **Playable with the network removed.** The intended check is: load the game, disconnect the network, reload, and
  play through save, reload, export, delete and import. The checklist is in
  [`docs/TEST-CHECKLIST.md`](docs/TEST-CHECKLIST.md).
- **`file://` statement: TO-BE-CONFIRMED-BY-VERIFY.** The design intends `dist/index.html` to run from `file://`
  without a server. This is not yet confirmed by a test. The final verification pass must confirm or correct this
  statement; until then, treat a local static server as the supported way to run the game.

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
  docs/                   design and test documents (present now)
    ARCHITECTURE.md       module boundaries, game loop, public API contract
    DATA-DEFINITIONS.md   weapon, skill, monster, item and difficulty data definitions
    SAVE-SCHEMA.md        save-state schema and migration rules
    BUILD-OFFLINE.md      build pipeline, file:// strategy and offline-scan rules
    TEST-CHECKLIST.md     8 offline test scenarios and 22 acceptance steps with status
  package.json            planned: scripts only, no dependencies
  index.src.html          planned: HTML template with CSS and JS markers
  build/                  planned: build, preview and offline-scan scripts
  src/                    planned: data/, core/, input/, world/, combat/, persistence/, render/, audio/, ui/
  tests/                  planned: headless test runner and suites
  dist/                   generated build output (git-ignored)
```

Game state, input, simulation, rendering and persistence are kept in separate modules so a later multiplayer mode
can reuse the simulation. Multiplayer is **not** implemented.

# Offline RPG

A single-player, top-down 2D hack-and-slash action RPG that runs in a desktop browser with **no internet
connection** after the files are obtained. Gameplay is drawn on a Canvas 2D surface; the game is plain JavaScript
with no runtime dependencies.

> **Status: playable in a desktop browser, not yet manually verified.** The build, offline scan, simulation (combat,
> weapons, skills, AI, loot, bosses, areas), HUD, menu screens (main menu, pause, inventory, character, skills,
> settings, save/load) and save/export/import are all wired and covered by unit and headless integration
> tests (972 pass, 0 fail at the time of writing, run in w14-01). Scripted headless Chrome runs (not a person, software canvas) covered boot, play, save, an offline reload and reopen, and showed the effects layer (telegraphs, damage numbers, particles) drawing. Manual walkthroughs are **not** recorded; nothing was run on a real device or touch hardware, and the swing and death animations have not been looked at by a person; see [`docs/VERIFICATION-REPORT.md`](docs/VERIFICATION-REPORT.md) for the step-by-step evidence and limits, [`docs/TEST-CHECKLIST.md`](docs/TEST-CHECKLIST.md) for per-scenario status and
> [`docs/GAMEPLAY-SYSTEMS.md`](docs/GAMEPLAY-SYSTEMS.md) for what each system does. Touch support is limited to four on-screen buttons (untested on touch hardware); multiplayer is not implemented.
>
> **Known limits (w14-01):** the Skills panel is about 598 px tall plus an 80 px top inset (it was built for 1280x720) and does not fit a 640x480 canvas; menu, inventory and pause actions make no sound (only gameplay events are wired to audio); the boss warning sound was verified only by a synthetic `BOSS_WARN` emit, not in a real boss fight; nobody has heard any of the audio.

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

`npm run preview` serves `dist/` from `127.0.0.1:8777` (run `npm run build` first). Options: `--port <n>` and `--host <addr>`. It serves the built file only, not a development
page that loads each source file separately.

## Commands

Run from `output/offline-rpg/`. There are no dependencies to install.

| Command | What it does |
|---|---|
| `npm run build` | Runs `node scripts/build.mjs`: writes `dist/index.html`, then runs the offline scan on it. |
| `npm run preview` | Runs `node scripts/preview.mjs`: static server with no external requests. |
| `npm test` | Runs `node scripts/build.mjs --verify` (JS_ORDER matches `src/`, and two in-memory builds must be byte-identical), then `npm run test:unit`. |
| `npm run test:unit` | Runs `node --test` over `tests/**/*.test.js` and `tests/scripts/*.test.mjs`, without the build check. |
| `npm run bench` | Runs `node scripts/bench.mjs`: steps the sim and renderer against a counting canvas stub with 320 monsters and prints sim, draw and total ms/frame (mean, p50, p95, max). Flags: `--entities N`, `--frames N`, `--warmup N`, `--seed N`, `--json`. A regression guard, not a device measurement; real-canvas figures are in `docs/VERIFICATION-REPORT.md` §7. |
| `npm run balance` | Runs `node scripts/balance.mjs`: a seeded headless simulation of a scripted player at levels 1-15 in each area on every difficulty (and custom). Prints time-to-kill, damage taken, XP pace and loot rarity tables. Flags: `--quick`, `--levels 1,5,10`, `--seeds N`, `--seconds N`, `--json`. A full run takes several minutes. |
| `npm run check-offline` | Runs `node scripts/check-offline.mjs dist/index.html`: fails on any remote URL, CDN/font host, `@import`, network API or dynamic `import()`. |
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

Progress is saved locally in the browser (`localStorage`) in four slots: `auto`, `1`, `2`, `3`. The game writes `auto`
on area change, level-up, boss defeat, victory and every 60 s of calm play; `F5` writes slot `1` and `F9` loads it.
The Save/Load menu (`M`, or Save/Load from the pause menu) saves, loads, exports (`X`) to a JSON file, imports (`G`) from a file
and deletes (`D`); overwrite and delete need a second `Enter`. Saves carry a schema version and a checksum. Settings (volume, key bindings, difficulty, ...) live under
their own storage key, so deleting a save does not reset them. Format and migration rules are in
[`docs/SAVE-SCHEMA.md`](docs/SAVE-SCHEMA.md). If `localStorage` is unavailable the game keeps saves in memory for the
session and the Export action is the way to keep progress.

**Continue** (main menu) loads the newest readable save by `savedAt`, so an area-entry autosave newer than a manual slot you just loaded wins. **Changing difficulty in Settings** rescales HP, damage and speed of live monsters at once; spawn count, elite rolls and boss modifiers apply from the next area entry. All browser evidence is scripted headless Chrome; there is no real-device, Firefox or manual-play run.

## Controls

Defaults (from `src/input/keymap.js`; all rebindable in Settings, one key per action):

| Action | Keys |
|---|---|
| Move | `W` `A` `S` `D` or arrow keys |
| Aim / basic attack | mouse pointer / left mouse button (or `J`) |
| Skills 1–7 | `1` `2` `3` `4` `5` `Q` `E` |
| Dash | `Space` |
| Switch weapon | `Tab` next, `Shift+Tab` previous, mouse wheel |
| Interact / widen pickup | `F` |
| Inventory / Character / Skills / Settings | `I` / `C` / `K` / `O` |
| Save/Load menu | `M` |
| Quick save / quick load | `F5` / `F9` |
| Pause (closes the top overlay first) | `Esc` or `P` |
| Debug line (fps) | `F3` |

In menus: Up/Down (or `W`/`S`) move, Enter activates, Esc goes back. Inventory: Enter equips, `X` drops, digits `1`–`7`
equip to a slot. Skills screen: Enter learns, Backspace respecs. The full map is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §9.

## Repository layout

```
output/offline-rpg/
  README.md               this file
  docs/                   design and test documents
    ARCHITECTURE.md       module boundaries, game loop, sim pipeline stages, screen registry, API notes
    DATA-DEFINITIONS.md   weapon, skill, monster, item and difficulty data definitions
    GAMEPLAY-SYSTEMS.md   the five gameplay systems and where each is wired
    SAVE-SCHEMA.md        save-state schema and migration rules
    BUILD-OFFLINE.md      build pipeline, file:// strategy and offline-scan rules
    INTEGRATION-PLAN.md   wave-4 wiring plan, with done items marked
    TEST-CHECKLIST.md     8 offline test scenarios and 22 acceptance steps with status
  package.json            npm scripts only, no dependencies
  scripts/                build, preview, offline-scan, run-all-checks and bench scripts
  src/                    game source: core, data, input, sim, world, persistence, render, audio, ui, main.js
  styles/                 stylesheet source
  tests/                  unit suites per area, plus offline-scan fixtures
  dist/                   generated by npm run build (git-ignored)
```

Game state, input, simulation, rendering and persistence are kept in separate modules so a later multiplayer mode
can reuse the simulation. Multiplayer is **not** implemented.

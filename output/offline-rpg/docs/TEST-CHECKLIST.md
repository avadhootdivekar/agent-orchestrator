# Offline RPG — test checklist

Source: prompt §24 (offline validation scenarios) and §28 (acceptance steps).

Every row starts as **not tested**. Update the status column only with evidence: a test name that passes, a log, or a
recorded manual walkthrough. Allowed statuses:

- `not tested` — no evidence yet (the starting state).
- `automated pass` — covered by a named test that passes (record the name in Evidence).
- `manual pass` — walked through by a person and recorded (record the date and browser in Evidence).
- `fail` — tried and did not work (record the observed behaviour in Evidence).
- `not applicable` — deliberately out of scope (state why in Evidence).

Only the offline-scan, build and test-suite rows in section C have evidence so far (wave 2 verify, 2026-10-16). All
gameplay and save rows stay `not tested`.

## A. Offline scenarios (prompt §24)

| # | Scenario | Procedure | Status | Evidence |
|---|---|---|---|---|
| S1 | Normal online browser | Open the built `dist/index.html` with network available; start a new game and play for a few minutes. | not tested | |
| S2 | Browser with network disabled | Disable the network (DevTools offline mode or airplane mode); reload `dist/index.html`; play. The Network tab should show zero requests. | not tested | |
| S3 | Local static HTTP server with network disabled | Run a static server over `dist/` (`npm run preview` or `python3 -m http.server -d dist`); disable the network; reload; play. | not tested | |
| S4 | Refresh / reload during gameplay | Mid-fight, reload the page; the game resumes from the last save point with the expected state. | not tested | |
| S5 | Save → reload → restore | Save the game, reload, load the save; state (level, XP, inventory, equipment, difficulty, seed, position) matches. | not tested | |
| S6 | Export save → delete save → import save | Export to `game-save.json`; delete the local save; import the file; state matches the exported save. | not tested | |
| S7 | New game after an existing save | Start New Game while a save exists; a confirm dialog appears; the existing save is untouched until overwritten. | not tested | |
| S8 | Corrupt / invalid save handling | Edit, truncate or mis-version a save (bad JSON, wrong `format`, newer `version`); the game shows a friendly error and still starts; no crash. | not tested | |

## B. Acceptance steps (prompt §28)

| # | Step | Verified by | Status | Evidence |
|---|---|---|---|---|
| 1 | Open the game locally | Load `dist/index.html` (or the preview server) and see the title screen. | not tested | |
| 2 | Start a new game | New Game creates a character in the starting area. | not tested | |
| 3 | Move around a 2D world | WASD / arrow keys move the player over the tile map with collision. | not tested | |
| 4 | Fight monsters | Basic attack damages and kills at least one monster type. | not tested | |
| 5 | Use different weapons | Switching weapons (Tab / Shift+Tab / wheel / R) changes the attack behaviour. | not tested | |
| 6 | Use offensive, defensive and regenerative skills | One skill from each category can be triggered from hotkeys 1–5 and goes on cooldown. | not tested | |
| 7 | Gain XP | Killing monsters increases XP shown in the HUD. | not tested | |
| 8 | Level up | Enough XP raises the level and shows a level-up effect. | not tested | |
| 9 | Spend skill points | Level-up grants skill points; the skills screen (`K`) spends them. | not tested | |
| 10 | Obtain and equip loot | A monster drops an item; it can be picked up, equipped and changes stats. | not tested | |
| 11 | Fight increasingly difficult enemies | Enemies in later areas or at higher levels have higher stats and more elites. | not tested | |
| 12 | Defeat at least one boss | The boss in its arena can be defeated and drops better loot. | not tested | |
| 13 | Change difficulty | Difficulty can be changed at runtime in Settings and the change is visible in stats or spawns. | not tested | |
| 14 | Save the game | Save writes the current state to local storage. | not tested | |
| 15 | Close the browser | Close the browser window completely. | not tested | |
| 16 | Reopen the game with no internet | Reopen with the network disabled; the game starts. | not tested | |
| 17 | Load the previous game | The saved game loads from the title or save menu. | not tested | |
| 18 | Continue playing exactly where the player left off | Position, HP, inventory, equipment and boss progress match the save. | not tested | |
| 19 | Export the save | Export downloads a human-readable `game-save.json`. | not tested | |
| 20 | Delete the local save | Delete removes the save from local storage after confirmation. | not tested | |
| 21 | Import the exported save | Import of the exported file restores the save. | not tested | |
| 22 | Continue playing from the restored state | After import, the game continues from the restored state without errors. | not tested | |

## C. Offline-scan and build checks

These are not in the prompt's numbered list but are required by the offline acceptance criteria (A6.1, A6.3, A9.3).

| # | Check | Command | Status | Evidence |
|---|---|---|---|---|
| C1 | Offline scan finds no remote URL, network API, module or telemetry reference | `npm run check-offline` | automated pass | `npm run check-offline` exit 0 on `dist/index.html`. The scan's own unit tests pass (`tests/scripts/check-offline.test.mjs`, run with `node --test`). |
| C2 | Build produces a single self-contained `dist/index.html` | `npm run build` | automated pass | `npm run build` exit 0, "built dist/index.html (172305 bytes)"; the build runs the offline guard on its own output. `npm test` also runs `build.mjs --verify` (two in-memory builds byte-identical). |
| C3 | Test suite passes from a clean checkout | `npm test` | automated pass | Run on the working tree, **not a clean checkout** (uncommitted wave-2 changes present; a clean-checkout run is not done). `npm test` exit 0: `# tests 241`, `# pass 241`, `# fail 0`. This runs `build.mjs --verify` and then `node --test 'tests/**/*.test.js' 'tests/scripts/*.test.mjs'`. |

## Summary

| Area | Total | Not tested | Passed | Failed | Not applicable |
|---|---|---|---|---|---|
| Offline scenarios (S1–S8) | 8 | 8 | 0 | 0 | 0 |
| Acceptance steps (1–22) | 22 | 22 | 0 | 0 | 0 |
| Offline-scan, build and tests (C1–C3) | 3 | 0 | 3 | 0 | 0 |

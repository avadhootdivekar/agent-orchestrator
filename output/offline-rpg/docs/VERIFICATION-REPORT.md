# Offline RPG — verification report

Date of the runs below: 2026-10-16 (waves 7 to 10). Machine: Linux 6.1, Node 22. Working tree of branch
`ad/model-refresh-haiku-5-5` with **uncommitted changes**, not a clean checkout. Sections 1 and 2 were first written in wave 7
(894 tests; 972 after w14-01); section 6 was rewritten in w09-02 and extended in w10-05 (w09 verify defects and their wave-10 fixes, new §6.3) and w11-03 (wave-11 fixes, new §6.4; test count 959).

**Read this first.** Every step of prompt §28 has automated evidence. **Nobody has played the game by hand:
there are 0 manual passes.** Evidence comes from (a) headless tests (`node --test` in a `vm` realm with stubs for the DOM,
canvas and `localStorage`) and (b) scripted real-browser runs (w07-04 and w08-06: headless Chrome 138 driven over CDP with real
key/mouse events, not a person, on a software canvas). Nothing was run on a real GPU, on Android or macOS, or on a touch device.
The w07-05 finding that the effects layer was not wired is **fixed** (§6); what the effects look like to a person has not been judged.

## 1. Commands and current results

Run from `output/offline-rpg/`, verbatim tail output:

| Command | Result |
|---|---|
| `npm test` | `# tests 972`, `# pass 972`, `# fail 0` (re-run in w14-01 on the wave-13 tree; was 963 in w12-01, 959 in w11-03, was 952 in w10-05, 937 in w09, 894 in wave 7; runs `build.mjs --verify` first, then `node --test`) |
| `npm run build` | `built dist/index.html (536843 bytes)` in w14-01 (the printed figure); it was 534430 in w12-01, 529700 in w10-05, 523135 in w08-06 and 524871 in w09, so it changes with the tree |
| `npm run check-offline` | in w14-01: prints only the npm header lines, exit code 0 (no remote URL, CDN/font host, `@import`, network API or dynamic `import()` in `dist/index.html`) |
| `npm run check-all` | `PASS build 0.2s`, `PASS test 19.1s`, `PASS check-offline 0.1s`, `all 3 checks passed` |
| `npm run bench` | `bench: 600 frames, 320 monsters ...`; re-run in w10-05: sim mean 1.891 ms, p95 2.186; draw mean 1.094 ms, p95 1.177 (7736 canvas calls); total mean 2.985, p95 3.326, max 3.952; `budget 16 ms p95: PASS` |
| `node scripts/balance.mjs --quick` | see §4 |

The bench draws against a counting canvas stub (its last frame had 40 damage numbers and 41 effects live), so it is a regression guard, not a device measurement. Real-canvas numbers are in §7.

## 2. Prompt §28 acceptance steps

Status key: **browser-scripted** = also exercised in real headless Chrome by w07-04 (scripted); **headless** = only in
`node --test` with stubs; **untested** = no evidence of that part. Test file names are under `tests/`.

| # | Step | Evidence | Status |
|---|---|---|---|
| 1 | Open the game locally | `integration/acceptance-28.test.js` "step 01"; w06-07 and w07-04 loaded `dist/index.html` as `file://` and via `http://127.0.0.1` in headless Chrome 138: title screen, 0 exceptions, 0 console errors, 0 non-local requests (`w07-04-evidence/log.json`, `01-title.png`) | browser-scripted |
| 2 | Start a new game | "step 02"; `scripts/boot.test.js`; `core/game-wiring.test.js`; Chrome: Enter, Enter reaches `playing` in `town` (208, 368) | browser-scripted |
| 3 | Move around a 2D world | "step 03"; Chrome: WASD holds move ~72-76 px per 500 ms | browser-scripted |
| 4 | Fight monsters | "step 04"; `integration/headless.test.js` 60 s run; Chrome: mouse attacks killed 5 monsters in the forest | browser-scripted |
| 5 | Use different weapons | "step 05" (all three weapons via the real `switchWeapon` intent) | headless only; weapon switching never tried in Chrome; the `R` key is not bound |
| 6 | Offensive, defensive, regenerative skills | "step 06" (fireball, shield, mend, with points earned from real kills) | headless; Chrome cast only fireball (resource 63 -> 52.9); defensive/regenerative untested in a browser |
| 7 | Gain XP | "step 07"; Chrome: XP 0 -> 96 (state read, HUD not read) | browser-scripted |
| 8 | Level up | "step 08"; Chrome: level 1 -> 2 | browser-scripted; the on-screen level-up effect was not judged |
| 9 | Spend skill points | "step 09"; `ui/skills-screen.test.js`; Chrome: `K`, Enter learned fireball (1 point -> 0) | browser-scripted |
| 10 | Obtain and equip loot | "step 10"; `ui/inventory.test.js` "equip changes player stats and unequip restores them" | headless only; no pickup or equip in a browser |
| 11 | Increasingly difficult enemies | "step 11" (monster level and HP rise forest -> caves -> arena) | headless only; not judged by eye |
| 12 | Defeat a boss | "step 12"; `integration/headless.test.js` boss fight with a god-mode player; `sim/boss-combat.test.js` | headless only; telegraphs now draw (§7) but the boss was not defeated in a browser and no person fought it |
| 13 | Change difficulty | "step 13"; `core/game-wiring.test.js`; `ui/settings-screen.test.js` | headless only |
| 14 | Save the game | "step 14"; `integration/save-roundtrip.test.js`; Chrome: `F5` wrote `offline-rpg:v1:slot:1` (~2 KB) | browser-scripted |
| 15 | Close the browser | "step 15" (realm dropped, storage survives); Chrome: `Browser.close`, new process on the same profile, both slots still in `localStorage` | browser-scripted (graceful close; SIGTERM lost the data in an early try) |
| 16 | Reopen with no internet | "step 16"; Chrome: relaunched with the network emulated off before the first navigation, boots, 0 failed requests | browser-scripted |
| 17 | Load the previous game | "step 17"; `save-roundtrip.test.js`; Chrome: Continue loaded the save after reload and after a full close | browser-scripted |
| 18 | Continue exactly where they left off | "step 18" (exact comparison); `persistence/resume-exact.test.js` (3 tests: motion, `vel`, cleared slots); Chrome: position, level, XP, kills, inventory count match | browser-scripted; equipment not compared; `world.time` restarts near 0 (48.9 -> 0.95), live monsters of uncleared slots and attack swing in progress are not saved (SAVE-SCHEMA) |
| 19 | Export the save | "step 19"; `save-roundtrip.test.js`; `persistence/files.test.js` (fakes) | headless only; the real download and file picker were never driven in a browser |
| 20 | Delete the local save | "step 20"; `core/game-wiring.test.js` | headless only; the second-`Enter` confirm is not covered |
| 21 | Import the exported save | "step 21"; `save-roundtrip.test.js` "export -> delete -> import -> continue" | headless only |
| 22 | Continue from the restored state | "step 22"; same round trip test | headless only |

Summary: 22 of 22 have automated evidence; 12 (1-4, 7-9, 14-18) also have scripted real-browser evidence; 10 are headless only
(5, 6 in part, 10-13, 19-22); **0 are manually verified**. Offline scenarios S3 (local server with the network disabled) and S7
(new game over an existing save, confirm dialog) are `not tested`; see `TEST-CHECKLIST.md`.

## 3. Offline evidence

- Static: `npm run check-offline` passes; the page carries a CSP with `connect-src 'none'`. A probe `fetch('https://example.com')` in Chrome was refused by it.
- Dynamic (w07-04): `file://` run, network emulated off and a full browser close/reopen: only request was the page itself, 0 failed, 0 game console errors.
- Not covered: a person disconnecting the network, other browsers, a localhost server with the network disabled (S3).

## 4. Balance headline (`node scripts/balance.mjs --quick`, seeded, scripted bot)

Per-difficulty mean over areas and levels:

| difficulty | TTK s | dmg %HP | deaths | XP/min |
|---|---|---|---|---|
| easy | 8.24 | 45.65 | 0.08 | 432.4 |
| normal | 4.90 | 82.98 | 0.17 | 559.3 |
| hard | 2.02 | 98.90 | 0.17 | 874.7 |
| nightmare | 2.44 | 108.73 | 0.42 | 738.9 |
| custom | 16.28 | 91.31 | 0.42 | 809.2 |

By area: forest TTK 1.06 s, caves 1.62 s, arena 23.46 s (dmg %HP 59.96 / 89.70 / 106.87).
Gate route (normal, seed 1, every monster killed once): forest->caves gate L4 (903 XP) opens after clearing 58% of the forest
(area supplies 1557 XP); caves->arena gate L6 (2821 XP) opens after 48% of the caves (cumulative 4180 XP).
Caveat: the quick run is one seed with a scripted bot, not a human; only nightmare's mean damage exceeds 100% of max HP
(108.73%; hard is 98.90%, just under), the arena's mean damage is also above it (106.87%), and the arena's ~23.5 s TTK looks unbalanced; none of this has been tuned by playing.

## 5. Changes since earlier docs (stale counts refreshed)

`npm test` was 874 tests with 2 failing in wave 6; it is now 972 passing (963 after wave 12; 894 in wave 7, 937 after wave 9, 952 after wave 10, 959 after wave 11). The w07-01 XP rebalance fixed the progression gate, w07-02 made
cleared slots stay cleared and saved velocity, and w07-03 added on-canvas touch/click buttons (I, S, C, pause). Wave 8 wired the effects layer, added elite-affix sim effects, the swing arc and death animation, skill auto-slotting and stabilised flaky tests (933 tests); w09-01 added the effect cap, the hit-flash rest and the explosive-death cue (937 tests). Wave 10 fixed the defects found by the w09 verify units (camera, hotbar labels, clipped overlays, load-overwrites-autosave, runtime difficulty rescale): 952 tests. Wave 11 fixed the hotbar cooldown display and made `enemySpeed` change movement (§6.4): 959 tests.

## 6. Defects and known limitations (current state, after waves 8-9)

### 6.1 w07-05 review findings, re-checked

The w07-05 charter review (verdict FAIL) listed D1-D9. w08-01 to w08-05 fixed them; w08-07 re-reviewed the code (read-only; `npm test` 933/933 then) and w08-06 measured the result in headless Chrome. Status now:

| # | Finding | Status | Evidence |
|---|---|---|---|
| D1 | `renderer.js` called a missing `drawEffects` | **Fixed** | `drawEffects(ctx, world, alpha, pal, layer)` exists in `render/draw-effects.js` and is called for the `below` and `above` layers; `tests/render/effects-wiring.test.js` uses the real modules. w08-06 real-canvas control renders: boss telegraph adds 1 fill, 1 stroke, 2 arc and 4.8k-13.4k pixels. |
| D2 | damage numbers passed the wrong argument | **Fixed** | `drawDamageNumbers(ctx, world.fx.damageNumbers)`, gated by `settings.showDamageNumbers`. w08-06: +1 `fillText`, 88-106 px. |
| D3 | particles and hurt flash had no callers | **Fixed** | `stepWorldFx` runs from the sim, `drawHurtFlash` from the renderer; particles are capped by `particleBudget(quality)`. w08-06: 3 live particles add 3 fill, 3 arc, 18-40 px. |
| D4 | telegraphs invisible | **Fixed** | Monster telegraphs, boss warnings and ember zones draw below the entities. w08-06 saw `warning:circle` on `warden-of-ash`. |
| D5 | elite affixes cosmetic | **Fixed** | Five affixes now have sim effects (stage `affixes`, GAMEPLAY-SYSTEMS "Monsters"). `tests/sim/elite-affixes.test.js`, 13 tests. |
| D6 | no swing / death animation | **Fixed in code and tests** | `drawSwing` and `drawDying` (GAMEPLAY-SYSTEMS "Combat animations"). `tests/render/combat-anims.test.js`, 7 tests. **The swing and death animations were never pixel-checked on their own in a real browser** (w08-06 did not isolate them), so how they look is unverified. |
| D7 | bench understated draw cost | **Partly fixed** | Real-canvas figures in §7 and TEST-CHECKLIST D2. Software GL in headless Chrome, not a device. |
| D8 | unused exports | **Fixed** | `wallTop`/`wallEdge` were in use (w07-05 was wrong there); `audio.SOUND_NAMES` and `skillEffects.supports` were removed in w09-01. |
| D9 | hit-flash strobing | **Fixed in code (w09-01)** | A sprite must stay un-flashed for 0.25 s after a flash (`FLASH_REST`), at most about 2.7 Hz. Covered by a sim-level test in `tests/sim/effects-hardening.test.js`; not judged by eye or in a browser. |

Also fixed in w09-01 and covered only by sim-level tests: `world.effects` is capped at 64 entries (w08-06 had seen it grow to 32 over ~100 swings without checking), the explosive-death affix spawns a visible `blast` nova ring and a `hit` sound, and a boss `damageTakenMult` between 0 and 0.1 is raised to 0.1 (exactly 0 stays deliberate immunity). The blast ring has no real-canvas check.

### 6.3 Defects found by the w09 verify units, and their wave-10 fixes

w09-03 (play), w09-04 (world) and w09-05 (save/offline) ran in headless Chrome 138 or the headless harness on temp copies and fixed nothing; w10 fixed them. "Fixed" below means fixed in code with a regression test that fails on the old code where noted. **None was re-checked in a real browser after the fix** (no wave-10 browser run exists), so the visual results are inferred from tests on recording contexts, not from pixels.

| Defect (found in) | Status | Fix and test |
|---|---|---|
| **Blocker: the camera never follows the player.** `game.render` passed `dt = 0` to `renderer.draw`, so `camera.follow` (k = 1 - exp(-10 dt)) never moved; at 1280x661 the player walked off screen (w09-03) | **Fixed in code (w10-01)** | `game.js` `frameDt()` passes the real frame time from the injected clock, capped at 0.1 s; `snapCamera` also runs on new game, load, respawn and every `AREA_CHANGED`. `tests/core/game-camera.test.js`, 4 tests; tests 1-3 fail on the old `game.js`. Depends on `deps.clock` (no clock means no follow); production wiring supplies one. |
| Hotbar slots 1-4 show only digits, no skill name (w09-03) | **Fixed in code (w10-02)** | `render/hud.js` slot model carries `name` (display name, or id, cut to 6 characters in w10-02; widened to 9 in w11-01, see §6.4) and `drawSlot` draws it in a 10px font above the existing cooldown seconds and sweep. `tests/render/hud.test.js` (name, cooldown seconds, display name from data, empty slots blank). Icons were not added. |
| Skills screen and Save/Load screen clipped at the left edge: first characters missing, e.g. "reball", "ave: empty" (w09-03, w09-05) | **Fixed in code (w10-02)** | Cause: toasts and banners in `hud-overlays` set `textAlign='center'` and never restored it, and the overlays inherited it. `ui/screens.js` now wraps each screen in `save`/`restore` with `left`/`alphabetic`, and each of the 7 overlays sets them itself. `tests/ui/text-align.test.js`, 3 tests (starts from `right`/`bottom` and checks every `fillText`; Skills x positions inside 1280x661); removing the alignment from `skills-screen` makes it fail. Checked on a recording context, not by pixels. |
| Loading a save overwrote slot `auto` with the pre-load state, so Continue after a reload resumed the abandoned state (w09-05) | **Fixed in code (w10-03)** | `game.js` `loading` counter; `autosave()` returns null while `loadFromSlot` runs. Regression test in `tests/core/game.test.js` (fails with the guard removed; asserts `auto` is byte-identical after a load, and that a later `AREA_CHANGED` autosaves the loaded state). |
| Changing difficulty mid-area did not affect monsters already spawned (w09-04) | **Fixed in code (w10-04)** | `areas.rescaleMonsters` multiplies live scaled monsters by new/old factor (HP with fraction kept, shield, damage, speed, XP/loot/gold) and `game.setDifficulty` calls it. 4 tests in `tests/world/areas.test.js` (equals a fresh spawn at the new difficulty and round-trips; same difficulty is a no-op; summons untouched) and 1 in `tests/core/game-wiring.test.js` (a Settings change reaches live monsters). Spawn count, elite rolls and boss modifiers still apply only from the next area entry. Stored `m.speed` is rescaled, but movement used the def's base speed, so `enemySpeed` had no real effect on movement (fixed in w11-02, §6.4). |
| Corrupt only slot: Continue does nothing and shows no message (w09-05, same as w07-04 follow-up) | **Open** | Not changed in wave 10. |
| Hard-difficulty level-2 boss fight lethal for a weak melee bot (w09-04) | **Open, may be intended** | No change. |

Doc mismatches found by w09-06 (no code defects): the README listed `npm run bench` twice, the SAVE-SCHEMA save-size estimate (~10-40 KB) was far above the observed ~2 KB, and §1 quoted a stale build size. All three are corrected in w10-05 (README one bench row; SAVE-SCHEMA a few KB, ~6 KB for a full 24-slot inventory by arithmetic from the sample, not measured; §1 above).

### 6.2 What is still not verified

- **No manual play.** 0 manual passes in any document. Nobody has judged feel, balance, readability of telegraphs, the look of the effects, or the sound.
- **No real device.** All browser evidence is headless Chrome 138 with software GL, vsync-bound at 16.67 ms. No GPU frame rate, no Android, macOS or Windows, no Firefox, Safari or other browser.
- **No touch hardware.** Only four canvas buttons exist (I, S, C, pause; w07-03), tested headless. There is no virtual stick. The touch HUD was not exercised in the real-browser runs.
- **Boss defeat in a browser** was not done; w08-06 reduced the boss from 1800 to about 1450 HP with HP and position shortcuts (disclosed in TEST-CHECKLIST D). Defeat is covered headless only.
- **Audio** is verified only for the no-`AudioContext` path and by unit tests; nobody heard it. The bridge is attached in `game.create` (guarded, detached by `destroy`, survives `newGame`/load; ARCHITECTURE §audio). Menu, inventory and pause actions are silent (they emit no events). The boss warning sound was verified by a synthetic `BOSS_WARN` emit only, never in a real boss fight.
- **Skills panel is taller than 640x480**: 598 px of panel plus the 80 px inset (678 px) fits 1280x720 only; at 640x480 the bottom (detail text and skill bar) is off-canvas. Not fixed.
- **Balance** (§4) is a seeded scripted bot, not a human; only nightmare's mean damage exceeds 100% of max HP (the arena's does too) and the arena's ~23.5 s time-to-kill have not been retuned by play.
- Scenarios S3 and S7 are `not tested`; weapon switching, the defensive and regenerative skills, loot pickup/equip, difficulty change and the file export/import were never driven in a browser.
- **The wave-10 fixes (§6.3) were not re-run in any browser.** Camera follow in particular has only recording-context unit tests behind it: nobody has watched the view scroll.
- w07-04 followups still open unless fixed elsewhere: a corrupt-JSON save shows "No save to continue." and not an error message; `world.time` resets on load. Skill auto-slotting on learn was added in w08-04 (headless-tested only).
- The offline scan does not check ES-module syntax or `eval` (BUILD-OFFLINE §4).
- Multiplayer is not implemented. Weapon behaviours `thrust`, `smash`, `beam`, `arc` and `explosive` have no code.
- **This work is uncommitted** (in w10-05 `git status` still shows modified and untracked files under `output/offline-rpg`) on `ad/model-refresh-haiku-5-5`; it still needs a commit and a PR to `main`.

### 6.4 Wave-11 fixes (w11-01, w11-02, w11-03)

| Defect | Status | Evidence |
|---|---|---|
| Hotbar cooldown sweep and seconds always showed 0 after a cast: `render/hud.js` read `player.skills.cooldowns`, the sim writes `player.cooldowns['skill:<id>']` (w11-01) | **Fixed in code** | `hud.js` reads the sim value (legacy map as fallback) and sizes the sweep by `base * (1 - cooldownReduction)`; slot 44 -> 56 px and name cut 6 -> 9 characters ("Fireball", "Whirlwind" whole; "Blink Strike" still cut). `tests/integration/hotbar-cooldowns.test.js`, 3 tests driven by a real cast through `game.tick`. The tests feed the HUD the base-cooldown map by hand, not through the real `hudInputs`. Slot sizing was checked only by string length, not by pixels. |
| `enemySpeed` did not change monster movement: `world/ai.js` uses the def's base speed and `sim/monsters.js` ignored `m.speed` (w11-02) | **Fixed in code** | `monstersStage` scales the AI velocity by `m.speed / def.baseStats.speed` (1 for unscaled monsters such as summons); a runtime difficulty change reaches live monsters through `rescaleMonsters`. `tests/sim/enemy-speed.test.js`, 4 tests (2 fail with the fix reverted). Attack cooldowns, windups and telegraphs are deliberately **not** scaled. |

Behaviour to know (documented in GAMEPLAY-SYSTEMS "Difficulty" and "Continue"):

- **Continue loads the newest save by `savedAt`** (`ui/mainmenu.js` `latestSave`, corrupt slots skipped). An area-entry autosave newer than a manual slot you just loaded therefore wins, so Continue after loading an older manual slot can resume the autosave. Loading itself does not overwrite `auto` (w10-03).
- **Runtime difficulty change:** HP, damage and speed of live monsters rescale at once. Spawn count, elite rolls and boss modifiers apply from the **next area entry**.

Evidence limits, stated plainly: all browser evidence in this report is **scripted headless Chrome 138 (software canvas, driven over CDP)**; there is **no real-device run, no Firefox/Safari run, and no manual play**. The wave-11 fixes were checked by unit/integration tests only and were **not re-run in any browser**. `npm test` passed 972 of 972 in w14-01 (963 of 963 in w12-01, 959 of 959 in w11-03) on an uncommitted working tree.

## 7. Real-render check (w08-06, 2026-10-16)

Headless Chrome 138 over CDP on a clean temp build (523135 bytes), `file://`, scripted. Results are in TEST-CHECKLIST §D (D1-D3). In short: the boss telegraph, damage numbers and particles now draw on the real canvas (control renders differ by draw calls and by 29 to 13351 pixels, a self-render differs by 0), contradicting the w07-05 D1-D4 findings in §6 for this build; rAF interval 16.67 ms mean / 16.7 p95 with effects live, `render()` 0.054 ms mean live and 1.09 ms mean under a 40-number / 300-particle / 6-telegraph stress; with `AudioContext` removed the audio module reports `available:false` and play, boss fight and movement are unaffected. Setup shortcut: the arena was entered via the game's area API with HP raised, the fight itself was real. Still unchecked: a human looking at it, touch, GPU/device frame rate, defeating the boss, the swing animation in isolation. The old w07-05 findings D1-D4 are resolved; §6 records their status.

### 6.5 Inventory and character layout (w12-01)

w11-05 saw no equipment slots in the inventory and the HUD HP line covered. Cause: `ui/inventory.js` never drew equipment, and the inventory and character panels were drawn at (0,0), over the HUD bars (y 16..74). Now the inventory panel draws a slot block per `RPG.data.itemSlots` under the bag grid (click a filled slot to unequip), the character sheet is two columns, and both panels start `HUD_INSET` = 80 px down. Tests: 3 in `tests/ui/inventory.test.js`, 1 in `tests/ui/character.test.js`, and the two pointer tests in `tests/core/game-pointer.test.js` were shifted by the inset. Checked on recording contexts at 640x480 and 1280x720 geometry; **not re-checked in a browser**. The skills screen was not changed in w12-01; it has since been moved below the HUD with the same `HUD_INSET` (80 px) and, like the inventory, names in item cells now wrap (w13; unit-tested on recording contexts, not re-checked in a browser).

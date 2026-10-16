# Offline RPG — Architecture (v1 design contract)

Status: design contract, reconciled with the code at the end of wave 5. Plain JavaScript (ES2019, no TypeScript, no bundler
dependency). Where a signature in §4 differs from the source, **the source and the header comment of its file win**;
the known differences are listed in [`INTEGRATION-PLAN.md`](INTEGRATION-PLAN.md) §1.

**Implementation status.** Everything in `src/` listed in §2 exists and is wired into one playable page: `core/game.js` owns the
state machine and the fixed-step loop; `sim/sim.js` runs the stage pipeline (§3.1) over weapons, skills, AI, bosses, loot, rewards
and areas; `render/` draws the world and HUD; `ui/` holds the canvas menu screens (§3.2); `persistence/` saves, loads, exports and
imports; `audio/` plays Web Audio effects from sim events. 972 unit and headless integration tests exist (`npm test`; all passed on 2026-10-16 in w14-01, see VERIFICATION-REPORT.md). Not implemented:
a virtual-stick touch input (only four on-canvas buttons exist), weapon behaviours `thrust`/`smash`/`beam`/`arc`/`explosive`, and multiplayer. No manual browser walkthrough is recorded
([`TEST-CHECKLIST.md`](TEST-CHECKLIST.md)). Gameplay systems: [`GAMEPLAY-SYSTEMS.md`](GAMEPLAY-SYSTEMS.md).

**Why plain JS:** the build is a zero-dependency node script (see BUILD-OFFLINE.md); TypeScript would force a
compiler dependency and an `npm install` that needs the network. JSDoc typedefs in each file give editor hints.

## 1. Principles

1. **Separation (prompt §19/§20):** State (`world`) is plain data. Simulation mutates state only through
   `step(world, inputFrame, dt)`. Input produces an `InputFrame`. Rendering reads state, never mutates it.
   Persistence serializes a subset of state. No module in `render/` or `input/` may be imported by `core/`, `world/`
   or `persistence/` logic. Enforced by load order only; there is no lint check.
2. **Determinism:** all gameplay randomness goes through a seeded `Rng` (mulberry32) stored in `world.rng`.
   Rendering/particles use a separate cosmetic `Rng` (`world.fx.rng`) so visuals never perturb gameplay replay.
3. **Fixed timestep:** simulation runs at 60 Hz (`DT = 1/60`), accumulator capped at 5 steps/frame; render
   interpolates with `alpha`.
4. **Data-driven:** content lives in `src/data/*.js` as frozen plain objects registered into `RPG.data`.
   Adding a monster/weapon/skill/item = adding an entry; behaviours are referenced by string key
   (`ai: 'melee'`, `attack: 'projectile'`), implemented once in systems.
5. **Classic scripts, one global namespace `RPG`:** every file is an IIFE `(function(RPG){ ... })(window.RPG = window.RPG || {});`
   attaching exports to `RPG.<ns>`. No `import`/`export`, no `fetch`, no `type="module"` → works on `file://`.
6. **No DOM in hot paths:** gameplay is on one `<canvas>`; HUD is drawn on the canvas, menu screens
   (main menu, pause, inventory, skills, …) are also drawn on that canvas by the screen manager (`ui/screens.js`); there is no DOM UI.

## 2. Directory layout

Layout (every file listed exists):

```
output/offline-rpg/
  package.json              scripts: build, preview, test, test:unit, check-offline, check-all, bench (no dependencies)
  README.md
  index.html                template with <!--@CSS--> and <!--@JS--> markers
  scripts/
    build.mjs               single-file build -> dist/index.html; JS_ORDER lists the load order; --verify
    preview.mjs             static server for dist/ (127.0.0.1:8777)
    check-offline.mjs       offline guard (see BUILD-OFFLINE.md §4)
    run-all-checks.mjs      build, test and check-offline in order
    bench.mjs               headless sim + draw timing
  styles/main.css           page layout; system fonts only
  src/
    data/                   weapons skills monsters items difficulties areas (frozen definitions), maps/test-map.js
    core/                   namespace util rng events pool entities state loop game (state machine, autosave, wiring)
    input/                  raw (key/pointer state) keymap (actions, defaults) intent (-> InputFrame) input (DOM wiring)
    world/                  stats tilemap collision spatial mapgen world combat weapons skills skill-effects ai boss loot
                            equipment progression difficulty encounters areas   (rules; mutate the world they are handed)
    sim/                    player combat-step monsters rewards sim   (the stage pipeline, §3.1)
    persistence/            save migrate storage exportimport snapshot (world <-> save body) files (Blob download, file picker)
    audio/                  audio (Web Audio synthesis) bridge (sim events -> sounds)
    render/                 renderer camera palette hud draw-shapes draw-entities draw-map draw-effects
    ui/                     screens (manager) mainmenu pause saveload settings inventory character skills-screen hud-overlays
    main.js                 entry: finds the canvas, passes localStorage for the settings key, calls RPG.core.game.start
  tests/
    audio core data input integration persistence render scripts sim ui world    one folder per area
    <area>/load.js          some areas: loads the area's src files into a vm context
    *.test.js  *.test.mjs   run with npm run test:unit (node --test)
    fixtures/offline/       inputs for the offline guard
  docs/                     design and test documents
  dist/                     build output (git-ignored): index.html
```

### Load order (authoritative copy: `JS_ORDER` in `scripts/build.mjs`)

`core/namespace → core/util → core/rng → core/events → core/pool → core/entities → core/state → data/* (maps/test-map first) →
world/* (stats … areas) → sim/player → sim/combat-step → sim/monsters → sim/rewards → sim/sim → persistence/* → input/* →
audio/* → render/* → ui/* → core/loop → core/game → main`. The exact list is `JS_ORDER`; `npm test` fails if a file in `src/` is
missing from it, listed twice, or listed but absent.

Every module may call only modules earlier in the list at *load* time; at *run* time, calls through `RPG.<ns>`
resolve lazily, so cross-references between peers in the same layer are fine.

**Headless rule:** everything in `core/`, `data/`, `world/`, `sim/`, `persistence/` and `input/raw.js`, `input/intent.js`,
`input/keymap.js` MUST run in plain node (no `window`, `document`, `localStorage` at load time) so the area tests can load
them in a `vm` context with only `{RPG: {}}`-style globals. `namespace.js` uses `globalThis`.

## 3. Game loop (core/loop.js)

```js
RPG.core.createLoop({ step, render, shouldStep, dt = 1/60, maxSteps = 5 }) -> { start(), stop(), reset(), running }
// per rAF(ts): acc += min(ts-last, 0.25); n=0; while (shouldStep() && acc >= dt && n++ < maxSteps) { step(dt); acc -= dt; }
//              render(acc/dt)   // alpha in [0,1)
```
`core/game.js` supplies `step` (`game.tick`), `render` (`game.render`) and `shouldStep` (state is `playing`, a world exists and no
modal screen is open). While a screen is open the sim does not advance but `render` still reads input so `Esc` can resume.
`visibilitychange` → auto-pause (if playing) and `loop.reset()`.

### 3.1 Sim pipeline (`sim/sim.js`, `RPG.sim.step(world, intent, dt)`)

`step` runs `RPG.sim.ORDER` in this fixed order, calling `RPG.sim.stage.<name>(world, ctx)` with `ctx = world.scratch`. A name with
no registered function is skipped. Registered stages and their files:

| # | Stage | Registered by | Does |
|---|---|---|---|
| 0 | `begin` | `sim/sim.js` | clears per-tick scratch (events, hits, killed) |
| 1 | `input` | *(nothing registered; skipped)* | |
| 2 | `player` | `sim/player.js` | sanitises the intent, dash and i-frames, regen, movement, weapon switch, basic attack and skill casts. Stages 3 and 4 below are done inside it through `world/weapons` and `world/skills` |
| 3 | `weapons` | *(not registered; done in `player`)* | |
| 4 | `skills` | *(not registered; done in `player`)* | |
| 5 | `spatial` | `sim/monsters.js` | rebuilds the spatial grid |
| 6 | `monsters` | `sim/monsters.js` | `world/ai` → telegraphs, melee, enemy projectiles, summons, exploders; bosses via `world/boss` |
| 7 | `movement` | `sim/monsters.js` | integrates monster velocity through the tilemap, knockback, pack separation |
| 8 | `projectiles` | `sim/combat-step.js` | player and enemy projectiles, effects, ground zones |
| 9 | `statuses` | `sim/combat-step.js` | status ticks (burn, poison, slow, …) |
| 9b | `affixes` | `sim/monsters.js` | elite affix effects: shield, regeneration, leech and the explosive-death blast (data: `RPG.data.eliteAffixes`) |
| 10 | `resolve` | `sim/combat-step.js` | hits become HIT events; `RPG.sim.hurtPlayer` is the single entry for player damage |
| 11 | `rewards` | `sim/rewards.js` | kill XP, gold, drops, level-ups, boss bookkeeping |
| 12 | `pickups` | `sim/rewards.js` | walk-over pickup (gold, items; `F` widens the radius) |
| 13 | `playerDeath` | `sim/rewards.js` | death penalty (gold %), `progress.deaths` |
| 14 | `cleanup` | `sim/sim.js` | ages drops and damage numbers, removes dead entities |
| 15 | `areas` | `sim/sim.js` | `world/areas.checkExits`: portal collision enters the next unlocked area |
| 16 | `end` | `sim/sim.js` | `tick++`, `time += dt`, `progress.playtime`, shake decay |

`RPG.sim.createWorld({seed, areaId?, mapId?, difficulty?})` builds the world, grants the three starting weapons, installs the skill
executor, and (with `areaId`) enters that area. The whole pipeline is headless and deterministic for a seed and an intent sequence
(`tests/integration/headless.test.js`, `tests/sim/pipeline.test.js`).

### 3.2 Game states and the screen registry (`core/game.js`, `ui/screens.js`)

States: `mainmenu | playing | paused | dead | victory`. `game.tick` samples the intent, routes queued key codes to open screens,
handles global toggles (pause, overlays, quick save/load, debug), then calls `sim.step` only if `shouldStep()`. A throwing sim step
pauses the game and records `game.lastError`; the loop keeps running. Death comes from the `playerDied` event; the final boss
(`warden-of-ash`) autosaves and switches to `victory`.

`RPG.ui.screens.create()` is a stack of canvas screens. Registry (`SCREEN_IDS`), with the file that registers each and who registers it:

| Id | Kind | File | Opened by |
|---|---|---|---|
| `mainmenu` | base (replaces the stack) | `ui/mainmenu.js` | start, `quitToMenu` |
| `playing` | base | toasts/banners from `ui/hud-overlays.js` | new game, load, resume |
| `pause` | modal | `ui/pause.js` | `Esc`/`P` |
| `inventory` | modal | `ui/inventory.js` | `I` |
| `character` | modal | `ui/character.js` | `C` |
| `skills` | modal | `ui/skills-screen.js` | `K` |
| `settings` | modal | `ui/settings.js` | `O`, main menu, pause menu |
| `saveload` | modal | `ui/saveload.js` | `M`, main menu (Continue, Import), pause menu |
| `dead` | modal, terminal | `ui/hud-overlays.js` | `dead` state (Enter respawns in town) |
| `victory` | modal, terminal | `ui/hud-overlays.js` | `victory` state (Enter keeps playing) |

Layout: the inventory, character and skills panels all start `HUD_INSET` = 80 px down so they sit below the HUD bars (y 16..74). The Skills panel
is 460 px wide and `24 + 16 * rows + 34 + 36 + 24` px tall (30 rows: 598 px), which fits 1280x720 but not 640x480. Inventory cells wrap item names to at most 3 lines of 8
characters (`nameLines` in `ui/inventory.js`; break at spaces, hard-split long words, end with an ellipsis).

While any modal id is on the stack the sim is paused. `core/game.js` registers the screens in `registerScreens()`; inventory,
skills and character are re-registered on every new game or load because they act on `world.player`. `RPG.ui.settings` owns the
settings model and the standalone settings key (SAVE-SCHEMA "Settings key"); the game re-applies it when the Settings screen closes.

## 4. Public API by module (contract)

Notation: `Type name` in JSDoc style. `World` and entity shapes are in §5. All `dt` in seconds.
All functions are namespaced; "→" is the return value. Pure = no mutation of arguments.

### core
`src/core/namespace.js`
```js
RPG = { VERSION: '1.0.0', SAVE_VERSION: 1, core:{}, data:{}, world:{}, render:{}, ui:{}, input:{}, audio:{}, persistence:{} }
```
`src/core/util.js` — `RPG.core.util`
```js
clamp(v, lo, hi) -> number          lerp(a, b, t) -> number           easeOutCubic(t) -> number
dist2(ax, ay, bx, by) -> number     dist(ax, ay, bx, by) -> number    angleTo(ax, ay, bx, by) -> radians
norm(x, y) -> {x, y}                fromAngle(a, len=1) -> {x, y}     wrapAngle(a) -> (-PI, PI]
removeAt(arr, i)                    // swap-remove, O(1)
deepFreeze(obj) -> obj              deepClone(obj) -> obj  // JSON-safe only
fmt(n) -> string                    // 1234 -> "1.2k"
```
`src/core/rng.js` — `RPG.core.Rng`
```js
Rng.create(seed: uint32) -> Rng
Rng.prototype: next() -> [0,1)   int(lo, hi) -> integer inclusive   range(lo, hi) -> float
               chance(p) -> bool   pick(arr) -> elem   weighted(items, weightFn) -> elem
               shuffle(arr) -> arr   fork(label: string) -> Rng   // child seeded from (state, hash(label))
               getState() -> uint32   setState(s)
Rng.hashString(str) -> uint32
Rng.seedFromString(str) -> uint32      // for user-typed seeds
```
`src/core/events.js` — `RPG.core.events` (shipped name; the design said `bus`)
```js
events.on(type, fn) -> unsubscribe    events.off(type, fn)    events.emit(type, payload)    events.drain()    events.NAMES
// event types: 'hit','kill','playerDied','levelUp','pickup','skillUsed','bossPhase','bossWarn','areaChanged',
//              'itemDropped','save','load','victory','uiSound'
```
`src/core/pool.js` — `RPG.core.Pool`
```js
Pool.create(factory: ()=>obj, reset: (obj)=>void, prealloc=0) -> { acquire() -> obj, release(obj), active: obj[] }
```
`src/core/game.js` — `RPG.core.game`
```js
create(deps) -> game      // headless: every collaborator (events, persistence, sim, input, renderer, screens, audio, storage, settingsStore, …) injectable
start(canvas, { settingsStorage }) -> game    // browser entry used by main.js: builds renderer, input, screens, settings store, loop
game.state: 'mainmenu'|'playing'|'paused'|'dead'|'victory'     game.world   game.settings   game.difficultyId   game.lastError
game.newGame({ seed?: uint32|string, difficulty?: string|DifficultyConfig, name? }) -> {ok}
game.saveToSlot(slot)  game.loadFromSlot(slot)  game.quickSave()  game.quickLoad()  game.autosave(reason)
game.exportSave(slot?) -> {ok, filename, text}   game.importSave(text, slot?)   game.deleteSave(slot)
game.setPaused(bool)  game.respawn()  game.continuePlaying()  game.quitToMenu()  game.setDifficulty(idOrConfig)
game.applySettings(s)  game.persistSettings()  game.queueKey(code)  game.tick(dt)  game.render(alpha)
```

### input
Shipped split (the design's `keyboard.js` and `mouse.js` are folded into `raw.js` and `input.js`):

`src/input/raw.js` — `RPG.input.raw` (pure)
```js
createRawState(bindings) -> RawState    setCode(state, code, down: bool)    setPointer(state, x, y)
clearHeld(state)                        clearPressed(state)                  // pressed = edge, cleared after each frame
```
`src/input/keymap.js` — `RPG.input.keymap` (pure)
```js
ACTIONS: moveUp moveDown moveLeft moveRight attack skill1..skill7 dash switchWeapon switchWeaponPrev interact toggleInventory toggleCharacter toggleSkills
         toggleSettings pause saveMenu quickSave quickLoad debug
defaultBindings() -> {action: code[]}    copyBindings(b)    withBinding(b, action, code) -> new bindings   isAction(name)
// Codes are KeyboardEvent.code values, or 'Mouse0'..'Mouse2'. Defaults are in §9.
```
`src/input/intent.js` — `RPG.input.intent` (pure)
```js
sampleIntent(raw, camera) -> InputFrame     screenToWorld(camera, sx, sy) -> {x, y}
SKILL_SLOTS = ['skill1'..'skill7']          TOGGLES = { inventory, character, skills, settings, pause, saveMenu, debug, quickSave, quickLoad }
/** InputFrame (shipped):
 *   move: {x, y}          -1..1 each, diagonals normalised
 *   aim: {x, y}           world coords under the mouse (camera centre if the pointer has not entered the canvas)
 *   attack: bool          level-triggered (held)
 *   skills: bool[7]       edge-triggered; index 0 = slot 1 … index 6 = E
 *   dash: bool            edge-triggered
 *   switchWeapon: -1|0|1  previous / none / next (key presses plus wheel notches, clamped per frame)
 *   interact: bool        edge-triggered
 *   toggles: { inventory, character, skills, settings, pause, saveMenu, debug, quickSave, quickLoad }   edge-triggered
 */
```
`src/input/input.js` — `RPG.input.create()` (DOM wiring; one instance per game)
```js
attach(windowTarget, canvas?)  detach()
buildFrame(camera, viewW, viewH) -> InputFrame    // samples intent, then clears edges
getBindings() -> {action: code[]}   setBinding(action, code)   resetBindings()
```
`switchWeapon` (`Tab`, `Shift+Tab` and mouse wheel) and `interact` (`F`) are shipped. Touch input (`input/touch.js`) is not.
`sim/player.js` reads `move`, `aim`, `attack`, `skills`, `dash`, `switchWeapon` and `interact`; `core/game.js` consumes `toggles`.

### data (see DATA-DEFINITIONS.md for fields)
```js
RPG.data.difficulties, .weapons, .skills, .monsters, .items, .areas, .maps   // id -> definition (frozen)
// Shipped extras: .difficultyMultiplierFields, .itemSlots, .rarities, .modifierPool. There is no RPG.data.get / .validate (INTEGRATION-PLAN M3);
// per-file checks are exercised by tests/data/. Stats live in world/stats.js and the keymap in input/keymap.js.
```

### world
All modules below exist. The world is built by `RPG.sim.createWorld` (§3.1), not by `RPG.world.createWorld`. The signatures in
this section are the original design; **the header comment of each `src/world/*.js` file is authoritative** where they differ
(`stats` is a `StatBlock` class, `combat` has `resolveHit/applyHit`, `difficulty.scaleMonster` takes `(def, level, diff, areaLevel)`,
`encounters` and `areas.enter` take extra options, and `weapons` implements only `swing`, `projectile`, `spread` and `aoeGround`).

`src/world/stats.js` — `RPG.world.stats`
```js
createStatBlock(base: Partial<StatMap>) -> StatBlock
addModifier(block, { source: string, stat: string, kind: 'flat'|'inc'|'more', value: number }) 
removeModifiersBySource(block, source: string)
recompute(block) -> void          // fills block.derived; formula in DATA-DEFINITIONS §6
get(block, stat) -> number        // derived value
```
`src/world/world.js` — `RPG.world`
```js
createWorld({ seed: uint32, difficulty: DifficultyConfig, areaId?: string }) -> World
spawnMonster(world, defId, x, y, opts?{ elite?:bool, affixes?:string[], level?:number, summonerId?:number }) -> Monster
spawnProjectile(world, spec: ProjectileSpec) -> Projectile            // pooled
spawnEffect(world, spec: EffectSpec) -> Effect                        // pooled (aoe, telegraph, text, particle, zone)
spawnItemDrop(world, item: ItemInstance, x, y) -> Drop
nextId(world) -> int
```
`src/world/tilemap.js` — `RPG.world.tilemap`
```js
create(w, h, tileSize=32) -> Tilemap { w, h, tileSize, tiles:Uint8Array, theme:string }
isSolid(map, tx, ty) -> bool       solidAtPx(map, x, y) -> bool       setTile(map, tx, ty, id)
raycastClear(map, ax, ay, bx, by) -> bool                  // line of sight
findPath(map, ax, ay, bx, by, maxNodes=400) -> {x,y}[]|null   // A*, used sparingly by AI (cached per monster)
TILE = { FLOOR:0, WALL:1, WATER:2, DOOR:3, PORTAL:4, SPAWN:5, DECOR:6 }
```
`src/world/mapgen.js` — `RPG.world.mapgen`
```js
generate(areaDef: AreaDefinition, rng: Rng) -> { map: Tilemap, spawn:{x,y}, exits:[{x,y,toArea}], encounterSlots:[{x,y,r}], bossSlot?:{x,y} }
// kinds: 'authored' (ASCII rows from areaDef.layout), 'cave' (cellular automata), 'rooms' (BSP rooms+corridors), 'open' (forest scatter)
```
`src/world/spatial.js` — `RPG.world.spatial`
```js
create(cellSize=64) -> Grid;  clear(g);  insert(g, ent);  queryCircle(g, x, y, r, out: Entity[]) -> out (reused array)
```
`src/world/collision.js` — `RPG.world.collision`
```js
moveCircle(map, ent, dx, dy) -> void         // axis-separated slide; sets ent.x/ent.y, ent.hitWall flag
circlesOverlap(a, b) -> bool                 circleHitsPoint(c, x, y) -> bool
sweepProjectile(map, p, dt) -> {hitWall:bool}
inCone(ox, oy, dirAngle, halfArc, range, tx, ty, tr) -> bool
```
`src/world/combat.js` — `RPG.world.combat`
```js
/** @typedef DamageSpec { amount, type:'phys'|'fire'|'cold'|'lightning'|'poison'|'arcane', crit?:bool, knockback?:number,
 *   sourceId?:int, skillId?:string, status?:StatusSpec[], ignoreArmor?:bool, isDot?:bool } */
rollDamage(attacker: Entity, base: number, opts?{type, skillMult, canCrit=true, rng}) -> DamageSpec
applyDamage(world, target: Entity, spec: DamageSpec) -> {dealt:number, killed:bool}   // armor, resist, shield, flash, number, lifesteal, thorns, events
applyStatus(world, target, status: StatusSpec)   // 'slow','stun','freeze','root','burn','poison','shield','haste','armorUp','reflect','invuln'
tickStatuses(world, ent, dt)
knockback(ent, fromX, fromY, force)
killEntity(world, ent, killerId)                 // death anim, XP, loot roll, events
healEntity(world, ent, amount, source?)
armorReduction(armor, attackerLevel) -> 0..0.85
```
`src/world/weapons.js` — `RPG.world.weapons`
```js
// attack behaviours keyed by WeaponDefinition.behavior
BEHAVIORS = { swing, thrust, smash, projectile, spread, beam, arc, aoeGround, explosive }
tryAttack(world, player, aim:{x,y}) -> bool        // checks cooldown+resource; starts attack anim; spawns hit/projectile/effect
updateAttack(world, player, dt)                    // advances windup/active/recover frames, applies hit on active frame
switchWeapon(world, player, slotIndexOrDelta) -> bool
currentWeapon(player) -> {def: WeaponDefinition, item: ItemInstance|null}
attackInterval(player) -> seconds                  // base / derived attackSpeed
```
`src/world/skills.js` — `RPG.world.skills`
```js
SKILL_SLOTS = ['1','2','3','4','5','Q','E']
tryCastSkill(world, player, slotIndex, aim) -> bool       // learned+equipped, cooldown (with CDR), resource
updateSkills(world, player, dt)                           // cooldown tick, channeled/buff expiry
getSkillRank(player, skillId) -> int                      getUpgradeRanks(player, skillId) -> {upgradeId:rank}
canLearn(player, skillId) -> {ok, reason?}                canUpgrade(player, skillId, upgradeId) -> {ok, reason?}
learnSkill(world, player, skillId) -> bool                upgradeSkill(world, player, skillId, upgradeId) -> bool
equipSkill(player, skillId, slotIndex) -> bool            respec(world, player, cost) -> {ok, refunded}
applyPassives(player)                                     // pushes passive-skill modifiers into player.stats (source 'skill:<id>')
resolveSkillParams(player, def) -> params                  // base params + upgrade overrides (used by cast + tooltip)
```
`src/world/ai.js` — `RPG.world.ai`
```js
BEHAVIORS = { melee, flanker, ranged, tank, exploder, summoner, boss }
updateMonster(world, mon, dt)            // dispatches on mon.def.ai; sets mon.vx/vy, triggers attacks
chooseTarget(world, mon) -> Entity|null  // player (MP-ready: nearest player)
```
`src/world/boss.js` — `RPG.world.boss`
```js
update(world, boss, dt)                  // phase thresholds from def.phases; schedules telegraphed attacks
beginTelegraph(world, boss, attackDef, targetPos)   // spawns warning effect; damage applied at telegraph end
currentPhase(boss) -> int
```
`src/world/loot.js` — `RPG.world.loot`
```js
rollDrops(world, monster) -> ItemInstance[]          // gold, potion, gear by monster tier, difficulty lootQuality, area
generateItem(rng, { baseId?, ilvl, rarityBoost=0, slot? }) -> ItemInstance
rollRarity(rng, ilvl, rarityBoost) -> 'common'|'uncommon'|'rare'|'epic'|'legendary'
rollModifiers(rng, baseDef, rarity, ilvl) -> Modifier[]
describe(item) -> {name, lines:string[], color}
```
`src/world/equipment.js` — `RPG.world.equipment`
```js
SLOTS = ['weapon','offhand','head','chest','legs','boots','ring1','ring2','amulet']   // v1 uses a subset; see DATA-DEFINITIONS
equip(world, player, invIndex) -> {ok, swapped?:ItemInstance}   unequip(world, player, slot) -> bool
dropFromInventory(world, player, invIndex) -> bool              pickup(world, player, dropId) -> bool
addToInventory(player, item) -> bool          // capacity 24
compare(player, item) -> [{stat, current, candidate, delta}]
recomputePlayerStats(player)                  // rebuild modifiers from equipment + skills + level
```
`src/world/progression.js` — `RPG.world.progression`
```js
xpToNext(level) -> int                      grantXp(world, player, amount) -> levelsGained:int
onLevelUp(world, player)                    // +stat growth, +1 skill point (+ extra every 5), full heal pulse
MAX_LEVEL = 30
```
`src/world/difficulty.js` — `RPG.world.difficulty`
```js
resolve(idOrConfig) -> DifficultyConfig     // preset or custom (clamped)
scaleMonster(def, areaLevel, cfg, isElite) -> { hp, damage, speed, xp, gold }
setDifficulty(world, idOrConfig)            // takes effect for new spawns; existing monsters rescaled by ratio
```
`src/world/encounters.js` — `RPG.world.encounters`
```js
populate(world, areaGen, rng) -> void       // fills monsters per slot from area.spawnTable weights x cfg.density
rollGroup(rng, area, budget, cfg) -> [{defId, elite, affixes}]
rollBossModifiers(rng, bossDef) -> string[]
ELITE_AFFIXES = ['fast','shielded','vampiric','explosive-death','regenerating']
```
`src/world/areas.js` — `RPG.world.areas`
```js
enter(world, areaId, fromAreaId?) -> void   // generate map (seed = hash(world.seed, areaId)), populate, place player
checkExits(world) -> void                   // portal collision -> enter(); gated by progress flags
markBossDefeated(world, bossId)  isUnlocked(world, areaId) -> bool
```
`src/world/sim.js` does not exist; the step function is `RPG.sim.step` (§3.1).

### persistence
All functions below are on `RPG.persistence`. `snapshot(world, {settings, name})` builds the save body from a world and
`restore(doc)` rebuilds a world from a body, a document or JSON text (`persistence/snapshot.js`; never mutates its input or a live world,
returns `{ok, world, game, warnings}`). `core/game.js` calls both.

`src/persistence/save.js`
```js
serialize(state, opts?) -> string          // JSON text in the SAVE-SCHEMA.md format; throws an Error with .code === 'shape' on an invalid body
deserialize(text) -> {ok:true, state, warnings, version} | {ok:false, code:'parse'|'shape'|'version'|'size', error, details?}
validate(doc) -> string[]                  // empty = valid; accepts a full document or a bare body
checksum(...)                              // exported for tests and tooling
FORMAT = 'offline-rpg-save'
```
`src/persistence/migrate.js`
```js
migrate(raw: object) -> object            // runs MIGRATIONS[v] -> v+1 until SAVE_VERSION; fails on unknown or newer version
MIGRATIONS = { 0: (save)=>save /* fromVersion: step to fromVersion+1 */ }     SAVE_VERSION = 1
```
`src/persistence/storage.js` — slot storage over `localStorage`, with in-memory fallback
```js
// Module-level default store; each call also accepts opts.storage to inject a backend.
storage.list() -> [...]     storage.read(slot) -> string|null    storage.write(slot, json) -> {ok, error?}
storage.remove(slot)        storage.available() -> bool
saveGame(slot, state, opts?) -> {ok:true} | {ok:false, code, error}   // code 'storage', or a serialize/deserialize code; refuses to write an unreadable save
loadGame(slot, opts?)        -> deserialize result, or {ok:false, code:'missing'}     // recovers an interrupted write from ':tmp'
deleteSave(slot, opts?)      -> {ok:true} | {ok:false, code:'storage', error}
```
Slots: `'auto'`, `'1'`, `'2'`, `'3'`. Key prefix: `offline-rpg:v1:`. An unreadable slot is moved to `:corrupt`; a newer-version
save is left alone.

`src/persistence/exportimport.js` — string functions only (Blob download and file picker are in `persistence/files.js`: `downloadText`, `downloadSave`, `readSaveFile`, `pickSaveText`)
```js
exportJson(state, opts?) -> string          // pretty JSON (indent 2)
importJson(text) -> deserialize result      // validates and migrates only; the caller writes it with saveGame
exportFileName(state) -> string             // e.g. game-save-7-2026-10-16.json
```
The UI side is `ui/saveload.js`; there is no clipboard fallback.

### audio
`src/audio/audio.js` — `RPG.audio`
```js
init() -> void                 // lazily on first user gesture (autoplay policy)
play(name: 'attack'|'hit'|'kill'|'skill'|'levelup'|'pickup'|'bossWarn'|'ui'|'hurt', opts?{pitch, vol})
setVolume(master, sfx)  mute(bool)
```
`src/audio/bridge.js` — `RPG.audio.bridge.attach(events, audio) -> detach`: a table of event -> sound rows. `core/game.js` `create` calls
`attachAudioBridge()` once, after the `PLAYER_DIED` listener. The attach is guarded: it is skipped when `audio` or `RPG.audio.bridge` is
missing or `audio` lacks `play`/`status`, and a throwing `attach` is caught and recorded in `game.lastWarnings` (`audio bridge: ...`).
The returned `detach` is pushed onto the same `offs` list as the game's other listeners, so `game.destroy()` detaches it. The bridge
subscribes to the event bus, not to a world, so it survives `newGame` and `loadFromSlot` and is never attached twice. No sound plays until
the first key or pointer press creates the `AudioContext`; with no context it is a no-op. Known limits: only gameplay events
(including `UI_SOUND` from the sim, e.g. inventory-full) reach the bridge; menu, inventory and pause screen actions emit nothing and are silent;
the `bossWarn` row was checked with a synthetic `BOSS_WARN` emit only.

### render
`src/render/renderer.js` — `RPG.render`
```js
create(canvas, world?) -> Renderer
renderer.resize(cssW, cssH, dpr)        renderer.draw(world, alpha, ui:{fps,debug})
renderer.setWorld(world)                // rebuilds map cache
```
`src/render/camera.js`
```js
create() -> Camera{x,y,zoom,shake}   follow(cam, tx, ty, dt)   addShake(cam, amp, dur)   worldToScreen(cam, x, y, vw, vh) -> {x,y}   screenToWorld(...)
```
`src/render/draw-shapes.js` — `RPG.render.shapes`
```js
roundedPoly(ctx, cx, cy, r, sides, rot, cornerR)  outlineFill(ctx, fill, stroke, lineW)  softShadow(ctx, x, y, rx, ry, alpha)
glow(ctx, x, y, r, color, alpha)  arcSweep(ctx, x, y, r0, r1, a0, a1)  ring(ctx, x, y, r, w)  eyes(ctx, x, y, size, lookAngle)
```
`src/render/draw-entities.js`
```js
drawPlayer(ctx, p, x, y, pal)  drawMonster(ctx, m, alpha, pal)  drawDying(ctx, ghost, alpha, pal)  drawProjectile(ctx, pr, alpha, pal)  drawDrop(ctx, d, t, pal)
// drawPlayer includes the melee swing arc (drawSwing); drawDying plays a world.fx.deaths ghost (white flash, shrink, fade over 0.4 s)
// monster drawing dispatches on def.shape: 'blob'|'dart'|'archer'|'brute'|'bomb'|'caster'|'boss-colossus'
```
`src/render/draw-effects.js`
```js
drawEffects(ctx, world, alpha, pal, layer)   // layer "below": telegraphs, ember zones, ground effects; "above": rings, novas, particles
drawDamageNumbers(ctx, list)  drawHurtFlash(ctx, w, h, t, colour)  stepWorldFx(world, ctx)   // sim-side: particles, death ghosts, damage numbers; called from sim/sim.js
```
`src/render/hud.js`
```js
draw(ctx, world, view, settings)  // HP/resource orbs-bars, XP bar, level, skill slots+cooldown sweeps, weapon, difficulty, boss bar, minimap(optional)
```
`src/render/palette.js` — `RPG.render.palette` — colour tokens (muted: bg `#14161c`, floor `#2a2f3a`, wall `#3b4252`,
player `#6fb6c9`, enemy `#c46a5a`, hazard/telegraph `#e0a458`, loot rarity colours, UI `#d8dee9`).

### ui
`src/ui/screens.js` — `RPG.ui`
```js
init(rootEl, game)  open(name)  close(name)  toggle(name)  isBlocking() -> bool  refresh(name)
// names: 'mainmenu'|'inventory'|'character'|'skills'|'settings'|'saveload'|'pause'|'death'|'victory'
```
Each screen file exports `RPG.ui.<name> = { build(root, game) -> HTMLElement, show(game), hide(), update(game) }`.

## 5. State model (plain data; the only things `persistence` may read)

Shipped world (`core/state.js`): `version, seed, rng, time, tick, nextEntityId, difficulty, area, player, monsters,
projectiles, drops, effects, progress, fx, grid`. `player` is created by `sim/player.js`: movement, `hp`/`maxHp`, `resource` and a `statBlock` (`world/stats.js`).
The `Player` shape below is the target.

```js
World = {
  version: 1, seed, rng: Rng, time: 0, tick: 0, nextEntityId: 1,
  difficulty: DifficultyConfig,
  area: { id, map: Tilemap, spawn, exits[], visited: {areaId:true} },
  player: Player, monsters: Monster[], projectiles: Projectile[], drops: Drop[], effects: Effect[],
  progress: { bossesDefeated: {bossId:true}, areasUnlocked: {areaId:true}, kills: int, deaths: int, playtime: sec },
  fx: { rng: Rng, shake: 0, damageNumbers:[] },       // cosmetic only, NOT saved
  grid: Grid                                           // transient, NOT saved
}
Entity = { id, kind:'player'|'monster'|'projectile'|'drop'|'effect', x, y, vx, vy, r, facing, hp, maxHp, team:0|1, dead:false, flash:0, status:{} }
Player = Entity & { name, classId:'wayfarer', level, xp, gold, stats: StatBlock, resource, resourceKind:'energy',
  weaponSlots:[ItemInstance|null,ItemInstance|null,ItemInstance|null], activeWeapon:0, equipment:{slot:ItemInstance|null},
  inventory:(ItemInstance|null)[24], skills:{ learned:{skillId:rank}, upgrades:{skillId:{upgradeId:rank}}, bar:(skillId|null)[7],
  cooldowns:{skillId:remaining}, points:int, spentPoints:int }, attack:{state,t,dir}, dash:{t,cd} }
Monster = Entity & { defId, level, elite:bool, affixes:string[], aiState, aiT, target:id|null, phase:int, home:{x,y}, summoned:int }
```
Cosmetic/transient fields (flash, shake, particles, damage numbers, spatial grid, pools) are never serialized.

## 6. Multiplayer-readiness (prompt §19)

`sim.step(world, inputFrame, dt)` takes input as data and owns all mutation; rendering is read-only; entity ids are
integers from `world.nextEntityId`; `player` is addressed by id in `target`/`sourceId`. Nothing implemented now.

## 7. Performance notes

- Spatial hash rebuilt per tick (≤ ~600 entities). Projectiles/effects/particles pooled (`Pool`). Tile layer pre-rendered to
  offscreen canvases per 16×16-tile chunk, drawn only if on-screen. Draw entities culled to viewport.
- No per-frame allocations in `sim.step`/`renderer.draw` hot paths (reuse query arrays, no closures in loops).
- Shadows: pre-draw ellipse via fill + alpha, never `shadowBlur`; glow via cached radial-gradient sprites.
- Cap particles at 300, damage numbers at 40, and make `settings.quality` ('low'|'med'|'high') scale both.

## 8. Test strategy (for later units)

Shipped: each area under `tests/` has its own `*.test.js` files (`tests/core`, `tests/input`, `tests/persistence`,
`tests/sim`, `tests/world`, `tests/scripts`). Where an area needs the browser scripts, a `load.js` loads them into a `vm`
context (see `tests/world/load.js`). Run with `npm run test:unit` (`node --test`). There is no `tests/run.js`.
The suites below are the target set; the ones listed in the tree above exist.

Required suites: rng determinism,
`data.validate()==[]`, stat recompute, damage pipeline, loot determinism (same seed → same items), mapgen determinism and
connectivity (spawn reaches exits), save round-trip (`deserialize(serialize(w))` deep-equals gameplay state), migration, corrupt save
rejection, headless 60 s sim with scripted input (no exceptions, monsters die, XP grows).

## 9. Key bindings

S is both "move down" (WASD) and the prompt's "Skills" screen. Resolution: **WASD keeps movement; the Skills screen is `K`.**
(`S` is not bound to any UI action.) Full map, all rebindable in Settings (stored as non-default `keys` in the settings key, defaults in `input/keymap.js`):

| Action | Default | Notes |
|---|---|---|
| Move up/left/down/right | `W` `A` `S` `D` (also arrow keys) | |
| Aim | Mouse pointer | world coords via camera |
| Basic attack | Left mouse (hold repeats) | `J` alt for trackpad users |
| Skills slot 1–5 | `1` `2` `3` `4` `5` | |
| Extra skills | `Q`, `E` | slots 6, 7 |
| Dash / mobility | `Space` | direction = movement, else aim |
| Switch weapon | `Tab` next, `Shift+Tab` previous, mouse wheel | 3 weapon slots; `R` alt |
| Interact / pick up | `F` (also auto-pickup on walk-over for gold) | |
| Inventory | `I` | |
| Character | `C` | |
| Skills (tree) | **`K`** | resolves S conflict |
| Settings | `O` | |
| Save / Load menu | `M` (menu) · `F5` quick save · `F9` quick load | |
| Pause | `Esc` (also `P`) | closes top overlay first |
| Toggle debug (fps, seed) | `F3` | |
| Touch (Android) | v1: virtual stick + buttons rendered by `input/touch.js` — OPTIONAL stretch, desktop is the primary target | not implemented |

Shipped defaults match this table except: `Tab`/`Shift+Tab`/`R` weapon switching, `F` interact and `Esc`-closes-overlay
behaviour are not bound yet (no `switchWeapon`/`interact` action in `input/keymap.js`). Actions that exist in the keymap
but have no handler yet: attack, skill1–7, dash, the toggles, `saveMenu`, `quickSave`, `quickLoad`, `debug`.

## 10. Decisions and open points

- ADR-1 Plain JS, global `RPG` namespace, classic scripts → `file://` works. (Alt: ES modules rejected, blocked on `file://` in Chromium.)
- ADR-2 Fixed 60 Hz sim, uncapped render with interpolation.
- ADR-3 Seeded RNG with forkable streams: world seed → per-area stream (`hash(seed, areaId)`) → per-encounter streams, so
  re-entering an area regenerates the same layout; loot uses a separate `world.lootRng` forked from the seed.
- ASSUMPTION: one class `wayfarer`; `classId` + `RPG.data.classes` hook reserved for more.
- OPEN_QUESTION (non-blocking): touch controls for Android — desktop is the target; a later unit may add `input/touch.js`.

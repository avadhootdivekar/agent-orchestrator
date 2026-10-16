# Offline RPG — Architecture (v1 design contract)

Status: design contract for parallel implementation. Plain JavaScript (ES2019, no TypeScript, no bundler
dependency). Units building modules MUST use the file names and signatures below; changes go through a
design amendment, not ad-hoc drift.

**Implementation status (wave 1–2).** Sections below describe the target design. Where the code differs, the
shipped form is given in the "shipped" notes and the design text is marked **(planned)**. Shipped today:

- `src/core/` (namespace, util, rng, pool, entities, state, loop), `src/input/` (raw, intent, keymap, input),
  `src/sim/` (player, sim), `src/world/` (stats, tilemap, collision, combat, progression), `src/persistence/` (save,
  migrate, storage, exportimport), `src/render/` (camera, palette, renderer, hud), `src/audio/audio.js`,
  `src/data/` (weapons, skills, monsters, items, difficulties, maps/test-map), `src/main.js`.
- Build, preview and offline guard in `scripts/`. The `build/` directory and `index.src.html` in the layout below
  do not exist; the shipped names are in §2.
- The simulation moves the player and records aim. The combat, progression, HUD and audio modules and the data
  definitions are shipped, but `sim/sim.js` does not call them yet. Attacks, skills, AI, spawning, loot, areas and
  the UI screens are planned. See [`GAMEPLAY-SYSTEMS.md`](GAMEPLAY-SYSTEMS.md).

**Why plain JS:** the build is a zero-dependency node script (see BUILD-OFFLINE.md); TypeScript would force a
compiler dependency and an `npm install` that needs the network. JSDoc typedefs in each file give editor hints.

## 1. Principles

1. **Separation (prompt §19/§20):** State (`world`) is plain data. Simulation mutates state only through
   `step(world, inputFrame, dt)`. Input produces an `InputFrame`. Rendering reads state, never mutates it.
   Persistence serializes a subset of state. No module in `render/` or `input/` may be imported by `core/`, `world/`
   or `persistence/` logic. Enforced by load order only; the lint check in the build script is **(planned)**.
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
   (inventory/skills/…) are DOM overlays updated only while open.

## 2. Directory layout

Shipped layout (`✓` = exists; `(planned)` = design only, not written yet):

```
output/offline-rpg/
  package.json              scripts: build, preview, test, check-offline   (no dependencies)  ✓
  README.md                 ✓
  index.html                template with <!--@CSS--> and <!--@JS--> markers                   ✓
  scripts/
    build.mjs               single-file build -> dist/index.html; JS_ORDER lists the load order  ✓
    preview.mjs             static server for dist/ (127.0.0.1:8777)                            ✓
    check-offline.mjs       offline guard (see BUILD-OFFLINE.md §4)                             ✓
  styles/main.css           layout and overlays; system fonts only                              ✓
  src/
    data/
      maps/test-map.js      the only map definition so far                                      ✓
      weapons.js  skills.js  monsters.js  items.js  difficulties.js                             ✓
      areas.js  stats.js  keymap.js                                                              (planned)
    core/
      namespace.js  util.js  rng.js  pool.js  entities.js  state.js  loop.js                     ✓
      events.js  game.js                                                                         (planned)
    input/
      raw.js                raw key/pointer state, no DOM                                       ✓
      keymap.js             ACTIONS, defaults, rebinding helpers (pure)                         ✓
      intent.js             raw state + camera -> InputFrame (pure)                             ✓
      input.js              DOM wiring; RPG.input.create() -> per-game input object              ✓
      keyboard.js  mouse.js                                                                      (folded into input.js)
    world/
      stats.js              StatBlock: base + modifiers -> derived stats                        ✓
      tilemap.js            tile grid, solid queries, TILE constants                            ✓
      collision.js          circle-vs-tile movement (moveCircle)                                ✓
      combat.js             damage resolution, statuses, cooldowns (not yet called by sim)       ✓
      progression.js        XP curve, level-up grants (not yet called by sim)                    ✓
      spatial.js  mapgen.js  world.js  weapons.js  skills.js  ai.js  boss.js
      loot.js  equipment.js  difficulty.js  encounters.js  areas.js  sim.js                     (planned)
    sim/
      player.js             player entity, acceleration movement, aim facing                    ✓
      sim.js                RPG.sim.createWorld(opts), RPG.sim.step(world, intent, dt)          ✓
    render/
      renderer.js           RPG.render.create(canvas) -> renderer                               ✓
      camera.js             follow / snap / world<->screen                                      ✓
      palette.js            colour tokens                                                       ✓
      hud.js                buildHudModel (pure) and draw                                       ✓
      draw-shapes.js  draw-entities.js  draw-map.js  draw-effects.js                          (planned)
    persistence/
      save.js               serialize / deserialize / validate / checksum                       ✓
      migrate.js            SAVE_VERSION and migration table                                    ✓
      storage.js            localStorage adapter (+ in-memory fallback), saveGame / loadGame    ✓
      exportimport.js       exportJson / importJson / exportFileName (string functions)         ✓
    audio/audio.js          procedural Web Audio sound effects (not yet called by main)         ✓
    ui/                                                                                          (planned)
    main.js                 entry: builds world, wires input -> sim -> renderer into the loop  ✓
  tests/
    core/ input/ persistence/ scripts/ sim/ world/    one folder per area                      ✓
    <area>/load.js          some areas: loads the area's src files into a vm context (e.g. tests/world/load.js)
    *.test.js  *.test.mjs   run with npm run test:unit (node --test)
    fixtures/offline/       inputs for the offline guard
  docs/                     design and test documents
  dist/                     build output (git-ignored): index.html
```

### Load order (authoritative copy: `JS_ORDER` in `scripts/build.mjs`)

`core/namespace → core/util → core/rng → core/events → core/pool → data/* → world/stats → world/tilemap → world/collision →
world/spatial → world/mapgen → world/world → world/combat → world/weapons → world/skills → world/ai → world/boss →
world/loot → world/equipment → world/progression → world/difficulty → world/encounters → world/areas → world/sim →
sim/player → sim/sim → persistence/* → input/* → audio/audio → render/palette → render/camera → render/draw-* → render/hud →
render/renderer → ui/* → core/loop → core/game → main`.

`JS_ORDER` also lists files not yet written (they are skipped), and the build appends any unlisted file with a warning.
The list above is the design order; the shipped order is whatever `JS_ORDER` says.

Every module may call only modules earlier in the list at *load* time; at *run* time, calls through `RPG.<ns>`
resolve lazily, so cross-references between peers in the same layer are fine.

**Headless rule:** everything in `core/`, `data/`, `world/`, `sim/`, `persistence/` and `input/raw.js`, `input/intent.js`,
`input/keymap.js` MUST run in plain node (no `window`, `document`, `localStorage` at load time) so the area tests can load
them in a `vm` context with only `{RPG: {}}`-style globals. `namespace.js` uses `globalThis`.

## 3. Game loop (core/loop.js)

```js
RPG.core.createLoop({ step, render, dt = 1/60, maxSteps = 5 }) -> { start(), stop(), running }
// per rAF(ts): acc += min(ts-last, 0.25); n=0; while (acc >= dt && n++ < maxSteps) { step(dt); acc -= dt; }
//              render(acc/dt)   // alpha in [0,1)
```
`step` is a no-op while a blocking overlay is open (`Game.paused`). `visibilitychange` → auto-pause + resets `last`.

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
`src/core/events.js` — `RPG.core.bus`
```js
bus.on(type, fn) -> unsubscribe    bus.emit(type, payload)    bus.clear()
// event types: 'hit','kill','playerDied','levelUp','pickup','skillUsed','bossPhase','bossWarn','areaChanged',
//              'itemDropped','save','load','uiSound'
```
`src/core/pool.js` — `RPG.core.Pool`
```js
Pool.create(factory: ()=>obj, reset: (obj)=>void, prealloc=0) -> { acquire() -> obj, release(obj), active: obj[] }
```
`src/core/game.js` — `RPG.core.Game`
```js
Game.start(canvasEl: HTMLCanvasElement, rootEl: HTMLElement) -> Game
Game.state: 'menu'|'playing'|'paused'|'dead'|'victory'
game.newGame({ seed?: uint32|string, difficulty?: string|DifficultyConfig }) 
game.loadFromSlot(slot) -> {ok, error?}   game.saveToSlot(slot) -> {ok, error?}
game.setPaused(bool)    game.world    game.settings
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
ACTIONS: moveUp moveDown moveLeft moveRight attack skill1..skill7 dash toggleInventory toggleCharacter toggleSkills
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
 *   toggles: { inventory, character, skills, settings, pause, saveMenu, debug, quickSave, quickLoad }   edge-triggered
 */
```
`src/input/input.js` — `RPG.input.create()` (DOM wiring; one instance per game)
```js
attach(windowTarget, canvas?)  detach()
buildFrame(camera, viewW, viewH) -> InputFrame    // samples intent, then clears edges
getBindings() -> {action: code[]}   setBinding(action, code)   resetBindings()
```
Still planned: `switchWeapon` and `interact` actions and their frame fields, touch input (`input/touch.js`), and
persisting bindings in `settings`.
Shipped note: `sim/player.js` reads `move` and `aim` only. `attack`, `skills`, `dash` and `toggles` are produced
but not yet acted on.

### data (see DATA-DEFINITIONS.md for fields)
```js
RPG.data.difficulties, .weapons, .skills, .monsters, .items, .areas, .stats, .keymap   // id -> definition (frozen)
RPG.data.get(kind: 'weapon'|'skill'|'monster'|'item'|'difficulty'|'area', id) -> def | throws Error(`unknown ${kind} ${id}`)
RPG.data.validate() -> string[]    // cross-reference + range checks; tests assert it returns []
```

### world
Shipped: `world/stats.js` (`RPG.world.StatBlock`, `RPG.world.stats`), `world/tilemap.js`, `world/collision.js`.
`sim/sim.js` builds the world directly: `RPG.sim.createWorld({seed, mapId})` returns a `core/state.js` world with
`area`, `player` and the `progress` block. The `createWorld` and entity factory signatures below are planned.

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
`src/world/sim.js` — `RPG.world.sim`
```js
step(world, input: InputFrame, dt) -> void
// FIXED ORDER: time += dt
//  1 movePlayer(input)           2 weapons.tryAttack / updateAttack    3 skills.tryCastSkill / updateSkills
//  4 ai.updateMonster* + boss    5 projectiles advance + collide       6 effects (aoe/zone/telegraph) tick
//  7 combat.tickStatuses         8 spatial rebuild (before 5)          9 pickups / interact / areas.checkExits
// 10 progression/cleanup (swap-remove dead, release pooled)  11 camera targets (render-side reads only)
```

### persistence
Shipped: the save body is passed in and out directly. Building it from a World, and the `settings` block, are the
caller's job (not yet written). All functions below are on `RPG.persistence`.

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
MIGRATIONS = { /* fromVersion: (save)=>save */ }     SAVE_VERSION = 1
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

`src/persistence/exportimport.js` — string functions only (Blob download and file picker are planned in the UI layer)
```js
exportJson(state, opts?) -> string          // pretty JSON (indent 2)
importJson(text) -> deserialize result      // validates and migrates only; the caller writes it with saveGame
exportFileName(state) -> string             // e.g. game-save-7-2026-10-16.json
```
Planned: `exportSave(slot)` (Blob + `<a download>`), `importSaveFile(file)` (FileReader), clipboard fallback.

### audio
`src/audio/audio.js` — `RPG.audio`
```js
init() -> void                 // lazily on first user gesture (autoplay policy)
play(name: 'attack'|'hit'|'kill'|'skill'|'levelup'|'pickup'|'bossWarn'|'ui'|'hurt', opts?{pitch, vol})
setVolume(master, sfx)  mute(bool)
```

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
drawPlayer(ctx, p, alpha)  drawMonster(ctx, m, alpha)  drawProjectile(ctx, pr)  drawDrop(ctx, d, t)  drawProp(ctx, prop)
// monster drawing dispatches on def.shape: 'blob'|'dart'|'archer'|'brute'|'bomb'|'caster'|'boss-colossus'
```
`src/render/draw-effects.js`
```js
drawEffects(ctx, world, alpha)   // telegraphs (always on top of floor, below entities), aoe rings, particles, damage numbers
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
(`S` is not bound to any UI action.) Full map, all rebindable in Settings (stored in `settings.keys`, defaults in `data/keymap.js`):

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
| Touch (Android) | v1: virtual stick + buttons rendered by `input/touch.js` — OPTIONAL stretch, desktop is the primary target | (planned) |

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

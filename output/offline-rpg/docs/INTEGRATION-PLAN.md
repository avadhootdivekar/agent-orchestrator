# Offline RPG — Wave-4 integration plan

Status: **implemented (waves 4–5); this document is now a record, reconciled in wave 6 (unit `w06-08-docs-reconcile`).**
It was written as the wave-4 design (unit `w03-18-integration-plan`) against the tree at the end of wave 3. Sections 0–5 and 8
describe the intent; the "Done" table below says what happened to each item. Where this plan and the code differ, the code and
[`ARCHITECTURE.md`](ARCHITECTURE.md) (now reconciled) win. Wording in sections 0–9 that says "today", "not yet" or "missing" refers to
the end of wave 3 and is left as written.

## Done table (checked against the tree)

| Item | Status | Where it landed / deviation |
|---|---|---|
| W4-00 `JS_ORDER` + checks | **done** | `scripts/build.mjs`, `tests/scripts/build-order.test.js`. `--verify` fails on unlisted, duplicate and missing files; `PENDING_JS` is empty, so strict mode is on |
| W4-01 sim stage dispatch, `createWorld` additions | **done** | `src/sim/sim.js` (`ORDER`, `stage.*`), `core/state.js`; stage list in ARCHITECTURE §3.1 |
| W4-02 player stage | **done** | `src/sim/player.js`; it also performs the work of the unregistered `weapons` and `skills` stages |
| W4-03 monster stages | **done** | `src/sim/monsters.js` (`spatial`, `monsters`, `movement`) |
| W4-04 combat stages | **done** | `src/sim/combat-step.js` (`projectiles`, `statuses`, `resolve`, `RPG.sim.hurtPlayer`) |
| W4-05 rewards stages | **done** | `src/sim/rewards.js` (`rewards`, `pickups`, `playerDeath`) |
| W4-06 areas | **done** | `src/world/areas.js`; deviation: `restore()` does not pass `restorePos`, it sets the position itself after `enter` (SAVE-SCHEMA notes the cleared-slot consequence) |
| W4-07 `core/game.js` | **done** | state machine, autosave, quick save/load, export/import/delete, settings; slot `1` is the quick slot as planned |
| W4-08 snapshot, files | **done** | `src/persistence/snapshot.js`, `files.js` |
| W4-09 / 09b / 10 / 11 rendering and audio | **done** | `render/draw-entities.js`, `draw-effects.js`, `renderer.js`, `hud.js`, `camera.js`; `audio/audio.js`, `audio/bridge.js` |
| W4-12 input | **done** | `switchWeapon` (`Tab`, `Shift+Tab`, wheel) and `interact` (`F`) added; the `R` alternative was not bound |
| W4-13 / 13a / 13b / 13c UI | **done** | `ui/screens.js`, `hud-overlays.js`, `mainmenu.js`, `pause.js`, `saveload.js`, `character.js`, `skills-screen.js`, `settings.js`. Deviation: text entry is a seed field typed in the main menu and a file picker for import, not `window.prompt` |
| W4-14 integration tests | **done** | `tests/integration/` (headless run, determinism, boss, areas, save round trips) |
| W4-15 `main.js`, strict build | **done** | `src/main.js` calls `RPG.core.game.start`; `npm run check-all` passes |
| W4-16 docs | **done (this unit)** | ARCHITECTURE, README, SAVE-SCHEMA, GAMEPLAY-SYSTEMS, BUILD-OFFLINE, TEST-CHECKLIST and this file reconciled |
| W4-17 verify (22 steps in a real build) | **not evidenced** | only headless tests exist; no manual browser walkthrough is recorded (TEST-CHECKLIST) |
| Wave 5 additions | **done** | `scripts/bench.mjs` (`npm run bench`), boot test `tests/scripts/boot.test.js`, settings screen and key rebinding, boss-combat and pipeline tests |
| Touch input (`input/touch.js`) | **not planned for v1** | desktop only |

Section 9 risk 1 ("no live-play evidence") still stands: the tests are headless (894 at the time of writing, all passing; see TEST-CHECKLIST C3).

## 0. Reading the brief against the code

The brief calls the pipeline file `src/world/sim.js`. **That file does not exist and should not be created.** The shipped
simulation entry is `src/sim/sim.js` (`RPG.sim.createWorld`, `RPG.sim.step`), already used by `src/main.js` and
`tests/sim/`. `JS_ORDER` and ARCHITECTURE §4 still list `src/world/sim.js`; both are stale (section 5, table M7). Everything
below says `src/sim/sim.js`.

## 1. Mismatches between ARCHITECTURE.md and shipped code

| # | ARCHITECTURE says | Shipped code | Resolution |
|---|---|---|---|
| M1 | `RPG.core.bus` with `on/emit/clear` (§4 core) | `RPG.core.events` with `on/off/emit/drain`, `NAMES`, `create()`; no `clear` | Keep shipped. Doc fix. |
| M2 | `core/game.js` `Game.start(canvas, root)`, states `menu/playing/paused/dead/victory` | not written; `main.js` builds the loop directly | Build per section 3. States become `mainmenu/playing/paused/dead/victory` (names match `ui/screens` ids; section 3.1). |
| M3 | `RPG.data.get(kind,id)` and `RPG.data.validate()` | neither exists. Only `RPG.data.areas.validate()` (non-enumerable) and per-file frozen maps | Do not add `data.get`/`data.validate`; the integration test calls each existing per-file check (W4-14). Doc fix. |
| M4 | `data/stats.js`, `data/keymap.js` | not written. Stats live in `world/stats.js`, keymap in `input/keymap.js` | Prune both from `JS_ORDER` (section 5). |
| M5 | `world/stats.js` functional API `createStatBlock/addModifier/...` | class `RPG.world.StatBlock` + `RPG.world.stats` constants | Keep shipped. Doc fix. |
| M6 | `world.js`: `createWorld(...)` | `RPG.world` has spawn helpers only. `createWorld` is `RPG.sim.createWorld`. `world.js` adds `despawnDead`, `forEachMonster`, `livingMonsters`, `forEachEntity`, `findEntity`, `poolStats` | Keep shipped. |
| M7 | `world/sim.js` and `world/areas.js` in the load order | `world/sim.js` never will exist (`sim/sim.js` is the entry). `world/areas.js` not written yet | Prune `world/sim.js`; write `world/areas.js` (W4-06). |
| M8 | `combat.rollDamage/applyDamage/killEntity/healEntity/knockback` | `resolveHit(attacker,target,hit,rng)` (pure, returns result + `events[]`), `applyHit(attacker,target,result)`, `applyStatus(ent,spec)`, `tickStatuses(ent,dt)->events`, cooldown helpers. No `killEntity`; death = `target.dead=true` set by `applyHit`; `result.killed` | Kill handling (xp, loot, events) is the new rewards stage (W4-05). |
| M9 | `weapons.tryAttack/updateAttack/switchWeapon/currentWeapon/attackInterval`; behaviours swing, thrust, smash, projectile, spread, beam, arc, aoeGround, explosive | adds `updateProjectiles(world,dt)` and `moveFactor`. Only `swing`, `projectile`, `spread`, `aoeGround` implemented; `tryAttack` refuses the rest | Keep. The three shipped weapons (sword, hunting-bow, arc-staff) only use implemented ones. |
| M10 | `skills.tryCastSkill(world, player, slotIndex, aim) -> bool` | returns `{ok, reason?, applied}`; effects run only if `skills.setExecutor(fn)` was called; `skill-effects.js` is the executor with `execute`, `updateProjectiles`, `updateEffects`, `absorbDamage` | Wire `setExecutor(RPG.world.skillEffects.execute)` once in `sim.createWorld` (section 2.3). |
| M11 | `ai.updateMonster(world, mon, dt)` and `chooseTarget` | `ai.think(monster, view, rng, dt) -> intent` is **pure**; the sim applies the intent. Actions: `telegraph`, `attack`, `summon`, `explode`, `phase` | The monsters stage is the applier (W4-03). |
| M12 | `boss.update(world, boss, dt)`, `beginTelegraph`, `currentPhase` | `boss.update(bossWorld, boss, intent, dt)`; `bossWorld` is a duck-typed `{effects, players[], spawnMonster?, damagePlayer?, onPhaseChange?}`. It **reassigns** `world.effects = world.effects.filter(...)` and pushes plain, unpooled effect objects | Never pass the real `world`. Pass an adapter whose `effects` is the separate array `world.telegraphs` (section 2.2 step 6). Pooled `world.effects` must not be reassigned. |
| M13 | `loot.rollDrops(world, monster)`, `generateItem(rng, {...})`, `describe(item)` | `rollDrops(rng, lootTable, {areaLevel, difficulty, eliteBoost})`, `rollMonsterDrops(rng, monsterDef, opts)`, `generateItem(rng, opts)`; no `describe` | Rewards stage passes `world.lootRng` (new, section 2.4). `describe` stays UI-side (`ui/inventory.js` formats). |
| M14 | `equipment.equip(world, player, invIndex)`, `SLOTS`, `recomputePlayerStats`, `addToInventory` | `equip(eq, statBlock, item)`, `unequip`, `applyAll`, `compare`, `createEquipment`; `eq = {slots}`. `ui/inventory.js` already owns inventory adapters (`pickup`, `equipFromSlot`, `dropFromSlot`) and uses `{slots: player.equipment}` | UI model is the single caller. Rewards stage uses `RPG.ui.inventory.pickup`-equivalent through `W4-05`'s own thin `addToInventory`; see 2.4. |
| M15 | `progression.xpToNext, grantXp(world, player, amount), onLevelUp` | pure `createProgression`, `addXp(prog, amount)`, `xpToNext`, `serialize/restore`, `LEVEL_STAT_GROWTH`, `onLevelUp` hook field; operates on its own object, not on the player | Rewards stage builds a progression view over `player` (level/xp) and applies growth itself (2.4). |
| M16 | `difficulty.scaleMonster(def, areaLevel, cfg, isElite)`, `setDifficulty(world, id)` | `scaleMonster(def, level, diff, areaLevel)`, `resolve`, `clampCustom`, `spawnBudget(area, diff)`; no `setDifficulty` | `Game.setDifficulty` (W4-07) = `resolve` + store on `world.difficulty`; new spawns scale. No rescale of live monsters (documented). |
| M17 | `encounters.populate(world, areaGen, rng)`, `rollGroup` | `compose(areaDef, slots, level, diff, rng) -> [{defId,x,y,elite,affixes}]`, `bossModifiers(bossDef, diff, rng)` | `world/areas.js` does the populate loop with `spawnMonster` + `scaleFn` (W4-06). |
| M18 | `areas.js`: `enter/checkExits/markBossDefeated/isUnlocked` | not written | W4-06. |
| M19 | `mapgen.generate(areaDef, rng)` returns `{map, spawn, exits, encounterSlots, bossSlot?}`, kinds authored/cave/rooms/open | matches, except kind `rooms` (BSP) is **not implemented and throws**; `data/areas` still lists it in KINDS. No shipped area uses it | None for v1; do not add a `rooms` area. |
| M20 | Input: `switchWeapon` and `interact` actions, wheel, `touch.js` marked planned | still absent; `intent` has `move, aim, attack, skills[7], dash, toggles{...}` | W4-12 adds `switchWeapon` (Tab, wheel) and `interact` (F). `touch.js` stays out of scope. |
| M21 | `ui.init(rootEl, game)`, per-screen `build/show/hide/update`, DOM overlays | shipped `ui/screens.js` is a canvas screen **stack manager**: `create(opts) -> manager` with `register({id,update,draw,onKey})`; ids `mainmenu, playing, pause, inventory, character, skills, settings, saveload`. `ui/inventory.js` is a canvas screen (`createScreen`). **No DOM** | All screens are canvas screens registered into the manager. Doc fix. `dead` and `victory` need new ids (W4-13 owns `screens.js`). |
| M22 | `render.draw(ctx, world, view, settings)` for HUD; `renderer.draw(world, alpha, ui)`; `renderer.setWorld` | `hud.buildHudModel(state, opts)` + `hud.draw(ctx, model, viewW, viewH)`; `renderer.draw(world, alpha, dt)` draws map + player only; `snapTo(world)`; no `setWorld` | W4-10 extends `renderer.draw` and calls `hud.draw` (signature `draw(world, alpha, dt, ui)`). |
| M23 | `render.shapes.roundedPoly/outlineFill/eyes/...` | `outlinedCircle/outlinedRect/rotatedPolygon/softShadow/glow/ring/arc/cone` | Keep shipped names. |
| M24 | Persistence: caller builds the save body | `snapshot.js` (w03-16) would; not on disk | Section 3.3. |
| M25 | `Pool` `{acquire, release, active}` | also `freeCount()`; `world.js` keeps pools in its own `poolsOf(world)` (transient, not on a field the save sees) | Fine. |
| M26 | `world.lootRng` (ADR-3) | `createWorldState` has `rng` and `fx.rng` only | W4-01 adds `lootRng` and `telegraphs` to `core/state.js` (2.4). |
| M27 | Audio `play(name)` includes `hurt` | `audio.js` `SOUNDS` has `attack, hit, kill, skill, levelup, pickup, bossWarn, ui`; **`hurt` is missing** (grep) | W4-09b adds `hurt` to `audio.js`; until then the bridge maps player-hit to `hit` at lower pitch. |
| M28 | Event types list (11) | shipped `NAMES` also has `PLAYER_DIED`, `UI_SOUND`, `SAVE`, `LOAD` (matches) | None. |
| M29 | `Game.paused` makes the loop's `step` a no-op | `createLoop` takes `shouldStep()`; `main.js` does not use it | Game passes `shouldStep: () => game.state === 'playing' && !ui.isBlocking()`. |
| M30 | `JS_ORDER` "mirrors" ARCHITECTURE §2 | contains 5 entries that will never exist and omits `persistence/snapshot.js`, `persistence/files.js`, `audio/bridge.js`, `ui/hud-overlays.js`, `sim/{combat-step,monsters,rewards}.js` | Section 5. |

## 2. The per-tick pipeline (`RPG.sim.step(world, intent, dt)`)

### 2.1 Principles

- One entry, fixed order, data in / data out. `step` never reads the DOM, the clock or `Math.random`.
- Each stage is a function in its own file on `RPG.sim.stage.<name>(world, ctx)`. `sim/sim.js` only calls them in order
  and owns `ctx`. That is what lets wave-4 units work in parallel without sharing a file.
- `ctx` is a per-world scratch object kept on the world as a non-saved field `world.scratch` (created by `createWorld`):
  `{ intent, dt, events: [], hits: [], spatial, killed: [], bossWorld }`. Arrays are reused (`length = 0`) each tick.
- Events go through `RPG.core.events.emit` (synchronously to listeners, and queued for `drain`). Stages emit; they never
  call audio or UI directly.
- A stage that finds a missing optional dependency (for example a monster def without `lootTable`) skips, never throws.
  Throwing inside `step` would freeze the game; `Game` wraps `step` in try/catch and drops to the `paused` state with an
  error banner (3.1).

### 2.2 Order

`step` returns immediately for `dt <= 0`. Otherwise, in this order:

| # | Stage (file) | What it does | Reads | Writes / emits |
|---|---|---|---|---|
| 0 | `begin` (`sim/sim.js`) | `ctx.intent = intent; ctx.dt = dt;` clear `ctx` arrays; `world.tick++` happens at the **end**, as now, so tick N sees time N·dt | intent | ctx |
| 1 | `input` (`sim/player.js`) | Sanitise intent (move clamp already in `player.step`); derive `wantAttack`, `wantSkill[i]`, `wantDash`, `wantSwitch`, `wantInteract` | intent | `ctx.actions` |
| 2 | `player` (`sim/player.js`) | `prevX/prevY` snapshot; dash (if `wantDash` and `dash.cd<=0`: burst velocity for `dash.t`, `invuln` status 0.15 s); acceleration movement `approach()` with `weapons.moveFactor(player)` applied to speed; facing from aim; `moveCircle` against `world.area.map`; life/resource regen (`stats.lifeRegen`, `stats.resourceRegen`, resource capped at max). **Movement only touches the player** | intent, stats | player; `dash` |
| 3 | `weapons` (`sim/player.js`, calls `world/weapons.js`) | `switchWeapon(world, p, delta)` if wanted; `weapons.tryAttack(world, p, aim)` if `intent.attack` held; `weapons.updateAttack(world, p, dt)` (fires the behaviour on entering `active`; hits go through `combat.resolveHit/applyHit` inside weapons) | attack, aim | monsters' `hp/dead/flash/vx/vy/status`, `world.projectiles`, `world.effects`, `fx.damageNumbers`; emit `HIT` is done by stage 10 from `ctx.hits` (below) |
| 4 | `skills` (`sim/player.js`, calls `world/skills.js`) | for each `i` with `intent.skills[i]`: `skills.tryCastSkill(world, p, i, aim)`; then `skills.updateSkills(world, p, dt)` (cooldown tick). The executor (`skillEffects.execute`) is registered once in `createWorld`. Emit `SKILL_USED {skillId, slot}` on `ok`; on `reason` emit `UI_SOUND 'ui'` only for a keypress that failed (cooldown/resource) | skills bar | resource, cooldowns, projectiles/effects/statuses |
| 5 | `spatial` (`sim/monsters.js`) | rebuild `world.grid` (`spatial.clear`; insert player + living monsters). Created in `createWorld` (`spatial.create(64)`). Used by stages 6 and 8 | monsters | `world.grid` |
| 6 | `monsters` (`sim/monsters.js`) | for each living monster: if frozen/stunned (`combat.hasStatus`) skip thinking; `intent = ai.think(m, view, world.rng, dt)` with `view = { target: player (or null if dead), summonCount(m) }`; apply `vx,vy,facing` scaled by `combat.moveSpeedFactor(m)`; apply actions in order: `telegraph` → push a ground warning (via boss adapter for bosses, `world.spawnEffect` type `telegraph` for others); `attack` → arm/resolve (melee in range → hit the player through stage 8's `hurtPlayer`; `projectile` → `world.spawnProjectile` team 1); `summon` → `spawnMonster(..., {summonerId})` capped by `aiParams`; `explode` → area hit + `m.dead = true`; `phase` → emit `BOSS_PHASE`. Bosses: `boss.update(bossWorld, m, intent, dt)` where `bossWorld = { effects: world.telegraphs (get/set), players: [world.player], spawnMonster, damagePlayer: hurtPlayer, onPhaseChange }`. Emit `BOSS_WARN` when a boss `telegraph` starts | grid, player | monsters, projectiles, telegraphs |
| 7 | `movement` (`sim/monsters.js`) | integrate monsters: `x += vx·dt` through `collision.moveCircle(map, m, …)`; decay knockback velocity (`vx *= 0.85^(dt·60)`); separation push using `world.grid` so packs do not stack | monsters | monsters |
| 8 | `projectiles/effects` (`sim/combat-step.js`) | `weapons.updateProjectiles(world, dt)` (player projectiles; team 0) → `skillEffects.updateProjectiles` → `skillEffects.updateEffects` (whirl, heal-over-time, ttl); then **enemy** projectiles (team 1) advance with `collision.sweepProjectile` and hit the player via `hurtPlayer`; ground effects (`world.effects` type `aoe/zone`) tick damage; `world.telegraphs` already advanced by `boss.update` (6) | projectiles, effects | hp of player/monsters, `ctx.hits` |
| 9 | `statuses` (`sim/combat-step.js`) | `combat.tickStatuses(ent, dt)` for the player and every living monster (burn/poison damage, expiry), `combat.tickCooldowns(player, dt)`; decay `flash`; `skillEffects.absorbDamage` is already used inside `hurtPlayer` | status | hp, `ctx.hits` |
| 10 | `resolve` (`sim/combat-step.js`) | convert `ctx.hits` into events: `HIT {targetId, dealt, crit, type, x, y, team}` (for `team 1` targets the audio bridge plays `hurt`). `hurtPlayer(p, amount, info)` is defined here: invuln check → `absorbDamage` → armor via `combat.armorMitigation` for raw boss/AI amounts → `p.hp -= …`, `p.flash`, `fx.shake`, thorns/reflect, `HIT`; `p.hp<=0` sets `p.dead` | ctx.hits | events |
| 11 | `rewards` (`sim/rewards.js`) | for each monster with `dead && !m.rewarded`: set `rewarded`, `progress.kills++`, `xp = def.xp · diff.xpMultiplier · level scaling`, gold from `def.gold` · `goldMultiplier` · `(1 + stats.goldFind)`, `loot.rollMonsterDrops(world.lootRng, def, {areaLevel, difficulty, eliteBoost})` → `world.spawnItemDrop`, gold as a `drop` with `gold` field; emit `KILL {defId, elite, x, y, xp}` and `ITEM_DROPPED`. A boss death also calls `areas.markBossDefeated`, drops its epic table, emits `KILL` with `boss: true`. Then **level-up**: `progression.addXp` on a view of the player, apply `LEVEL_STAT_GROWTH` via a `statBlock` modifier with source `level`, `skills.points += SKILL_POINTS_PER_LEVEL`, full heal pulse, `refreshStats`, emit `LEVEL_UP {level}` (one per level gained) | dead monsters | player, drops, events |
| 12 | `pickups` (`sim/rewards.js`) | for each drop within `stats.pickupRadius` of the player: gold → `player.gold += n`; item → inventory first free slot (`RPG.ui.inventory.pickup` rules; full inventory leaves the drop and sets a one-shot `ui` hint). Interact (`F`) widens pickup range to `r+24`. Emit `PICKUP {kind, id}` | drops | player, drops |
| 13 | `player death` (`sim/rewards.js`) | if `player.dead` and not yet handled: `progress.deaths++`, apply `playerDeathPenalty` from the difficulty (`gold10` = lose 10% gold; `none`; others per `data/difficulties`), emit `PLAYER_DIED`; set `world.state = 'dead'` (a flag Game reads, section 3.1). The sim does not respawn by itself | player | events, flag |
| 14 | `cleanup` (`sim/sim.js`) | `world.despawnDead(world)` (pooled objects returned); drop `drops` older than their `ttl`; cap `fx.damageNumbers` at 40 and age them | all | lists |
| 15 | `areas` (`sim/sim.js` calling `world/areas.js`) | `areas.checkExits(world)` — if the player stands on an exit within 0.6 tiles and the target area `isUnlocked`: `areas.enter(world, toArea, fromId)` regenerates map (seed `hash(world.seed, areaId)`), repopulates, places the player on the arrival portal, clears `projectiles/effects/telegraphs/drops`, emits `AREA_CHANGED {from,to}`. Boss arena: exits locked while the boss is alive | player pos | area, lists |
| 16 | `end` (`sim/sim.js`) | `world.tick++; world.time += dt; progress.playtime += dt`; fx decay (`shake *= 0.9`) | | |

Notes that fix real ambiguities:

- **Damage to the player has exactly one entry point**, `RPG.sim.hurtPlayer(world, amount, info)` (defined in stage 10's
  file, used by stages 6, 8 and by the boss adapter's `damagePlayer`). This is where invulnerability, shield absorb, armor,
  reflect/thorns, flash, shake and the `HIT` event happen. Nobody else subtracts player HP.
- **Kills are detected, not signalled.** `combat.applyHit` only sets `dead`; the rewards stage scans `world.monsters` for
  `dead && !rewarded`. That keeps `weapons.js`, `skill-effects.js` and `boss.js` untouched.
- **Team field:** monsters and enemy projectiles are `team 1`, the player and player projectiles `team 0`. Enemy projectiles
  are filtered by that field in stage 8 so `weapons.updateProjectiles` (which hits monsters) must be called only with
  team-0 projectiles. Wave-4 unit W4-04 must confirm `updateProjectiles` ignores `team 1`, or keep enemy shots in
  `world.enemyProjectiles`. **Decision: use a separate array `world.enemyProjectiles`** (added by W4-01 in `core/state.js`);
  it avoids touching `world/weapons.js` and is rendered by `draw-entities`.
- **Determinism:** gameplay randomness uses `world.rng` (AI, combat), `world.lootRng` (drops), `world.areaRng(areaId)`
  (forked from `hash(seed, areaId)`). `fx.rng` is cosmetic only. Same seed + same intent stream = same state, which
  W4-14's headless test asserts.
- **Paused / menu:** `step` is not called at all (the loop's `shouldStep`). There is no `paused` flag inside `step`.
- **Entering an area during stage 15** means stages 1–14 already ran for the old area this tick; the next tick starts clean.

### 2.3 `createWorld` additions

`RPG.sim.createWorld(opts)` keeps its signature (`seed, mapId`) and gains: `difficulty` (id or config → `difficulty.resolve`),
`areaId` (default `'town'`; `mapId` still works for the test map used by `tests/sim`), `fresh` player from `player.create`
with a starting weapon (`sword` in slot 0, `hunting-bow` and `arc-staff` unlocked in slots 1–2 so acceptance step 5 works from
minute one; later gear may replace them), skills state from `skills.createSkillState()`, `world.grid = spatial.create(64)`,
`world.lootRng`, `world.telegraphs = []`, `world.enemyProjectiles = []`, `world.scratch`, and one-time
`RPG.world.skills.setExecutor(RPG.world.skillEffects.execute)`. When `areaId` is given the world is populated through
`areas.enter`; with only `mapId` it stays the empty test map so the existing tests are unchanged.

### 2.4 Rewards details

- XP per kill: `def.xp · diff.xpMultiplier · (1 + stats.xpGain)`; `difficulty.scaleMonster` already returns `xp` for the
  level, use that when the monster was spawned with `scaleFn`; `m.xp` is set at spawn by `areas.populate` so rewards read `m.xp`.
- `progression` operates on a plain object. The adapter is: `prog = createProgression({level: p.level, xp: p.xp,
  skillPointsGranted: p.skills.spentPoints + p.skills.points})`, `addXp`, write `level/xp` back. Skill points are tracked
  by the skills module (`points`, `spentPoints`); the adapter adds `SKILL_POINTS_PER_LEVEL` per level gained (and the extra
  every 5 levels if `progression` defines it — read `progression.js`, do not assume).
- Item pickup uses the same inventory rules as `ui/inventory.js`; to avoid two copies, W4-05 `require`s nothing: it calls
  `RPG.ui.inventory.pickup(player, item)` (a pure function on `player.inventory`). `ui/inventory.js` therefore loads
  **before** `sim/rewards.js` at run time only; at load time there is no dependency, so no `JS_ORDER` constraint.

## 3. `RPG.core.game` — state machine, wiring, autosave

### 3.1 States and transitions

```
 mainmenu --newGame()/loadFromSlot()--> playing <--resume()-- paused
    ^                                      |  |  ^
    |                                      |  |  +-- ui overlay closed
    +------------ quitToMenu() ------------+  +-- pause()/overlay opened/visibilitychange/error
                                           |
                       player dead  ----> dead --respawn()--> playing
                       final boss killed ----> victory --continue()--> playing | quitToMenu() --> mainmenu
```

`game.state` ∈ `mainmenu | playing | paused | dead | victory`. The simulation steps **only** in `playing` with no modal
overlay on the `ui.screens` stack (`shouldStep`). `paused` is entered by `Esc/P`, by `visibilitychange` (also
`loop.reset()` so no catch-up burst), by an overlay opening (the screens manager already makes overlays modal), and after a
caught sim exception. `dead` is set when the sim raises the player-dead flag; the death screen offers *Respawn in town*
(`respawn()`: full HP, area `town`, penalty already applied by the sim) and *Load last save*. `victory` is set when
`progress.bossesDefeated['warden-of-ash']` is first true; the screen offers *Keep playing* and *Main menu*; autosave runs first.

### 3.2 Shape

```js
RPG.core.game = {
  create(deps) -> game,          // deps: { canvas, ui?, renderer?, input?, storage?, audio?, clock?, scheduler? } — all injectable
  start(canvas) -> game          // browser entry used by main.js; builds real deps then create()
}
game = {
  state, world, settings, difficultyId,
  newGame({ seed, difficulty, name }) -> void,          // seed: uint32|string via Rng.seedFromString; default from clock only at the UI edge
  loadFromSlot(slot) -> {ok, code?, error?},            // 'auto'|'1'|'2'|'3'
  saveToSlot(slot) -> {ok, code?, error?},
  quickSave(), quickLoad(),                             // slot '1' (see below)
  exportSave(slot|current) -> {ok, filename, text},     // string only; Blob/download lives in persistence/files.js
  importSave(text) -> {ok, code?, error?},              // importJson -> saveGame(slot) -> loadFromSlot
  deleteSave(slot) -> {ok, error?},
  setDifficulty(idOrConfig), setPaused(bool), respawn(), quitToMenu(),
  tick(dt), render(alpha)                               // tick = one fixed step: intent -> RPG.sim.step; used by the loop and tests
  on(type, fn) -> off                                    // forwards RPG.core.events
}
```

- `quickSave` writes slot `'1'`; `quickLoad` reads slot `'1'`. (`'auto'` is reserved for autosave so a quick-save never
  overwrites it.) The save/load screen lists `auto, 1, 2, 3` with the `meta` block (level, area, difficulty, playtime).
- `tick(dt)`: `intent = input.buildFrame(renderer.camera, renderer.viewW, renderer.viewH)`; global toggles
  (`intent.toggles`) are consumed by Game/ui **before** the sim sees the intent: `pause`, overlay toggles → `ui.screens`
  `handleAction`, `quickSave/quickLoad` → Game, `debug` → renderer flag. The sim gets only move/aim/attack/skills/dash/
  switchWeapon/interact. Held movement is zeroed when an overlay is open.
- The loop is `RPG.core.createLoop({ step: game.tick, render: game.render, shouldStep })`. `game.render(alpha)` calls
  `renderer.draw(world, alpha, dt, uiState)` then `ui.screens.draw(ctx, …)` for the active overlays.
- Everything is injectable so `tests/core/game.test.js` runs `Game` headlessly with a fake storage, no canvas, a fake
  input returning scripted intents, and a manual `tick`.

### 3.3 Save/load wiring (acceptance steps 14–22)

Save document = `RPG.persistence.snapshot(world, game)` → `serialize` → `storage.saveGame(slot, body)`.
Load = `storage.loadGame(slot)` → `restore(doc)` → new world.

**Snapshot contract** (file `src/persistence/snapshot.js`; unit w03-16 owns it if it lands — otherwise W4-08 writes it
to this contract, and only one of them may). Fields map exactly to `SAVE-SCHEMA.md` and `persistence/save.js` `validate`:

| Save field | From | Restore |
|---|---|---|
| `seed`, `rngState {world, loot}` | `world.seed`, `world.rng.getState()`, `world.lootRng.getState()` | rebuild world from `seed`, then `setState` on each |
| `meta` | level, area id, difficulty id, `progress.playtime`, name | display only |
| `difficulty` | the **resolved config** (not just id) | `difficulty.resolve(config)` (keeps saved numbers) |
| `player` | `classId, level, xp, gold, hp, resource, pos, weaponSlots, activeWeapon, equipment, inventory, skills (skills.serialize), cooldowns` | `player.create`; set fields; `skills.restore` (drops unknown ids, refunds points); `equipment.applyAll`; `skills.applyPassives`; `refreshStats`; clamp `hp` to `maxHp` |
| `area` | `{id, visited, cleared}` | `areas.enter(world, id, null, {restorePos})`: regenerate from `hash(seed, areaId)`, skip cleared slots, put the player at `pos` (snap to nearest walkable tile if it is now in a wall) |
| `progress` | `bossesDefeated, areasUnlocked, kills, deaths` | copy |
| `settings` | `volume, sfx, muted, quality, showDamageNumbers, screenShake, keys` (non-default bindings only) | applied via `game.applySettings`; also stored on its own key so *Delete save* keeps it |

`area.cleared` needs the slot indexes of cleared encounters: `areas.enter` records `world.area.slotOf[monsterId]`; the
rewards stage adds `cleared[areaId].push(slot)` when the last monster of a slot dies. W4-06 owns this bookkeeping.

**Round trip rule (acceptance 18):** `snapshot → serialize → deserialize → restore → snapshot` must be byte-equal on the
gameplay fields. W4-14's test asserts it, and a second test saves, advances 600 ticks on both the original and the restored
world with the same scripted intent and asserts equal `player` state (monsters respawn, so compare the player only and
kill counts are excluded; documented in SAVE-SCHEMA: "uncleared fights respawn").

**Autosave** (slot `auto`): on `AREA_CHANGED`, `LEVEL_UP`, boss defeat, entering `victory`, and every 60 s of play when
no monster has been within aggro range of the player for 5 s. Implemented in `core/game.js` as event listeners plus a
timer on `world.time` (not wall clock). A failed write sets `game.lastError` and shows a one-line toast; it never throws.
`game.state` change to `paused`/`mainmenu` does not autosave (avoids saving a dead player).

**Export / delete / import** (steps 19–22): `exportSave` → `exportimport.exportJson(snapshot)` + `exportFileName`;
the browser download (Blob + `<a download>`) and the file picker (`FileReader`) are the only DOM in persistence and live in
the new file `src/persistence/files.js` (W4-08), loaded after `exportimport.js`. Import is validate → migrate →
`saveGame('1')`-style slot choice dialog → `loadFromSlot`. `deleteSave(slot)` → `storage.deleteSave`; the confirm prompt is
a screen in W4-13. All three must work with the network off; `check-offline` already guards the build.

### 3.4 Autoplay policy for audio

`audio.init()` is called by Game on the first `keydown`/`pointerdown` captured by `input.attach`, never at load.

## 4. Events → audio and UI

`core/events.js` is the only channel from sim to presentation. Subscribers are registered by `Game.create`; sim code never
imports audio or UI.

| Event (`NAMES`) | Emitted by | Audio (`RPG.audio.play`) | UI / render effect |
|---|---|---|---|
| `HIT` (`team 0` target) | stage 10 | `hit` (pitch +0.1 on `crit`) | damage number already in `fx.damageNumbers`; screen shake 0 |
| `HIT` (`team 1` target = player) | stage 10 | `hurt` | vignette flash, shake 6 px |
| `KILL` | stage 11 | `kill` | death particle burst at `x,y` (cosmetic, `fx.rng`); kill counter |
| `LEVEL_UP` | stage 11 | `levelup` | banner "Level N", skill-points badge on HUD |
| `PICKUP` | stage 12 | `pickup` | toast with the item name/rarity colour (gold: no toast, number only) |
| `ITEM_DROPPED` | stage 11 | none | drop beam for epic+ |
| `SKILL_USED` | stage 4 | `skill` | cooldown sweep from `hud` model |
| *(attack start)* | `weapons.tryAttack` returned true (stage 3) | `attack` | none. No new event name: stage 3 emits `UI_SOUND {name:'attack'}` |
| `BOSS_WARN` | stage 6 | `bossWarn` | boss bar flashes |
| `BOSS_PHASE` | stage 6 | `bossWarn` | phase banner |
| `PLAYER_DIED` | stage 13 | `hurt` (low pitch) | Game → `dead` state |
| `AREA_CHANGED` | stage 15 | `ui` | fade, area-name banner, autosave |
| `SAVE` / `LOAD` | Game | `ui` | toast "Saved"/"Loaded" |
| `UI_SOUND` | UI + stage 3/4 | `play(payload.name)` | none |

Payload convention for `UI_SOUND`: `{ name: <audio sound name>, pitch?, vol? }`.

The bridge is `src/audio/bridge.js` (`RPG.audio.bridge.attach(events, audio) -> detach`), a table of the rows above so the
mapping is data and testable with a fake `audio.play`. Toasts and banners are drawn by `ui/screens` `playing` handler from
a small queue fed by the same events (owned by W4-13, `ui/hud-overlays.js`). `drain()` is used only by tests and by the
debug overlay.

## 5. `JS_ORDER` decisions (`scripts/build.mjs`)

Current list vs disk. "Create" means a wave-4 unit writes it; "prune" means delete the entry. The list is edited **once**,
by W4-00, before anyone else starts, so order never races (new files not in the list are appended last with a warning, which
breaks load order).

| Entry | On disk | Decision | Why |
|---|---|---|---|
| `src/data/stats.js` | no | **prune** | stats live in `world/stats.js`; nothing needs a data table |
| `src/data/keymap.js` | no | **prune** | `input/keymap.js` holds defaults |
| `src/world/sim.js` | no | **prune** | the pipeline is `src/sim/sim.js` |
| `src/input/keyboard.js` | no | **prune** | folded into `input/raw.js` + `input/input.js` (ARCHITECTURE already says so) |
| `src/input/mouse.js` | no | **prune** | same; wheel support goes into `input/input.js` (W4-12) |
| `src/world/areas.js` | no | **keep, create** (W4-06) | enter/exits/gating |
| `src/render/draw-entities.js` | no | **keep, create** (W4-09) | monsters, projectiles, drops, player (move `drawPlayer` out of `renderer.js`) |
| `src/render/draw-effects.js` | no | **keep, create** (W4-10) | telegraphs, aoe rings, damage numbers, particles |
| `src/ui/character.js` | no | **keep, create** (W4-13b) | |
| `src/ui/skills-screen.js` | no | **keep, create** (W4-13b) | skill tree + respec |
| `src/ui/settings.js` | no | **keep, create** (W4-13c) | volume, quality, keys, difficulty |
| `src/ui/saveload.js` | no | **keep, create** (W4-13a) | slots, export, import, delete |
| `src/ui/pause.js` | no | **keep, create** (W4-13a) | |
| `src/ui/mainmenu.js` | no | **keep, create** (W4-13a) | new / continue / import |
| `src/core/game.js` | no | **keep, create** (W4-07) | |
| *missing* `src/sim/stages` files | — | **add**: `sim/combat-step.js`, `sim/monsters.js`, `sim/rewards.js` before `sim/player.js`? **No**: after `sim/player.js`, before `sim/sim.js` | stages register on `RPG.sim.stage.*`; only `sim.js` calls them |
| *missing* `src/persistence/snapshot.js` | w03-16 | **add** after `persistence/migrate.js` and `storage.js` | |
| *missing* `src/persistence/files.js` | no | **add** after `exportimport.js` | only DOM in persistence |
| *missing* `src/audio/bridge.js` | no | **add** after `audio/audio.js` | |
| *missing* `src/ui/hud-overlays.js` | no | **add** after `ui/inventory.js` | toasts, banners, death/victory screens |
| *missing* `src/world/skill-effects.js` | yes | already listed, keep after `world/skills.js` | |
| `src/data/maps/test-map.js` | yes | keep | still used by sim tests and as a fallback |
| `src/sim/player.js` | yes | order issue: it constructs `new RPG.world.StatBlock()` **at load time** (`BASE_STATS`), so `world/stats.js` must stay before it (it is) | none |
| `src/core/loop.js` | yes | currently after `ui/*`; fine (runtime-only dependency) | |
| `src/input/keymap.js` vs `src/ui/screens.js` | yes | `ui/screens.js` reads `RPG.input.keymap` at call time only | fine |
| `src/world/mapgen.js` before `src/world/world.js` | yes | fine | |

Resulting order (W4-00 writes exactly this; entries that do not exist yet are skipped by the build as before):

```
core/namespace util rng events pool entities state
data/maps/test-map data/difficulties weapons skills monsters items areas
world/stats tilemap collision spatial mapgen world combat weapons skills skill-effects ai boss loot equipment progression
      difficulty encounters areas
sim/player sim/combat-step sim/monsters sim/rewards sim/sim
persistence/save migrate storage exportimport snapshot files
input/raw keymap intent input
audio/audio audio/bridge
render/palette camera draw-shapes draw-entities draw-map draw-effects hud renderer
ui/screens inventory character skills-screen settings saveload pause mainmenu hud-overlays
core/loop core/game
main
```

Two build-script follow-ups for W4-00: (1) a check that fails (not warns) when a listed file is missing **after wave 4's
last unit** — wave 4 keeps the skip-missing behaviour while units land, and the final integration unit (W4-15) flips
`STRICT` on; (2) a check that fails when a file under `src/` is not in `JS_ORDER`. Both live only in `build.mjs` and the
`tests/scripts/` suite.

## 6. Wave-4 work items

Rules: every file below has exactly one owning unit; units may **read** anything, and may call the shared stage/context
contracts in section 2, but must not edit a file owned by another unit. If a unit needs a change in a file it does not own,
it records it as a follow-up in its breadcrumb. Test files are owned by the unit that owns the code they test. Docs in
`docs/` are owned by W4-16 only. `scripts/build.mjs` is owned by W4-00 only (and W4-15 for the strict flag after all others
finished).

Tiers (a unit starts when its dependencies are done; units in one tier are independent):

| Tier | Units |
|---|---|
| 0 | W4-00 |
| 1 | W4-01 … W4-12 incl. W4-09b (all parallel; each is tested with fakes against the section-2 contracts) |
| 2 | W4-13a/b/c (need the W4-13 screens extension), W4-14 (needs 01–08), W4-15 |
| 3 | W4-16 docs, W4-17 verify |

| ID | Kind | Owns (create/edit) | Does | Depends on | Serves |
|---|---|---|---|---|---|
| W4-00 | implement | `scripts/build.mjs`, `tests/scripts/build-order.test.js` | Replace `JS_ORDER` with the section-5 list; unlisted-file check as an error under `--verify`; test that the order has no duplicates and every listed file either exists or is on a short allow-list | — | A1 |
| W4-01 | implement | `src/sim/sim.js`, `src/core/state.js`, `tests/sim/sim.test.js`, `tests/sim/load.js`, `tests/core/core.test.js` (state part only) | `createWorld` additions (2.3), `step` per section 2.2 stages 0, 14, 15, 16, stage dispatch table that skips a stage whose `RPG.sim.stage.<name>` is absent (so units can land in any order), `world.scratch`, `lootRng`, `telegraphs`, `enemyProjectiles`, `areaRng` | W4-00 | A1 |
| W4-02 | implement | `src/sim/player.js`, `tests/sim/player.test.js` | Stages 1–4: intent sanitising, dash with i-frames, regen, weapon switch, `tryAttack`/`updateAttack`, skill casting + cooldown tick, `SKILL_USED`/`UI_SOUND` emits, `moveFactor` | W4-00, W4-12 (input fields; use `intent.switchWeapon \|\| false` until it lands) | A1, A2 |
| W4-03 | implement | `src/sim/monsters.js`, `tests/sim/monsters.test.js` | Stages 5–7: spatial rebuild, AI adapter (`think` → apply intent), boss adapter (separate `telegraphs`, `damagePlayer`, `spawnMonster`, `onPhaseChange` → `BOSS_PHASE`), movement/collision/separation, enemy melee and projectile spawn, exploder, summoner caps | W4-00 | A3 |
| W4-04 | implement | `src/sim/combat-step.js`, `tests/sim/combat-step.test.js` | Stages 8–10: projectile/effect updates in the right order, enemy projectiles, status ticks, `hurtPlayer` (`RPG.sim.hurtPlayer`), `HIT` events, shake/flash | W4-00 | A1 |
| W4-05 | implement | `src/sim/rewards.js`, `tests/sim/rewards.test.js` | Stages 11–13: kill detection, xp/gold/loot, level-up adapter, pickups, boss-death flag, death penalty | W4-00 | A1, A9 |
| W4-06 | implement | `src/world/areas.js`, `tests/world/areas.test.js` | `enter` (mapgen with area RNG, `encounters.compose`, `spawnMonster` with `difficulty.scaleMonster` as `scaleFn`, boss + `bossModifiers`, portal arrival, list clearing, `AREA_CHANGED`), `checkExits`, `markBossDefeated`, `isUnlocked` (town → forest → caves → arena gating), `cleared` slot bookkeeping, restore-at-position | W4-00 | A3 |
| W4-07 | implement | `src/core/game.js`, `tests/core/game.test.js`, `tests/core/game-load.js` | Section 3: state machine, newGame/load/save/quick/export/import/delete/setDifficulty, autosave rules, `shouldStep`, try/catch around `step`, settings apply, event subscriptions (bridge attach) | W4-00 | A1, A9 |
| W4-08 | implement | `src/persistence/snapshot.js` (only if absent), `src/persistence/files.js`, `tests/persistence/snapshot.test.js`, `tests/persistence/files.test.js` | Section 3.3 snapshot/restore to contract; Blob download and `FileReader` import with injected DOM fakes; round-trip tests | W4-00 | A9 |
| W4-09 | implement | `src/render/draw-entities.js`, `tests/render/entities.test.js` | `drawPlayer` (moved out of renderer), `drawMonster` per `def.shape`, `drawProjectile`, `drawDrop`, elite outline/affix tint, hp bars for elites/boss, status tints, team colours from `palette`; uses `render.shapes`; interpolates with `prevX/prevY` when present | W4-00 | A1 |
| W4-09b | implement | `src/audio/bridge.js`, `src/audio/audio.js`, `tests/audio/bridge.test.js`, `tests/audio/audio.test.js` | Section 4 table as data; add the `hurt` sound; pitch variation on crit; no-op when audio failed/muted | W4-00 | A1 |
| W4-10 | implement | `src/render/draw-effects.js`, `tests/render/effects.test.js` | telegraph shapes (circle/line/cone, filling as `t/duration`), aoe rings, whirl, nova, damage numbers (cap 40), particle bursts from `fx.rng` (cap by quality), screen-edge hurt flash | W4-00 | A1 |
| W4-11 | implement | `src/render/renderer.js`, `src/render/hud.js`, `src/render/camera.js`, `tests/render/renderer.test.js`, `tests/render/hud.test.js`, `tests/render/camera.test.js` | New draw order: map → drops → telegraphs/effects (below entities) → enemy projectiles → monsters → player → player projectiles → effects (above) → damage numbers → HUD; camera shake from `fx.shake`; HUD adds boss bar, minimap off by default, debug fps line, toasts hook; `renderer.draw(world, alpha, dt, ui)`; hand `drawPlayer` to `draw-entities` | W4-00 (calls W4-09/10 modules by name, guarded) | A1 |
| W4-12 | implement | `src/input/keymap.js`, `src/input/intent.js`, `src/input/input.js`, `src/input/raw.js`, `tests/input/input.test.js` | add actions `switchWeapon` (Tab / wheel), `interact` (F), `intent.switchWeapon` (-1/0/+1) and `intent.interact`; `Shift+Tab`; ignore keys while a text field has focus; keep existing frame shape; pointer capture on the canvas | W4-00 | A2 |
| W4-13 | implement | `src/ui/screens.js`, `src/ui/hud-overlays.js`, `tests/ui/screens.test.js`, `tests/ui/overlays.test.js` | add `dead`, `victory` ids; `handleAction`; toast/banner queue fed by events; death and victory screens; a `register` that rejects unknown ids | W4-00 | A1 |
| W4-13a | implement | `src/ui/mainmenu.js`, `src/ui/pause.js`, `src/ui/saveload.js`, `tests/ui/menus.test.js` | main menu (new game with difficulty + seed field, continue, import), pause menu (resume, save, load, settings, quit), save/load screen (slots with meta, save/load/export/import/delete with confirm) | W4-13 (id list; fake `register` until it lands) | A9 |
| W4-13b | implement | `src/ui/character.js`, `src/ui/skills-screen.js`, `tests/ui/character.test.js`, `tests/ui/skills-screen.test.js` | character sheet (stats, equipment, resistances); skill tree view with learn/upgrade/equip-to-bar/respec via `world/skills.js` | W4-13 | A2 |
| W4-13c | implement | `src/ui/settings.js`, `tests/ui/settings.test.js` | volume/mute, quality, damage numbers, shake, key rebinding (via `input.setBinding`), **difficulty selector incl. custom sliders** (acceptance step 13) | W4-13 | A1 |
| W4-14 | test | `tests/integration/headless.test.js`, `tests/integration/helpers.js`, `tests/integration/save-roundtrip.test.js` | scripted 60 s run (no exceptions, monsters die, XP grows, a level is gained), determinism (two runs same seed equal), boss fight scripted with god-mode player (boss dies, `bossesDefeated` set, epic drop), area transitions, save→load→continue equality, export→delete→import→continue (acceptance 14–22) | W4-01…W4-08 | A1, A9 |
| W4-15 | implement | `src/main.js`, `index.html`, `styles/main.css`, `scripts/build.mjs` (STRICT flag only), `tests/scripts/build-strict.test.js` | `main.js` becomes `RPG.core.game.start(canvas)`; canvas full-window; strict `JS_ORDER`; `npm run check-all` green | W4-01…W4-13c | A1, A9 |
| W4-16 | document | `docs/ARCHITECTURE.md`, `docs/GAMEPLAY-SYSTEMS.md`, `docs/TEST-CHECKLIST.md`, `README.md`, `docs/INTEGRATION-PLAN.md` (mark done) | fold section 1 table into ARCHITECTURE, describe only what ships | W4-15 | A1 |
| W4-17 | verify | none | independent pass over the 22 steps in section 8 in a built `dist/index.html` using a scripted headless harness; report only | W4-16 | A1, A9 |

Disjointness check performed: no path appears twice in the Owns column except `scripts/build.mjs` (W4-00 first, W4-15
STRICT flag later, strictly sequential by dependency), `tests/core/core.test.js` (W4-01 only; W4-07 uses
`tests/core/game.test.js`) and `tests/sim/load.js` (W4-01 only; W4-02..05 each use their own `tests/sim/<stage>.test.js` and
build their VM context from `RPG.sim.stage` fakes, never edit `load.js`, and note the need in a follow-up). The shipped
`tests/sim/index.js` is owned by W4-01. Everything under `tests/integration/` is W4-14. `tests/world/load.js` is **not**
modified by wave 4 except by W4-06 if `areas.js` needs to be listed — W4-06 owns it and no other unit edits it.

Ordering hazards to hand to each owner:

1. `boss.js` reassigns `world.effects` (M12). W4-03 must test with the adapter and assert the real `world.effects` array
   identity is unchanged after a boss tick.
2. `skills.tryCastSkill` silently does nothing without `setExecutor` (M10). W4-01 tests that `createWorld` registers it.
3. `weapons.tryAttack` returns false (no throw) for unimplemented behaviours; W4-02 must not treat that as an error.
4. Pooled monsters keep stale `m.ai`/`rewarded`/`fight` fields. `world.js` must reset them on `acquire`; W4-03/05 must not
   assume fresh objects. If the pool `reset` does not clear them, record a follow-up for the `world.js` owner (wave 3 closed;
   assign to W4-01 as the only editor of `world.js`: **W4-01 also owns `src/world/world.js` and `tests/world/world.test.js`
   for this one reset fix**).
5. `ui/screens.js` is canvas-only: popups needing text entry (seed, import paste) use `window.prompt` through an injected
   `ui.prompt(label, cb)` so tests stay headless. That function is W4-13's.

## 7. Dependencies on unfinished wave-3 units

- If `persistence/snapshot.js` has landed when wave 4 starts, W4-08 skips it and only adds `files.js`; it checks the
  snapshot against section 3.3 and records deviations as follow-ups instead of rewriting it.
- If w03-17 (tests for loop/pool/camera/renderer) has landed, W4-11 extends `tests/render/camera.test.js` and
  `renderer.test.js`; otherwise it creates them. `tests/core/loop.test.js` and `pool.test.js` are never touched by wave 4.
- Uncommitted wave-3 files (`render/draw-shapes.js`, `ui/inventory.js`, `world/skill-effects.js`, `world/weapons.js` and
  their tests, a modified `world/skills.js` and `scripts/build.mjs`) must be committed on the work branch by the first wave-4
  git step before W4-00 edits `build.mjs`, otherwise W4-00's diff mixes two units. The overseer decides who commits; this plan
  only flags it.

## 8. Prompt section 28 — which module satisfies each step

| # | Step | Satisfied by | Notes / gap today |
|---|---|---|---|
| 1 | Open the game locally | `scripts/build.mjs` → `dist/index.html` (single file, `file://`); `index.html`; W4-15 `main.js` | Works today for the movement demo. `check-offline` guards it. |
| 2 | Start a new game | `ui/mainmenu.js` (W4-13a) → `core/game.js` `newGame` (W4-07) → `sim.createWorld` + `areas.enter('town')` (W4-01/06) | `createWorld` exists; menu and game do not |
| 3 | Move around a 2D world | `input/*` → `sim/player.js` step (W4-02) → `world/collision.js`; `render/renderer.js` + `draw-map.js` | **Done today** (wave 1). Areas add real maps (`world/mapgen.js`, `data/areas.js`) |
| 4 | Fight monsters | `sim/monsters.js` (W4-03), `world/ai.js`, `world/combat.js`, `sim/combat-step.js` (W4-04), `render/draw-entities.js` (W4-09) | modules exist, none called by the sim yet |
| 5 | Use different weapons | `world/weapons.js` + `data/weapons.js` (3 weapons), `sim/player.js` switch (W4-02), `input` Tab/wheel (W4-12), HUD weapon label | `switchWeapon` exists; input action missing |
| 6 | Offensive, defensive, regenerative skills | `world/skills.js` + `world/skill-effects.js` + `data/skills.js` (fireball, whirlwind, ground-slam, frost-nova, shield, mend, blink-strike …); cast in `sim/player.js` (W4-02); `render/draw-effects.js` (W4-10) | skills are castable in unit tests only today |
| 7 | Gain XP | `sim/rewards.js` (W4-05), `world/progression.js`, `data/monsters.js` `xp`, HUD XP bar | `progression` exists; not called |
| 8 | Level up | `sim/rewards.js` level-up adapter (`LEVEL_STAT_GROWTH` via `StatBlock` modifier, points, heal), `LEVEL_UP` event → banner (W4-13) + audio bridge | |
| 9 | Spend skill points | `ui/skills-screen.js` (W4-13b) → `skills.learnSkill/upgradeSkill/equipSkill/respec` | model done, screen missing |
| 10 | Obtain and equip loot | drops: `world/loot.js` via `sim/rewards.js`; pickup: `sim/rewards.js` stage 12; equip: `ui/inventory.js` + `world/equipment.js`; stats recompute through `StatBlock` | inventory screen exists; drops never spawn today |
| 11 | Fight increasingly difficult enemies | `data/areas.js` levels 1→8 along `order`; `world/areas.js` (W4-06) with `world/encounters.js` and `world/difficulty.scaleMonster`; area gating | |
| 12 | Defeat at least one boss | `warden-of-ash` in `data/monsters.js`; `world/ai.js` boss behaviour + `world/boss.js` via the adapter in `sim/monsters.js` (W4-03); arena in `data/areas.js`; boss loot in `sim/rewards.js`; victory in `core/game.js` | integration risk M12 |
| 13 | Change difficulty | `ui/settings.js` (W4-13c) and main menu → `game.setDifficulty` → `world/difficulty.resolve`; applies to new spawns; saved as the resolved config | live monsters keep their stats (documented) |
| 14 | Save the game | `game.saveToSlot` → `persistence/snapshot.js` → `save.serialize` → `storage.saveGame`; autosave in `core/game.js`; `ui/saveload.js` | persistence layer tested; no snapshot/wiring today |
| 15 | Close the browser | Nothing runs. Save is in `localStorage` (`persistence/storage.js`); the in-memory fallback is warned about on the save screen | A `beforeunload` autosave is deliberately **not** used (could save a dead/paused state) |
| 16 | Reopen with no internet | `dist/index.html` has no network references (`check-offline.mjs`, `tests/scripts`); `core/game.js` start never fetches | guard exists |
| 17 | Load the previous game | main menu *Continue* and `ui/saveload.js` → `game.loadFromSlot` → `storage.loadGame` (recovers `:tmp`) → `snapshot.restore` | |
| 18 | Continue exactly where they left off | `snapshot.restore` + `areas.enter(..., {restorePos})` with the area regenerated from `hash(seed, areaId)`; W4-14 round-trip tests | uncleared fights respawn; boss restarts (SAVE-SCHEMA) |
| 19 | Export the save | `exportimport.exportJson` / `exportFileName`; `persistence/files.js` download (W4-08); button on `ui/saveload.js` | string functions exist; download missing |
| 20 | Delete the local save | `storage.deleteSave`; confirm in `ui/saveload.js`; settings key survives | exists at storage level |
| 21 | Import the exported save | `files.js` picker → `exportimport.importJson` (validate + migrate) → slot choice → `saveGame` | validation exists |
| 22 | Continue playing from the restored state | same path as 17/18; W4-14 `export → delete → import → continue` test | |

## 9. Risks and open points for the overseer

1. **No live-play evidence yet.** Nothing has been run in a browser; every pipeline claim is from reading the code.
   W4-17 must run the built `dist/index.html` (headless harness is acceptable, a real browser check is better).
2. **Boss adapter (M12) and pooled effects** are the likeliest source of wave-4 bugs: two effect arrays, one reassigned.
   The plan avoids touching `boss.js`; if W4-03 finds the adapter cannot work, the fallback is a small fix to `boss.js`
   owned by W4-03 (allowed as the single exception to "wave-3 files closed").
3. **Weapon balance and "fun"** (A1.7) cannot be judged by the plan. Reserve a post-integration tuning unit in wave 5.
4. **Touch/Android** (`input/touch.js`) stays out; the plan keeps `intent` shape stable so it can be added.
5. `ARCHITECTURE.md` still carries stale text until W4-16. Units must follow this plan and the code, not that file, where
   they differ.
6. The `Game` quick-save slot choice (`'1'`) is a design decision made here; the owner may prefer a dedicated `quick` slot,
   which needs `storage.js` slot validation to accept it (not wave-4 scope).

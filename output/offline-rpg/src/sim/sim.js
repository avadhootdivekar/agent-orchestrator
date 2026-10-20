/* Simulation entry: builds a world on a map and advances it one fixed tick. Owns all mutation (ARCHITECTURE §6).
   `step` is a fixed-order stage pipeline (INTEGRATION-PLAN §2.2): each stage lives in RPG.sim.stage.<name>(world, ctx),
   registered by its own file, and a stage that is not present is skipped so units can land in any order. */
(function (RPG) {
  'use strict';

  RPG.sim = RPG.sim || {};
  RPG.sim.stage = RPG.sim.stage || {};

  // Fixed execution order. Numbers are the plan's stage numbers (0 begin ... 16 end).
  var ORDER = ['begin', 'input', 'player', 'weapons', 'skills', 'spatial', 'monsters', 'movement', 'projectiles',
    'statuses', 'affixes', 'resolve', 'rewards', 'pickups', 'playerDeath', 'cleanup', 'areas', 'end'];

  var DAMAGE_NUMBER_CAP = 40;
  var DAMAGE_NUMBER_LIFE = 1.2;  // seconds

  // Plan §2.3: sword in slot 0, bow and staff in slots 1-2 so every weapon is usable from minute one.
  var STARTING_WEAPONS = ['sword', 'hunting-bow', 'arc-staff'];

  // Loot items carry baseId only and world/weapons.js resolves weaponId/defId, so the grant sets weaponId itself.
  // A private Rng keeps the world's streams untouched.
  function grantStartingWeapons(player) {
    var rng = RPG.core.Rng.create(1);
    for (var i = 0; i < STARTING_WEAPONS.length; i++) {
      if (player.weaponSlots[i]) continue;
      var item = RPG.world.loot.generateItem(rng, { baseId: STARTING_WEAPONS[i], rarity: 'common', ilvl: 1 });
      item.weaponId = STARTING_WEAPONS[i];
      player.weaponSlots[i] = item;
    }
  }

  /** World on the named map (RPG.data.maps), player standing on its spawn tile.
   *  opts: { seed, mapId, areaId, difficulty (id or config) }; a bare string is shorthand for { areaId }.
   *  With `areaId` the world is built through areas.enter (generated map, populated); with only `mapId` it stays the
   *  empty hand-authored test map. */
  function createWorld(opts) {
    if (typeof opts === 'string') opts = { areaId: opts };
    opts = opts || {};
    var def = RPG.data.maps[opts.mapId || 'test-map'];
    var tm = RPG.world.tilemap;
    var map = tm.fromRows(def.layout, def.tileSize);
    map.theme = def.theme;
    var world = RPG.core.state.createWorldState({ seed: opts.seed });
    if (opts.difficulty) world.difficulty = RPG.world.difficulty.resolve(opts.difficulty);
    var s = tm.findTile(map, tm.TILE.SPAWN) || { tx: 1, ty: 1 };
    var spawn = { x: (s.tx + 0.5) * map.tileSize, y: (s.ty + 0.5) * map.tileSize };
    world.area = { id: def.id, map: map, spawn: spawn, exits: [], visited: {} };
    world.area.visited[def.id] = true;
    world.player = RPG.sim.player.create(world, spawn.x, spawn.y);

    // Non-saved helpers (transient, rebuilt on every createWorld).
    var seed = world.seed;
    world.areaRng = function (areaId) { return RPG.core.Rng.create(RPG.core.Rng.hashString(seed + ':' + areaId)); };
    world.scratch = { intent: null, dt: 0, events: [], hits: [], killed: [], spatial: null, bossWorld: null, actions: null };
    if (RPG.world.spatial) world.grid = RPG.world.spatial.create(64);
    // skills.tryCastSkill does nothing without an executor (INTEGRATION-PLAN M10).
    if (RPG.world.skills && RPG.world.skillEffects) RPG.world.skills.setExecutor(RPG.world.skillEffects.execute);
    if (RPG.world.loot) grantStartingWeapons(world.player);
    if (opts.areaId) RPG.world.areas.enter(world, opts.areaId, { silent: true });
    return world;
  }

  // ---- Stage 0: begin ----
  function begin(world, ctx) {
    ctx.events.length = 0; ctx.hits.length = 0; ctx.killed.length = 0;
    ctx.actions = null; ctx.spatial = null; ctx.bossWorld = null;
  }

  // ---- Stage 14: cleanup ----
  function cleanup(world, ctx) {
    var dt = ctx.dt, i, d;
    for (i = 0; i < world.drops.length; i++) {
      d = world.drops[i];
      d.age = (d.age || 0) + dt;
      if (d.ttl !== undefined && d.ttl !== null && d.age > d.ttl) d.dead = true;
    }
    // Cosmetic particles read this tick's hits and fresh deaths, so they step before dead monsters are removed.
    if (RPG.render && RPG.render.effects && RPG.render.effects.stepWorldFx) RPG.render.effects.stepWorldFx(world, ctx);
    if (RPG.world.capEffects) RPG.world.capEffects(world);
    if (RPG.world.despawnDead) RPG.world.despawnDead(world);
    var nums = world.fx && world.fx.damageNumbers;
    if (nums) {
      for (i = nums.length - 1; i >= 0; i--) {
        nums[i].age = (nums[i].age || 0) + dt;
        if (nums[i].age > DAMAGE_NUMBER_LIFE) nums.splice(i, 1);
      }
      if (nums.length > DAMAGE_NUMBER_CAP) nums.splice(0, nums.length - DAMAGE_NUMBER_CAP);
    }
  }

  // ---- Stage 15: areas ----
  function areas(world) {
    if (RPG.world.areas && RPG.world.areas.checkExits) RPG.world.areas.checkExits(world);
  }

  // ---- Stage 16: end ----
  function end(world, ctx) {
    world.tick++;
    world.time += ctx.dt;
    world.progress.playtime += ctx.dt;
    if (world.fx) world.fx.shake *= 0.9;
  }

  RPG.sim.stage.begin = RPG.sim.stage.begin || begin;
  RPG.sim.stage.cleanup = RPG.sim.stage.cleanup || cleanup;
  RPG.sim.stage.areas = RPG.sim.stage.areas || areas;
  RPG.sim.stage.end = RPG.sim.stage.end || end;

  /** One fixed tick. `intent` is plain data (input/intent.js shape), so scripted intents replay exactly. */
  function step(world, intent, dt) {
    if (!(dt > 0)) return;
    var ctx = world.scratch;
    if (!ctx) ctx = world.scratch = { intent: null, dt: 0, events: [], hits: [], killed: [], spatial: null, bossWorld: null, actions: null };
    ctx.intent = intent; ctx.dt = dt;
    var stages = RPG.sim.stage;
    for (var i = 0; i < ORDER.length; i++) {
      var fn = stages[ORDER[i]];
      if (typeof fn === 'function') fn(world, ctx);
      // Until the real player stage lands, movement runs through the player module directly.
      else if (ORDER[i] === 'player') RPG.sim.player.step(world, intent, dt);
    }
  }

  RPG.sim.ORDER = ORDER;
  RPG.sim.createWorld = createWorld;
  RPG.sim.step = step;
})(globalThis.RPG = globalThis.RPG || {});

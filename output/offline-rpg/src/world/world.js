/* World container (ARCHITECTURE §4 "world"): id allocation and spawn/despawn of monsters, projectiles, effects
   and item drops on a core/state.js world. Monsters, projectiles and effects come from per-world object pools
   (core/pool.js) and are recycled by despawnDead; pools are transient, never serialized. Headless: no DOM. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  // Elite multipliers applied on top of level scaling.
  var ELITE = { hp: 2.5, damage: 1.5, radius: 1.15 };

  var pools = new WeakMap();   // world -> { monster, projectile, effect }

  function clearKeys(o) { for (var k in o) delete o[k]; }

  function poolsOf(world) {
    var p = pools.get(world);
    if (!p) {
      p = {
        monster: RPG.core.Pool.create(function () { return {}; }, clearKeys),
        projectile: RPG.core.Pool.create(function () { return {}; }, clearKeys),
        effect: RPG.core.Pool.create(function () { return {}; }, clearKeys)
      };
      pools.set(world, p);
    }
    return p;
  }

  /** Allocates the next entity id from the world's counter (survives save/load via `nextEntityId`). */
  function nextId(world) { return world.nextEntityId++; }

  /** Default level scaling: stat * perLevel[stat]^(level-1); stats without a perLevel entry stay flat. */
  function defaultScale(def, level) {
    var n = Math.max(0, level - 1), pl = def.perLevel || {}, b = def.baseStats;
    return {
      hp: b.hp * Math.pow(pl.hp || 1, n),
      damage: b.damage * Math.pow(pl.damage || 1, n),
      speed: b.speed,
      armor: b.armor
    };
  }

  /**
   * opts: { elite, affixes, level, summonerId, scaleFn(def, level, opts) -> {hp,damage,speed,armor} }.
   * `scaleFn` replaces the default scaling; elite multipliers are applied afterwards either way.
   */
  function spawnMonster(world, defId, x, y, opts) {
    opts = opts || {};
    var def = RPG.data.monsters[defId];
    if (!def) throw new Error('spawnMonster: unknown monster definition ' + defId);
    var level = Math.max(1, opts.level || 1);
    var s = (opts.scaleFn || defaultScale)(def, level, opts);
    var elite = !!opts.elite;
    var hp = Math.round(s.hp * (elite ? ELITE.hp : 1));
    var damage = s.damage * (elite ? ELITE.damage : 1);

    var m = poolsOf(world).monster.acquire();
    clearKeys(m);   // pooled objects must not carry stale ai / rewarded / fight state into the new monster
    var base = RPG.core.entities.baseEntity('monster', x, y, def.radius * (elite ? ELITE.radius : 1), 1);
    for (var k in base) m[k] = base[k];
    m.id = nextId(world);
    m.hp = hp; m.maxHp = hp;
    m.defId = defId; m.level = level; m.elite = elite;
    m.affixes = (opts.affixes || []).slice();
    m.aiState = 'idle'; m.aiT = 0; m.target = null; m.phase = 0;
    m.home = { x: x, y: y };
    m.summoned = 0;
    m.summonerId = opts.summonerId === undefined ? null : opts.summonerId;
    m.speed = s.speed;
    m.damage = damage;
    m.stats = new RPG.world.StatBlock({ maxHp: hp, damage: damage, moveSpeed: s.speed, armor: s.armor });
    // m.ai (behaviour scratch state) is created lazily by world/ai.js on the first think().
    world.monsters.push(m);
    return m;
  }

  /** spec: ProjectileSpec (DATA-DEFINITIONS §9). Returns a pooled Projectile. */
  function spawnProjectile(world, spec) {
    var p = poolsOf(world).projectile.acquire();
    var angle = spec.angle || 0, speed = spec.speed || 0;
    var base = RPG.core.entities.baseEntity('projectile', spec.x, spec.y, spec.radius === undefined ? 4 : spec.radius, spec.team);
    for (var k in base) p[k] = base[k];
    p.id = nextId(world);
    p.vx = Math.cos(angle) * speed; p.vy = Math.sin(angle) * speed; p.facing = angle;
    p.angle = angle; p.speed = speed;
    p.lifetime = spec.lifetime === undefined ? 1 : spec.lifetime; p.age = 0;
    p.damage = spec.damage || null;
    p.pierce = spec.pierce || 0; p.bounce = spec.bounce || 0; p.homing = spec.homing || 0;
    p.onHit = spec.onHit || 'none'; p.aoeRadius = spec.aoeRadius || 0;
    p.shape = spec.shape || 'orb'; p.color = spec.color || null; p.trail = !!spec.trail;
    p.ownerId = spec.ownerId === undefined ? null : spec.ownerId;
    p.hit = [];
    world.projectiles.push(p);
    return p;
  }

  /** spec: EffectSpec (DATA-DEFINITIONS §9). Returns a pooled Effect. */
  function spawnEffect(world, spec) {
    var e = poolsOf(world).effect.acquire();
    var base = RPG.core.entities.baseEntity('effect', spec.x, spec.y, spec.r === undefined ? 0 : spec.r, spec.team);
    for (var k in base) e[k] = base[k];
    e.id = nextId(world);
    e.type = spec.type; e.duration = spec.duration === undefined ? 1 : spec.duration; e.t = 0;
    e.color = spec.color || null;
    e.damage = spec.damage || null;
    e.delayDamageAt = spec.delayDamageAt === undefined ? null : spec.delayDamageAt;
    e.tickEvery = spec.tickEvery === undefined ? null : spec.tickEvery;
    e.text = spec.text === undefined ? null : spec.text;
    e.ownerId = spec.ownerId === undefined ? null : spec.ownerId;
    world.effects.push(e);
    return e;
  }

  /** Drops are plain (unpooled) data: they hold an item instance until picked up. */
  function spawnItemDrop(world, item, x, y) {
    var d = RPG.core.entities.baseEntity('drop', x, y, 12, 0);
    d.id = nextId(world);
    d.item = item;
    d.age = 0;
    world.drops.push(d);
    return d;
  }

  function sweep(list, pool) {
    var removed = 0;
    for (var i = list.length - 1; i >= 0; i--) {
      if (!list[i].dead) continue;
      var e = RPG.core.util.removeAt(list, i);
      if (pool) pool.release(e);
      removed++;
    }
    return removed;
  }

  /** Removes every `dead` monster/projectile/effect/drop; pooled ones go back to their pool. Returns the count removed. */
  function despawnDead(world) {
    var p = poolsOf(world);
    return sweep(world.monsters, p.monster) + sweep(world.projectiles, p.projectile) +
      sweep(world.effects, p.effect) + sweep(world.drops, null);
  }

  /** Calls fn(monster) for each living monster. Safe against fn marking monsters dead (not against spawning). */
  function forEachMonster(world, fn) {
    var l = world.monsters;
    for (var i = 0; i < l.length; i++) if (!l[i].dead) fn(l[i]);
  }

  function livingMonsters(world) { return world.monsters.filter(function (m) { return !m.dead; }); }

  /** Calls fn(entity) for every entity in the world: player, monsters, projectiles, drops, effects. */
  function forEachEntity(world, fn) {
    if (world.player) fn(world.player);
    [world.monsters, world.projectiles, world.drops, world.effects].forEach(function (l) {
      for (var i = 0; i < l.length; i++) fn(l[i]);
    });
  }

  function findEntity(world, id) {
    if (world.player && world.player.id === id) return world.player;
    var lists = [world.monsters, world.projectiles, world.drops, world.effects];
    for (var j = 0; j < lists.length; j++)
      for (var i = 0; i < lists[j].length; i++) if (lists[j][i].id === id) return lists[j][i];
    return null;
  }

  /** Pool diagnostics (for tests): active/free counts per pooled kind. */
  function poolStats(world) {
    var p = poolsOf(world), out = {};
    for (var k in p) out[k] = { active: p[k].active.length, free: p[k].freeCount() };
    return out;
  }

  RPG.world.nextId = nextId;
  RPG.world.spawnMonster = spawnMonster;
  RPG.world.spawnProjectile = spawnProjectile;
  RPG.world.spawnEffect = spawnEffect;
  RPG.world.spawnItemDrop = spawnItemDrop;
  RPG.world.despawnDead = despawnDead;
  RPG.world.forEachMonster = forEachMonster;
  RPG.world.livingMonsters = livingMonsters;
  RPG.world.forEachEntity = forEachEntity;
  RPG.world.findEntity = findEntity;
  RPG.world.poolStats = poolStats;
})(globalThis.RPG);

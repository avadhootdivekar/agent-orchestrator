/* Monster stages of the per-tick pipeline (INTEGRATION-PLAN §2.2 stages 5-7). Headless: no DOM, no clock, no Math.random.
     RPG.sim.stage.spatial(world, ctx)   5  rebuild world.grid (player + living monsters), count summons per summoner
     RPG.sim.stage.monsters(world, ctx)  6  ai.think -> intent, applied: desired velocity, telegraphs, melee, enemy
                                            projectiles, summons (capped), exploder; bosses go through world/boss.js
     RPG.sim.stage.movement(world, ctx)  7  integrate desired + knockback velocity through the tilemap, decay knockback,
                                            separate packs
   `ctx` is world.scratch ({ dt, events, ... }); only `ctx.dt` is required. Stages never throw on missing optional data.
   Boss adapter (M12): boss.js reassigns `world.effects`, so it is handed `ctx.bossWorld`, whose `effects` is the separate
   array `world.telegraphs`. The pooled `world.effects` array is never passed to it. All damage to the player goes
   through RPG.sim.hurtPlayer (combat-step); a small fallback is used until that stage is loaded. */
(function (RPG) {
  'use strict';

  RPG.sim = RPG.sim || {};
  RPG.sim.stage = RPG.sim.stage || {};

  var GRID_CELL = 64;
  var MELEE_HALF_ARC = 0.9;          // radians either side of the attack direction that a melee swing covers
  var SUMMON_CAP_DEFAULT = 4;        // living minions per summoner when its def has no aiParams.summonMax
  var BOSS_ADD_CAP = 6;              // living adds a boss may have at once
  var MAX_MONSTERS = 150;            // hard ceiling on living monsters; summons stop here
  var SUMMON_RING_GAP = 30;          // minions appear this far outside the summoner's body
  var KNOCKBACK_DECAY = 0.85;        // per-1/60 s multiplier on knockback velocity
  var KNOCKBACK_EPS = 1;             // px/s below which knockback is zeroed
  var SEPARATION_PUSH = 0.5;         // fraction of an overlap a monster resolves itself each tick
  var DISABLING = ['stun', 'freeze'];  // statuses that stop a monster thinking and moving under its own power

  function dist(ax, ay, bx, by) { var dx = bx - ax, dy = by - ay; return Math.sqrt(dx * dx + dy * dy); }
  function defOf(m) { return m.def || RPG.data.monsters[m.defId]; }

  function ctxOf(world, ctx) {
    if (!ctx) ctx = world.scratch || (world.scratch = {});
    if (typeof ctx.dt !== 'number') ctx.dt = 0;
    return ctx;
  }

  function emit(type, payload) {
    if (RPG.core && RPG.core.events) RPG.core.events.emit(type, payload);
  }

  function disabled(m) {
    var c = RPG.world.combat;
    for (var i = 0; i < DISABLING.length; i++) if (c.hasStatus(m, DISABLING[i])) return true;
    return false;
  }

  /** Single entry for damaging the player: the combat-step version when loaded, else a minimal equivalent. */
  function hurt(world, amount, info) {
    if (typeof RPG.sim.hurtPlayer === 'function') return RPG.sim.hurtPlayer(world, amount, info);
    var p = world.player, c = RPG.world.combat;
    if (!p || p.dead || amount <= 0 || c.hasStatus(p, 'invuln')) return 0;
    var dealt = amount * (1 - c.armorMitigation(p.stats ? p.stats.armor : 0));
    p.hp -= dealt;
    p.flash = Math.max(p.flash || 0, c.HIT_FLASH);
    if (p.hp <= 0) { p.hp = 0; p.dead = true; }
    return dealt;
  }

  function livingAddCount(world, summonerId) {
    var n = 0, list = world.monsters;
    for (var i = 0; i < list.length; i++) if (!list[i].dead && list[i].summonerId === summonerId) n++;
    return n;
  }

  function livingTotal(world) {
    var n = 0, list = world.monsters;
    for (var i = 0; i < list.length; i++) if (!list[i].dead) n++;
    return n;
  }

  // ---- stage 5 -----------------------------------------------------------------------------------------------------

  function spatialStage(world, ctx) {
    ctx = ctxOf(world, ctx);
    var sp = RPG.world.spatial;
    if (!world.grid) world.grid = sp.create(GRID_CELL);
    sp.clear(world.grid);
    if (world.player && !world.player.dead) sp.insert(world.grid, world.player);
    var list = world.monsters;
    for (var i = 0; i < list.length; i++) if (!list[i].dead) sp.insert(world.grid, list[i]);
  }

  // ---- stage 6 helpers ---------------------------------------------------------------------------------------------

  // Ground warning for a non-boss attack: a pooled 'telegraph' effect aged and removed by this stage (skill-effects
  // ignores effects without a skillId, so nothing else touches it).
  function spawnTelegraph(world, m, a) {
    if (!RPG.world.spawnEffect) return;
    var e = RPG.world.spawnEffect(world, {
      type: 'telegraph', x: a.x, y: a.y, r: a.radius > 0 ? a.radius : a.range, team: 1,
      duration: a.duration, ownerId: m.id
    });
    e.shape = a.shape; e.dirX = a.dirX; e.dirY = a.dirY; e.range = a.range; e.radius = a.radius; e.attackId = a.attackId;
    return e;
  }

  function ageTelegraphs(world, dt) {
    var list = world.effects;
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      if (e.type !== 'telegraph') continue;
      e.t += dt;
      if (e.t >= e.duration) e.dead = true;
    }
  }

  function playerInCircle(world, x, y, radius) {
    var p = world.player;
    return !!p && !p.dead && dist(x, y, p.x, p.y) <= radius + p.r;
  }

  function hurtInfo(m, a) {
    return { source: 'monster', attackerId: m.id, defId: m.defId, attackId: a.attackId, type: 'phys' };
  }

  function meleeHit(world, m, a) {
    var p = world.player;
    if (!p || p.dead) return;
    var ang = Math.atan2(a.dirY, a.dirX);
    if (RPG.world.collision.inCone(m.x, m.y, ang, MELEE_HALF_ARC, a.range, p.x, p.y, p.r)) {
      hurt(world, m.damage * a.damageMult, hurtInfo(m, a));
    }
  }

  function areaHit(world, m, a, cx, cy) {
    if (playerInCircle(world, cx, cy, a.radius)) hurt(world, m.damage * a.damageMult, hurtInfo(m, a));
  }

  // Enemy shots live in world.enemyProjectiles (team 1) so weapons.updateProjectiles never resolves them against monsters.
  function fireProjectile(world, m, a) {
    var spec = a.projectile;
    if (!spec || !RPG.world.spawnProjectile) return;
    if (!world.enemyProjectiles) world.enemyProjectiles = [];
    var ang = Math.atan2(a.dirY, a.dirX);
    var p = RPG.world.spawnProjectile(world, {
      x: m.x + Math.cos(ang) * m.r, y: m.y + Math.sin(ang) * m.r, angle: ang, speed: spec.speed, radius: spec.radius,
      lifetime: spec.lifetime, pierce: spec.pierce, bounce: spec.bounce, homing: spec.homing, shape: spec.shape,
      trail: spec.trail, team: 1, ownerId: m.id,
      damage: { amount: m.damage * a.damageMult, type: 'phys', source: 'monster', attackId: a.attackId }
    });
    var i = world.projectiles.lastIndexOf(p);
    if (i >= 0) world.projectiles.splice(i, 1);
    world.enemyProjectiles.push(p);
  }

  // Free spot on a ring around the summoner, else on the summoner itself (never inside a wall).
  function summonSpot(world, m, i, count) {
    var ang = (i / count) * Math.PI * 2 + (m.facing || 0);
    var d = m.r + SUMMON_RING_GAP;
    var x = m.x + Math.cos(ang) * d, y = m.y + Math.sin(ang) * d;
    var map = world.area && world.area.map;
    if (map && RPG.world.collision.circleOverlapsSolid(map, x, y, 10)) return { x: m.x, y: m.y };
    return { x: x, y: y };
  }

  function summon(world, m, a, ctx) {
    var def = defOf(m);
    var cap = (def.aiParams && def.aiParams.summonMax) || SUMMON_CAP_DEFAULT;
    var room = cap - livingAddCount(world, m.id);
    var n = Math.min(a.count, room, MAX_MONSTERS - livingTotal(world));
    for (var i = 0; i < n; i++) {
      var s = summonSpot(world, m, i, a.count);
      var minion = RPG.world.spawnMonster(world, a.defId, s.x, s.y, { level: m.level, summonerId: m.id });
      if (minion) ctx.spawned++;
    }
  }

  function explode(world, m, a, ctx) {
    var def = defOf(m), atk = def.attacks[0];
    areaHit(world, m, { radius: a.radius, damageMult: atk.damageMult, attackId: atk.id }, m.x, m.y);
    m.dead = true;
    m.rewarded = true;       // a self-destruct is not a kill: no xp or loot for it
    m.selfDestructed = true;
  }

  function applyAction(world, m, def, a, ctx) {
    switch (a.type) {
      case 'telegraph': spawnTelegraph(world, m, a); break;
      case 'summon': summon(world, m, a, ctx); break;
      case 'explode': explode(world, m, a, ctx); break;
      case 'attack':
        if (def.ai === 'exploder') break;              // the 'explode' action that follows carries the blast
        if (a.kind === 'projectile') fireProjectile(world, m, a);
        else if (a.kind === 'aoe') areaHit(world, m, a, a.x, a.y);
        else if (a.kind === 'nova') areaHit(world, m, a, m.x, m.y);
        else if (a.kind === 'melee') meleeHit(world, m, a);
        break;
      default: break;                                  // 'phase' only matters for bosses (handled by boss.js)
    }
  }

  // Duck-typed world for boss.js. `effects` is a live accessor onto world.telegraphs.
  function bossWorldOf(world, ctx) {
    var bw = ctx.bossWorld;
    if (!bw) {
      bw = ctx.bossWorld = {
        get effects() { return world.telegraphs; },
        set effects(v) { world.telegraphs = v; },
        players: [],
        boss: null,
        spawnMonster: function (defId, x, y) {
          var b = bw.boss;
          if (!b || livingAddCount(world, b.id) >= BOSS_ADD_CAP || livingTotal(world) >= MAX_MONSTERS) return null;
          return RPG.world.spawnMonster(world, defId, x, y, { level: b.level, summonerId: b.id });
        },
        damagePlayer: function (p, amount, info) { return hurt(world, amount, info); },
        onPhaseChange: function (boss, from, to, special) {
          emit(RPG.core.events.NAMES.BOSS_PHASE, { bossId: boss.id, defId: boss.defId, from: from, to: to, special: special });
        }
      };
    }
    bw.players.length = 0;
    if (world.player) bw.players.push(world.player);
    return bw;
  }

  function tickBoss(world, m, it, ctx) {
    var bw = bossWorldOf(world, ctx);
    bw.boss = m;
    if (!world.telegraphs) world.telegraphs = [];
    for (var i = 0; i < it.actions.length; i++) {
      if (it.actions[i].type === 'telegraph') {
        emit(RPG.core.events.NAMES.BOSS_WARN, { bossId: m.id, defId: m.defId, attackId: it.actions[i].attackId, duration: it.actions[i].duration });
      }
    }
    RPG.world.boss.update(bw, m, it, ctx.dt);
    bw.boss = null;
  }

  // Warnings and burning ground belong to the boss that made them; drop those of bosses that died or were removed.
  function purgeOrphanTelegraphs(world) {
    var list = world.telegraphs;
    if (!list || list.length === 0) return;
    var w = 0;
    for (var i = 0; i < list.length; i++) {
      var o = list[i].owner;
      if (!o || (!o.dead && world.monsters.indexOf(o) >= 0)) list[w++] = list[i];
    }
    list.length = w;
  }

  function summonCountView(world) {
    return function (m) { return livingAddCount(world, m.id); };
  }

  // ---- stage 6 -----------------------------------------------------------------------------------------------------

  function monstersStage(world, ctx) {
    ctx = ctxOf(world, ctx);
    var dt = ctx.dt, p = world.player, ai = RPG.world.ai, c = RPG.world.combat;
    ctx.spawned = 0;
    if (!ctx.monsterView) ctx.monsterView = { target: null, summonCount: summonCountView(world) };
    var view = ctx.monsterView;
    view.target = p && !p.dead ? p : null;
    ageTelegraphs(world, dt);

    var list = world.monsters, n = list.length;      // minions summoned this tick start thinking next tick
    for (var i = 0; i < n; i++) {
      var m = list[i];
      if (m.dead) continue;
      var def = defOf(m);
      if (!def) continue;
      var isBoss = def.ai === 'boss';
      var it;
      if (disabled(m)) {
        m.dvx = 0; m.dvy = 0;
        it = { vx: 0, vy: 0, facing: m.facing, actions: [] };
      } else {
        it = ai.think(m, view, world.rng, dt);
        var f = c.moveSpeedFactor(m);
        m.dvx = it.vx * f; m.dvy = it.vy * f;
        m.facing = it.facing;
      }
      if (isBoss) { tickBoss(world, m, it, ctx); continue; }
      for (var j = 0; j < it.actions.length; j++) {
        if (m.dead) break;
        applyAction(world, m, def, it.actions[j], ctx);
      }
    }
    purgeOrphanTelegraphs(world);
  }

  // ---- stage 7 -----------------------------------------------------------------------------------------------------

  function separate(world, ctx, m) {
    var near = ctx.nearby || (ctx.nearby = []);
    RPG.world.spatial.queryCircle(world.grid, m.x, m.y, m.r, near);
    var px = 0, py = 0;
    for (var i = 0; i < near.length; i++) {
      var o = near[i];
      if (o === m || o.kind !== 'monster' || o.dead) continue;
      var dx = m.x - o.x, dy = m.y - o.y, d = Math.sqrt(dx * dx + dy * dy), overlap = m.r + o.r - d;
      if (overlap <= 0) continue;
      if (d < 1e-6) { dx = (m.id % 2 ? 1 : -1); dy = 0; d = 1; }     // exactly stacked: split along x by id parity
      px += dx / d * overlap * SEPARATION_PUSH;
      py += dy / d * overlap * SEPARATION_PUSH;
    }
    return { x: px, y: py };
  }

  function movementStage(world, ctx) {
    ctx = ctxOf(world, ctx);
    var dt = ctx.dt, map = world.area && world.area.map, col = RPG.world.collision;
    if (!map) return;
    var decay = Math.pow(KNOCKBACK_DECAY, dt * 60);
    var list = world.monsters, i, m;
    if (!world.grid) spatialStage(world, ctx);
    var pushes = [];
    for (i = 0; i < list.length; i++) {
      m = list[i];
      pushes.push(m.dead ? null : separate(world, ctx, m));
    }
    for (i = 0; i < list.length; i++) {
      m = list[i];
      if (m.dead) continue;
      m.prevX = m.x; m.prevY = m.y;
      var kx = m.vx || 0, ky = m.vy || 0;
      col.moveCircle(map, m, ((m.dvx || 0) + kx) * dt + pushes[i].x, ((m.dvy || 0) + ky) * dt + pushes[i].y);
      m.vx = kx * decay; m.vy = ky * decay;
      if (Math.abs(m.vx) < KNOCKBACK_EPS) m.vx = 0;
      if (Math.abs(m.vy) < KNOCKBACK_EPS) m.vy = 0;
    }
  }

  RPG.sim.stage.spatial = spatialStage;
  RPG.sim.stage.monsters = monstersStage;
  RPG.sim.stage.movement = movementStage;
  RPG.sim.monsters = { BOSS_ADD_CAP: BOSS_ADD_CAP, SUMMON_CAP_DEFAULT: SUMMON_CAP_DEFAULT, MAX_MONSTERS: MAX_MONSTERS, MELEE_HALF_ARC: MELEE_HALF_ARC };
})(globalThis.RPG = globalThis.RPG || {});

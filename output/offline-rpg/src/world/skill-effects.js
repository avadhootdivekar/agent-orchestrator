/* Skill effects: turns a successful skill cast into world data (DATA-DEFINITIONS §4 `effect`). Headless: no DOM.
   execute(world, player, skillDef, rank, aim[, params]) dispatches on skillDef.effect and writes plain data onto
   world.projectiles / world.effects, statuses (combat.applyStatus) or the player position, resolving damage through
   RPG.world.combat. Time-based pieces (fireball flight, whirlwind ticks, mend heal-over-time) are advanced by
   updateProjectiles / updateEffects, which the sim calls once per tick. Implemented: projectile, aoe, nova, buff,
   heal, dash, whirl. Other `effect` keys in the design (teleport, chain, summonZone) have no code yet and return
   { ok:false, reason:'unsupported-effect' }. Registers itself as the optional executor of world/skills.js tryCastSkill. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var BASE_DAMAGE = 10;        // spell damage = BASE_DAMAGE + player 'damage' stat, times damageMult and skillDamage
  var SLAM_MAX_RANGE = 240;    // ground-targeted skills land at most this far from the caster
  var PROJECTILE_RANGE = 640;  // fallback flight distance for projectile skills
  var FAN_STEP = 0.14;         // radians between fireballs when count > 1
  var DEFAULT_PLAYER_R = 10;

  var COLORS = { fire: '#e8742a', cold: '#8fd0e8', phys: '#cfc6b0', heal: '#6fbf73', shield: '#9bb0c8', blink: '#d8e6a8' };

  function statOf(ent, key) {
    var s = ent && ent.stats;
    if (!s) return 0;
    var v = typeof s.get === 'function' ? s.get(key) : s[key];
    return typeof v === 'number' ? v : 0;
  }

  /** Stat view usable by combat.resolveHit (works for a StatBlock or a plain snapshot). */
  function statView(player) { return { get: function (key) { return statOf(player, key); } }; }

  function pushEffect(world, spec) {
    spec.id = world.nextEntityId++;
    spec.life = spec.ttl;   // total lifetime, so renderers can compute age = life - ttl
    world.effects.push(spec);
    return spec;
  }

  function liveMonsters(world) {
    var out = [], list = world.monsters || [];
    for (var i = 0; i < list.length; i++) if (!list[i].dead && list[i].hp > 0) out.push(list[i]);
    return out;
  }

  function spellAmount(player, mult) {
    return (BASE_DAMAGE + statOf(player, 'damage')) * mult * (1 + statOf(player, 'skillDamage'));
  }

  /** Statuses from learned upgrades (`addStatus`), one per upgrade rank group; chance is rolled by combat.resolveHit. */
  function upgradeStatuses(player, d) {
    var out = [], ranks = RPG.world.skills ? RPG.world.skills.getUpgradeRanks(player, d.id) : {};
    for (var i = 0; i < d.upgrades.length; i++) {
      var up = d.upgrades[i];
      if (!up.addStatus || !(ranks[up.id] > 0)) continue;
      var s = Object.assign({}, up.addStatus), r = ranks[up.id];
      if (s.chance !== undefined) s.chance = Math.min(1, s.chance * r);
      if (s.dps !== undefined) s.dps *= r;
      if (s.strength !== undefined && s.id === 'reflect') s.strength *= r;
      out.push(s);
    }
    return out;
  }

  /** Resolves and applies one skill hit. Life steal goes to the real player. Returns the combat result. */
  function hitMonster(world, player, m, hit, fromX, fromY) {
    var combat = RPG.world.combat;
    var attacker = { x: fromX, y: fromY, stats: statView(player) };
    var result = combat.resolveHit(attacker, m, hit, world.rng);
    combat.applyHit(null, m, result);
    if (result.lifeSteal > 0 && !player.dead) player.hp = Math.min(player.maxHp, player.hp + result.lifeSteal);
    if (world.fx && world.fx.damageNumbers) {
      for (var i = 0; i < result.events.length; i++) {
        if (result.events[i].type === 'damageNumber') world.fx.damageNumbers.push(result.events[i]);
      }
    }
    return result;
  }

  function aimAngle(player, aim) {
    var dx = (aim ? aim.x : 0) - player.x, dy = (aim ? aim.y : 0) - player.y;
    if (!aim || Math.sqrt(dx * dx + dy * dy) < 1e-9) return player.facing || 0;
    return Math.atan2(dy, dx);
  }

  function groundTarget(player, aim, maxRange) {
    var a = aimAngle(player, aim);
    var dist = aim ? Math.min(Math.hypot(aim.x - player.x, aim.y - player.y), maxRange) : 0;
    return { x: player.x + Math.cos(a) * dist, y: player.y + Math.sin(a) * dist };
  }

  /** Hits every live monster whose circle overlaps (cx, cy, radius). Returns the monsters hit. */
  function areaHit(world, player, cx, cy, radius, hit) {
    var list = liveMonsters(world), hits = [];
    for (var i = 0; i < list.length; i++) {
      var m = list[i], dx = m.x - cx, dy = m.y - cy, reach = radius + (m.r || 0);
      if (dx * dx + dy * dy > reach * reach) continue;
      hitMonster(world, player, m, hit, cx, cy);
      hits.push(m);
    }
    return hits;
  }

  // ---- per-effect behaviours: (world, player, d, p, aim) -> result fields ------------------------------------------

  var EFFECTS = {
    /** Fireball: `count` projectiles fanned around the aim; each explodes (aoeRadius) on its first monster or wall. */
    projectile: function (world, player, d, p, aim) {
      var count = Math.max(1, Math.round(p.count || 1)), base = aimAngle(player, aim), out = [];
      var amount = spellAmount(player, p.damageMult), statuses = upgradeStatuses(player, d);
      var life = PROJECTILE_RANGE / Math.max(1, p.speed);
      for (var i = 0; i < count; i++) {
        var a = base + (i - (count - 1) / 2) * FAN_STEP;
        var pr = {
          id: world.nextEntityId++, kind: 'projectile', team: player.team || 0, ownerId: player.id, skillId: d.id,
          x: player.x, y: player.y, vx: Math.cos(a) * p.speed, vy: Math.sin(a) * p.speed, r: p.radius, angle: a,
          amount: amount, damageType: 'fire', life: life, maxLife: life, pierceLeft: Math.round(p.pierce || 0),
          onHit: 'explode', aoeRadius: p.aoeRadius || 0, status: statuses, shape: 'orb', color: COLORS.fire,
          hitIds: [], dead: false
        };
        world.projectiles.push(pr);
        out.push(pr);
      }
      return { projectiles: out };
    },

    /** Ground Slam: damage + stun in a radius around the ground target, with a shockwave effect and a little knockback. */
    aoe: function (world, player, d, p, aim) {
      var t = groundTarget(player, aim, SLAM_MAX_RANGE);
      var hits = areaHit(world, player, t.x, t.y, p.radius, {
        amount: spellAmount(player, p.damageMult), type: 'phys', knockback: 90,
        status: [{ id: 'stun', duration: p.stunDur, stacks: 'max' }]
      });
      var fx = pushEffect(world, { kind: 'aoe', x: t.x, y: t.y, radius: p.radius, ttl: 0.4, color: COLORS.phys, damageType: 'phys',
        skillId: d.id, hits: hits.map(function (m) { return m.id; }) });
      return { hits: hits, effects: [fx], target: t };
    },

    /** Frost Nova: cold damage + slow around the caster; upgrades may add a chance to freeze. */
    nova: function (world, player, d, p) {
      var statuses = [{ id: 'slow', duration: p.duration, strength: Math.min(1, p.slow), stacks: 'max' }]
        .concat(upgradeStatuses(player, d));
      var hits = areaHit(world, player, player.x, player.y, p.radius, {
        amount: spellAmount(player, p.damageMult), type: 'cold', status: statuses
      });
      var fx = pushEffect(world, { kind: 'ring', x: player.x, y: player.y, radius: p.radius, ttl: 0.5, color: COLORS.cold,
        damageType: 'cold', skillId: d.id, hits: hits.map(function (m) { return m.id; }) });
      return { hits: hits, effects: [fx] };
    },

    /** Shield: a 'shield' status on the player whose strength is the damage it can still absorb (see absorbDamage). */
    buff: function (world, player, d, p) {
      var combat = RPG.world.combat;
      var st = combat.applyStatus(player, { id: 'shield', duration: p.duration, strength: p.shield, stacks: 'max' });
      st.healOnExpire = p.heal || 0;
      var statuses = [st], extra = upgradeStatuses(player, d);
      for (var i = 0; i < extra.length; i++) statuses.push(combat.applyStatus(player, extra[i]));
      var fx = pushEffect(world, { kind: 'aura', follow: player.id, x: player.x, y: player.y, radius: player.r || DEFAULT_PLAYER_R,
        ttl: p.duration, color: COLORS.shield, skillId: d.id });
      return { statuses: statuses, effects: [fx] };
    },

    /** Mend: heals `heal` HP spread over `duration` seconds (a 'heal' effect that updateEffects ticks). */
    heal: function (world, player, d, p) {
      var fx = pushEffect(world, { kind: 'heal', follow: player.id, x: player.x, y: player.y, radius: player.r || DEFAULT_PLAYER_R,
        ttl: p.duration, total: p.heal, perSecond: p.heal / Math.max(0.05, p.duration), color: COLORS.heal, skillId: d.id });
      return { effects: [fx], healTotal: p.heal };
    },

    /** Blink Strike: moves the player up to `distance` px toward the aim (sliding/stopping at walls via collision) and
        damages monsters along the path and around the landing point. */
    dash: function (world, player, d, p, aim) {
      var a = aimAngle(player, aim), sx = player.x, sy = player.y;
      var map = world.area && world.area.map, dist = p.distance;
      if (player.r === undefined) player.r = DEFAULT_PLAYER_R;
      if (map) RPG.world.collision.moveCircle(map, player, Math.cos(a) * dist, Math.sin(a) * dist);
      else { player.x += Math.cos(a) * dist; player.y += Math.sin(a) * dist; player.hitWall = false; }
      player.facing = a;
      var ex = player.x, ey = player.y, sdx = ex - sx, sdy = ey - sy, len2 = sdx * sdx + sdy * sdy;
      var amount = spellAmount(player, p.damageMult), hits = [], list = liveMonsters(world);
      for (var i = 0; i < list.length; i++) {
        var m = list[i], t = len2 > 0 ? Math.max(0, Math.min(1, ((m.x - sx) * sdx + (m.y - sy) * sdy) / len2)) : 0;
        var px = sx + sdx * t, py = sy + sdy * t, reach = p.radius + (m.r || 0);
        var nearPath = (m.x - px) * (m.x - px) + (m.y - py) * (m.y - py) <= reach * reach;
        var nearEnd = (m.x - ex) * (m.x - ex) + (m.y - ey) * (m.y - ey) <= reach * reach;
        if (!nearPath && !nearEnd) continue;
        hitMonster(world, player, m, { amount: amount, type: 'phys', knockback: 60 }, px, py);
        hits.push(m);
      }
      var fx = pushEffect(world, { kind: 'arc', x: sx, y: sy, x2: ex, y2: ey, radius: p.radius, ttl: 0.25, color: COLORS.blink, skillId: d.id,
        hits: hits.map(function (m) { return m.id; }) });
      return { hits: hits, effects: [fx], moved: Math.sqrt(len2), hitWall: !!player.hitWall, from: { x: sx, y: sy }, to: { x: ex, y: ey } };
    },

    /** Whirlwind: a spinning zone that follows the caster and hits everything in `radius` every `tickInterval`
        for `duration` seconds (first tick immediately); `lifeSteal` heals a fraction of the damage dealt. */
    whirl: function (world, player, d, p) {
      var fx = pushEffect(world, { kind: 'whirl', follow: player.id, x: player.x, y: player.y, radius: p.radius, ttl: p.duration,
        tickEvery: p.tickInterval, tickT: 0, amount: spellAmount(player, p.damageMult), lifeSteal: p.lifeSteal || 0,
        color: COLORS.phys, skillId: d.id, hits: [] });
      var hits = whirlTick(world, player, fx);
      return { effects: [fx], hits: hits };
    }
  };

  function whirlTick(world, player, fx) {
    var hits = areaHit(world, player, fx.x, fx.y, fx.radius, { amount: fx.amount, type: 'phys' });
    if (fx.lifeSteal > 0 && !player.dead) {
      var dealt = 0;
      // areaHit applied the hits already; approximate steal from the nominal amount per target (post-mitigation is not kept)
      dealt = hits.length * fx.amount;
      player.hp = Math.min(player.maxHp, player.hp + dealt * fx.lifeSteal);
    }
    for (var i = 0; i < hits.length; i++) if (fx.hits.indexOf(hits[i].id) < 0) fx.hits.push(hits[i].id);
    return hits;
  }

  /**
   * Applies skill `skillDef` at `rank` toward `aim` ({x,y} world coords). `params` defaults to the rank/upgrade-resolved
   * params from world/skills.js. Returns { ok, skillId, effect, ...result } or { ok:false, reason }.
   */
  function execute(world, player, skillDef, rank, aim, params) {
    if (!skillDef || skillDef.type !== 'active' || !skillDef.effect) return { ok: false, reason: 'not-active' };
    var fn = EFFECTS[skillDef.effect];
    if (!fn) return { ok: false, reason: 'unsupported-effect' };
    var p = params || RPG.world.skills.resolveSkillParams(player, skillDef);
    var r = fn(world, player, skillDef, p, aim);
    r.ok = true; r.skillId = skillDef.id; r.effect = skillDef.effect; r.rank = rank;
    return r;
  }

  // ---- per-tick updates ---------------------------------------------------------------------------------------------

  function explode(world, player, pr, hitMonsterRef) {
    var ex = pr.x, ey = pr.y;
    var hit = { amount: pr.amount, type: pr.damageType, knockback: 70, status: pr.status };
    var hits = pr.aoeRadius > 0 ? areaHit(world, player, ex, ey, pr.aoeRadius, hit) : [];
    if (hitMonsterRef && hits.indexOf(hitMonsterRef) < 0 && !pr.aoeRadius) hitMonster(world, player, hitMonsterRef, hit, ex, ey);
    pushEffect(world, { kind: 'aoe', x: ex, y: ey, radius: pr.aoeRadius || pr.r * 2, ttl: 0.3, color: pr.color, damageType: pr.damageType,
      skillId: pr.skillId, hits: hits.map(function (m) { return m.id; }) });
  }

  /** Moves skill projectiles (those with `skillId`), stops them at walls, and explodes them on the first monster they touch. */
  function updateProjectiles(world, dt) {
    var list = world.projectiles || [], map = world.area && world.area.map, player = world.player;
    for (var i = list.length - 1; i >= 0; i--) {
      var p = list[i];
      if (!p.skillId) continue;
      p.life -= dt;
      var hitWall = false;
      if (map) hitWall = RPG.world.collision.sweepProjectile(map, p, dt).hitWall;
      else { p.x += p.vx * dt; p.y += p.vy * dt; }
      var struck = null, mons = liveMonsters(world);
      for (var j = 0; j < mons.length && !struck; j++) {
        var m = mons[j];
        if (p.hitIds.indexOf(m.id) >= 0) continue;
        var dx = m.x - p.x, dy = m.y - p.y, reach = p.r + (m.r || 0);
        if (dx * dx + dy * dy <= reach * reach) struck = m;
      }
      if (struck && player) {
        p.hitIds.push(struck.id);
        if (p.pierceLeft > 0) { p.pierceLeft--; hitMonster(world, player, struck, { amount: p.amount, type: p.damageType, status: p.status }, p.x, p.y); }
        else { explode(world, player, p, struck); p.dead = true; }
      } else if (hitWall && player) {
        explode(world, player, p, null);
        p.dead = true;
      } else if (p.life <= 0) p.dead = true;
      if (p.dead) RPG.core.util.removeAt(list, i);
    }
  }

  /** Advances skill effects: follows the caster, ticks whirl damage and heal-over-time, ages ttl and removes expired ones. */
  function updateEffects(world, dt) {
    var list = world.effects || [], player = world.player;
    for (var i = list.length - 1; i >= 0; i--) {
      var e = list[i];
      if (!e.skillId) continue;
      if (e.follow !== undefined && player && player.id === e.follow) { e.x = player.x; e.y = player.y; }
      var step = Math.min(dt, Math.max(0, e.ttl));
      if (player && !player.dead) {
        if (e.kind === 'whirl') {
          e.tickT += step;
          while (e.tickT >= e.tickEvery - 1e-9) { e.tickT -= e.tickEvery; whirlTick(world, player, e); }
        } else if (e.kind === 'heal') {
          player.hp = Math.min(player.maxHp, player.hp + e.perSecond * step);
        }
      }
      e.ttl -= dt;
      if (e.ttl <= 1e-9) RPG.core.util.removeAt(list, i);
    }
  }

  /** Shield absorption for the damage pipeline: returns the damage left after the player's 'shield' status soaks `amount`. */
  function absorbDamage(player, amount) {
    var st = player.status && player.status.shield;
    if (!st || st.strength <= 0 || amount <= 0) return amount;
    var soaked = Math.min(st.strength, amount);
    st.strength -= soaked;
    if (st.strength <= 0) delete player.status.shield;
    return amount - soaked;
  }

  RPG.world.skillEffects = {
    execute: execute, updateProjectiles: updateProjectiles, updateEffects: updateEffects, absorbDamage: absorbDamage
  };

  if (RPG.world.skills && RPG.world.skills.setExecutor) RPG.world.skills.setExecutor(execute);
})(globalThis.RPG = globalThis.RPG || {});

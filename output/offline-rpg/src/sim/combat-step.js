/* Combat resolution stages 8-10 of the per-tick pipeline (INTEGRATION-PLAN §2.2): projectile and effect updates,
   enemy projectiles and ground zones, status ticks, and HIT events. Also defines RPG.sim.hurtPlayer, the single entry
   point for damage to the player (invulnerability, shield, armor/resistance, reflect/thorns, flash, shake, HIT).
   Headless: no DOM, no clock, no Math.random. Stages run as RPG.sim.stage.<name>(world, ctx); ctx = world.scratch. */
(function (RPG) {
  'use strict';

  RPG.sim = RPG.sim || {};
  RPG.sim.stage = RPG.sim.stage || {};

  var IFRAME_STATUS = 'iframe';
  var IFRAME_SECONDS = 0.4;      // invulnerability after a hit, so one swarm cannot strip HP in a single tick
  var HURT_FLASH_SECONDS = 0.25; // screen-edge flash length (world.fx.hurtFlash, decayed here)
  var SHAKE_BASE = 1.5;          // camera shake added per hit (px), plus SHAKE_PER_DAMAGE per point dealt
  var SHAKE_PER_DAMAGE = 0.2;
  var SHAKE_CAP = 8;             // world.fx.shake never exceeds this (sim.js decays it each tick)
  var ENEMY_SHOT_LIFETIME = 3;   // seconds, used when a shot carries no lifetime of its own

  function statOf(ent, key) {
    var s = ent && ent.stats;
    if (!s) return 0;
    var v = typeof s.get === 'function' ? s.get(key) : s[key];
    return typeof v === 'number' ? v : 0;
  }

  /** `{stats: view}` shape that combat.js reads. The player's `stats` is a plain snapshot with no `get`. */
  function statView(ent) { return { get: function (key) { return statOf(ent, key); } }; }

  /** Accepts a DamageSpec, a bare number, or nothing. Returns a DamageSpec-shaped object. */
  function damageSpec(d) {
    if (typeof d === 'number') return { amount: d };
    return d || { amount: 0 };
  }

  /** Publishes a HIT now when there is no tick in progress; otherwise queues it for the resolve stage. */
  function recordHit(world, hit) {
    var ctx = world.scratch;
    if (ctx && ctx.hits) ctx.hits.push(hit);
    else RPG.core.events.emit(RPG.core.events.NAMES.HIT, hit);
  }

  function pushDamageNumber(world, x, y, amount, type) {
    var nums = world.fx && world.fx.damageNumbers;
    if (nums && amount > 0) nums.push({ type: 'damageNumber', x: x, y: y, amount: Math.round(amount), crit: false, damageType: type });
  }

  function healPlayer(p, amount) {
    if (!p.dead && amount > 0) p.hp = Math.min(p.maxHp, p.hp + amount);
  }

  // ---- Stage 8: projectiles / effects ----

  /** Living monster HP keyed by id, taken before player-side projectiles and effects act this tick. */
  function monsterHpSnapshot(world) {
    var snap = {}, list = world.monsters || [];
    for (var i = 0; i < list.length; i++) if (!list[i].dead) snap[list[i].id] = list[i].hp;
    return snap;
  }

  /** Weapons and skills resolve their own damage without reporting it, so HIT is derived from the HP that went down. */
  function reportMonsterHits(world, before) {
    var list = world.monsters || [];
    for (var i = 0; i < list.length; i++) {
      var m = list[i], was = before[m.id];
      if (was === undefined || !(m.hp < was)) continue;
      recordHit(world, { targetId: m.id, dealt: was - m.hp, crit: false, type: 'phys', x: m.x, y: m.y, team: m.team });
    }
  }

  /** Enemy shots (team 1) fly, stop at walls, hit the player through hurtPlayer and are consumed on contact. */
  function updateEnemyProjectiles(world, dt) {
    var list = world.enemyProjectiles || [], map = world.area && world.area.map, p = world.player;
    var collision = RPG.world.collision;
    for (var i = list.length - 1; i >= 0; i--) {
      var shot = list[i];
      shot.age = (shot.age || 0) + dt;
      var hitWall = false;
      if (map) hitWall = collision.sweepProjectile(map, shot, dt).hitWall;
      else { shot.x += shot.vx * dt; shot.y += shot.vy * dt; }
      var reached = !!p && !p.dead && collision.circlesOverlap(shot, p);
      if (reached) {
        var spec = damageSpec(shot.damage);
        var source = shot.ownerId === undefined || shot.ownerId === null ? null : RPG.world.findEntity(world, shot.ownerId);
        hurtPlayer(world, spec.amount, { type: spec.type, ignoreArmor: spec.ignoreArmor, source: source });
      }
      var lifetime = shot.lifetime === undefined ? ENEMY_SHOT_LIFETIME : shot.lifetime;
      if (reached || hitWall || shot.age >= lifetime) RPG.core.util.removeAt(list, i);
    }
  }

  /** Monster-owned ground effects (type 'aoe'|'zone' with a damage spec) hit the player once, or every tickEvery. */
  function updateZones(world, dt) {
    var list = world.effects || [], p = world.player, collision = RPG.world.collision;
    for (var i = 0; i < list.length; i++) {
      var e = list[i];
      // Skill effects carry `kind` and are advanced by skill-effects.js; spawnEffect() entries carry `type`.
      if (e.skillId || e.dead || e.type === undefined) continue;
      e.t = (e.t || 0) + dt;
      if (e.damage && (e.type === 'aoe' || e.type === 'zone') && p && !p.dead) {
        var due = e.nextDamageAt !== undefined && e.nextDamageAt !== null ? e.nextDamageAt : (e.delayDamageAt || 0);
        if (e.t >= due) {
          if (collision.circlesOverlap({ x: e.x, y: e.y, r: e.r || 0 }, p)) hurtPlayer(world, e.damage.amount, e.damage);
          e.nextDamageAt = e.tickEvery ? due + e.tickEvery : Infinity;
        }
      }
      if (e.t >= (e.duration === undefined ? 1 : e.duration)) e.dead = true;
    }
  }

  function projectiles(world, ctx) {
    var dt = ctx.dt, before = monsterHpSnapshot(world);
    if (RPG.world.weapons && RPG.world.weapons.updateProjectiles) RPG.world.weapons.updateProjectiles(world, dt);
    if (RPG.world.skillEffects) {
      RPG.world.skillEffects.updateProjectiles(world, dt);
      RPG.world.skillEffects.updateEffects(world, dt);
    }
    reportMonsterHits(world, before);
    updateEnemyProjectiles(world, dt);
    updateZones(world, dt);
  }

  // ---- Stage 9: statuses / cooldowns / flash ----

  /** Ticks the player's statuses through combat.tickStatuses on a shim (its `stats` view is what combat reads). */
  function tickPlayerStatuses(world, dt) {
    var p = world.player, combat = RPG.world.combat;
    var shield = p.status && p.status.shield;
    var shim = { id: p.id, x: p.x, y: p.y, hp: p.hp, maxHp: p.maxHp, dead: p.dead, flash: p.flash, status: p.status, stats: statView(p) };
    var events = combat.tickStatuses(shim, dt);
    p.hp = shim.hp; p.dead = shim.dead; p.flash = shim.flash;
    for (var i = 0; i < events.length; i++) {
      if (!events[i].dot) continue;
      pushDamageNumber(world, p.x, p.y, events[i].amount, events[i].damageType);
      recordHit(world, { targetId: p.id, dealt: events[i].amount, crit: false, type: events[i].damageType, x: p.x, y: p.y, team: p.team, dot: events[i].dot });
    }
    // A shield that ran out by timer heals for its healOnExpire (the absorbing case is handled in hurtPlayer).
    if (shield && !p.status.shield && shield.healOnExpire > 0) healPlayer(p, shield.healOnExpire);
  }

  function statuses(world, ctx) {
    var dt = ctx.dt, combat = RPG.world.combat, p = world.player;
    if (p) {
      tickPlayerStatuses(world, dt);
      combat.tickCooldowns(p, dt);
      p.flash = Math.max(0, (p.flash || 0) - dt);
    }
    var fx = world.fx;
    if (fx && fx.hurtFlash > 0) fx.hurtFlash = Math.max(0, fx.hurtFlash - dt);

    var list = world.monsters || [];
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m.dead) continue;
      var events = combat.tickStatuses(m, dt);
      for (var j = 0; j < events.length; j++) {
        if (!events[j].dot) continue;
        pushDamageNumber(world, m.x, m.y, events[j].amount, events[j].damageType);
        recordHit(world, { targetId: m.id, dealt: events[j].amount, crit: false, type: events[j].damageType, x: m.x, y: m.y, team: m.team, dot: events[j].dot });
      }
      m.flash = Math.max(0, (m.flash || 0) - dt);
    }
  }

  // ---- Stage 10: resolve ----

  /** Turns the hits recorded this tick (ctx.hits) into HIT events. Begin clears the list next tick. */
  function resolve(world, ctx) {
    var bus = RPG.core.events, hits = ctx.hits || [];
    for (var i = 0; i < hits.length; i++) bus.emit(bus.NAMES.HIT, hits[i]);
  }

  /** Reflects `amount` back at the monster that hit the player, through the same combat resolution path. */
  function reflectDamage(world, p, m, amount) {
    var combat = RPG.world.combat;
    var result = combat.resolveHit({ x: p.x, y: p.y, stats: statView(p) }, m,
      { amount: amount, type: 'phys', canCrit: false, ignoreArmor: true }, world.rng);
    // No attacker: the player's life steal must not apply to reflected damage.
    combat.applyHit(null, m, result);
    recordHit(world, { targetId: m.id, dealt: result.dealt, crit: false, type: result.type, x: m.x, y: m.y, team: m.team });
  }

  /**
   * The only way damage reaches the player. info = { type='phys', ignoreArmor, source?: Entity, sourceId? }.
   * Order: invulnerability (dash `invuln`, post-hit `iframe`) -> shield absorb -> armor (phys) or resistance (elements)
   * -> HP -> reflect/thorns back at a monster source -> flash, shake, HIT. Returns
   * { dealt, absorbed, reflected, blocked: null|'dead'|'invuln'|'iframe', killed }.
   */
  function hurtPlayer(world, amount, info) {
    var p = world.player, combat = RPG.world.combat;
    info = info || {};
    var out = { dealt: 0, absorbed: 0, reflected: 0, blocked: null, killed: false };
    if (!p || p.dead) { out.blocked = 'dead'; return out; }
    if (!(amount > 0)) return out;
    if (combat.hasStatus(p, 'invuln')) { out.blocked = 'invuln'; return out; }
    if (combat.hasStatus(p, IFRAME_STATUS)) { out.blocked = 'iframe'; return out; }

    var type = info.type || 'phys';
    var shield = p.status.shield;
    var raw = amount;
    var absorb = RPG.world.skillEffects && RPG.world.skillEffects.absorbDamage;
    if (absorb) raw = absorb(p, raw);
    out.absorbed = amount - raw;
    if (shield && !p.status.shield && shield.healOnExpire > 0) healPlayer(p, shield.healOnExpire);

    var mitigation = 0;
    if (type === 'phys') { if (!info.ignoreArmor) mitigation = combat.armorMitigation(statOf(p, 'armor')); }
    else mitigation = combat.resistance({ stats: statView(p) }, type);
    var dealt = Math.max(0, raw * (1 - mitigation));
    if (dealt > p.hp) dealt = Math.max(0, p.hp);
    p.hp -= dealt;
    out.dealt = dealt;

    // Every hit that got past the gate buys a short i-frame window, shield-absorbed ones included.
    combat.applyStatus(p, { id: IFRAME_STATUS, duration: IFRAME_SECONDS, stacks: 'refresh' });

    var source = info.source || (info.sourceId === undefined || info.sourceId === null ? null : RPG.world.findEntity(world, info.sourceId));
    if (source && source.team === 1 && !source.dead && dealt > 0) {
      var share = shield && p.status.reflect ? p.status.reflect.strength * dealt : 0;
      out.reflected = share + statOf(p, 'thorns');
      if (out.reflected > 0) reflectDamage(world, p, source, out.reflected);
    }

    p.flash = Math.max(p.flash || 0, combat.HIT_FLASH);
    var fx = world.fx;
    if (fx) {
      fx.hurtFlash = Math.max(fx.hurtFlash || 0, HURT_FLASH_SECONDS);
      fx.shake = Math.min(SHAKE_CAP, (fx.shake || 0) + SHAKE_BASE + SHAKE_PER_DAMAGE * dealt);
    }
    pushDamageNumber(world, p.x, p.y, dealt, type);

    if (p.hp <= 0) { p.hp = 0; p.dead = true; out.killed = true; }
    recordHit(world, { targetId: p.id, dealt: dealt, crit: false, type: type, x: p.x, y: p.y, team: p.team, absorbed: out.absorbed });
    return out;
  }

  RPG.sim.stage.projectiles = projectiles;
  RPG.sim.stage.statuses = statuses;
  RPG.sim.stage.resolve = resolve;
  RPG.sim.hurtPlayer = hurtPlayer;
})(globalThis.RPG = globalThis.RPG || {});

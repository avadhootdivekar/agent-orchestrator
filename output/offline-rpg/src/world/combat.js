/* Combat core: pure damage resolution, statuses and cooldowns (ARCHITECTURE §combat). Headless: no DOM, no rendering.
   Entities are plain objects { id, x, y, vx, vy, hp, maxHp, dead, flash, stats: StatBlock, status: {}, cooldowns: {} }.
   resolveHit() only computes (consumes RNG, mutates nothing); applyHit() writes the result back onto the entities. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var ARMOR_K = 100;            // physical mitigation = armor / (armor + ARMOR_K)
  var HIT_FLASH = 0.12;         // seconds of white flash on a hit
  var FLASH_REST = 0.25;        // seconds a sprite must stay un-flashed after a flash, so rapid multi-hits cannot strobe (<3 Hz)
  var STATUS_TICK = 0.5;        // seconds between damage-over-time ticks
  var RESIST_STAT = { fire: 'resFire', cold: 'resCold', lightning: 'resLightning', poison: 'resPoison', arcane: 'resArcane' };
  var DOT_TYPE = { burn: 'fire', poison: 'poison' };

  /** Reads a stat from a StatBlock (`stats.get`) or from a plain snapshot (`stats[key]`, as sim/player.js keeps on the player). */
  function stat(ent, key) {
    var s = ent && ent.stats;
    if (!s) return 0;
    var v = typeof s.get === 'function' ? s.get(key) : s[key];
    return typeof v === 'number' ? v : 0;
  }

  /** Fraction of incoming damage removed by armor (physical only). */
  function armorMitigation(armor) { return armor <= 0 ? 0 : armor / (armor + ARMOR_K); }

  /** Fraction removed by the target's resistance to `type` (0 for 'phys' or an unknown type). */
  function resistance(target, type) { return RESIST_STAT[type] ? stat(target, RESIST_STAT[type]) : 0; }

  /** Direction (unit vector) pointing from the attacker towards the target; falls back to +x when they coincide. */
  function directionFrom(fromX, fromY, toX, toY) {
    var dx = toX - fromX, dy = toY - fromY, len = Math.sqrt(dx * dx + dy * dy);
    return len < 1e-9 ? { x: 1, y: 0 } : { x: dx / len, y: dy / len };
  }

  /**
   * hit = { amount, type='phys', canCrit=true, ignoreArmor=false, knockback=0, status=[StatusSpec], skillId?, isDot? }
   * Returns { dealt, raw, crit, mitigated, type, knockback:{vx,vy}|null, lifeSteal, killed, statuses:[StatusSpec],
   *           events:[{type:'hitFlash'|'damageNumber', ...}] }. Draw order from `rng` is fixed: crit roll, then status rolls.
   */
  function resolveHit(attacker, target, hit, rng) {
    var type = hit.type || 'phys';
    var raw = Math.max(0, hit.amount);
    var crit = false;
    if (hit.canCrit !== false && !hit.isDot) crit = rng.chance(stat(attacker, 'critChance'));
    if (crit) raw *= Math.max(1, stat(attacker, 'critDamage'));

    var mitigation = 0;
    if (type === 'phys') { if (!hit.ignoreArmor) mitigation = armorMitigation(stat(target, 'armor')); }
    else mitigation = resistance(target, type);
    var dealt = raw * (1 - mitigation);
    if (dealt > target.hp) dealt = Math.max(0, target.hp);
    var killed = target.hp - dealt <= 0 && raw > 0;

    var knock = null;
    if (hit.knockback > 0 && !hit.isDot && attacker) {
      var dir = directionFrom(attacker.x, attacker.y, target.x, target.y);
      knock = { vx: dir.x * hit.knockback, vy: dir.y * hit.knockback };
    }

    var statuses = [];
    var specs = hit.status || [];
    for (var i = 0; i < specs.length; i++) {
      if (specs[i].chance === undefined || rng.chance(specs[i].chance)) statuses.push(specs[i]);
    }

    return {
      dealt: dealt, raw: raw, crit: crit, mitigated: raw - dealt, type: type, knockback: knock,
      lifeSteal: hit.isDot ? 0 : dealt * stat(attacker, 'lifeSteal'), killed: killed, statuses: statuses,
      events: [
        { type: 'hitFlash', targetId: target.id, duration: HIT_FLASH },
        { type: 'damageNumber', x: target.x, y: target.y, amount: Math.round(dealt), crit: crit, damageType: type }
      ]
    };
  }

  /** Starts a hit flash unless the sprite is still inside its rest window after the last one (caps flashing at ~3 Hz). */
  function triggerFlash(ent) {
    if ((ent.flashLock || 0) > 0) return;
    ent.flash = Math.max(ent.flash || 0, HIT_FLASH);
    ent.flashLock = HIT_FLASH + FLASH_REST;
  }

  /** Counts the flash and its rest window down by dt. */
  function tickFlash(ent, dt) {
    ent.flash = Math.max(0, (ent.flash || 0) - dt);
    ent.flashLock = Math.max(0, (ent.flashLock || 0) - dt);
  }

  /** Writes a resolveHit result onto the entities: hp, death, knockback velocity, flash, life steal, statuses. */
  function applyHit(attacker, target, result) {
    target.hp -= result.dealt;
    if (target.hp <= 0) { target.hp = 0; target.dead = true; }
    if (result.knockback) { target.vx = (target.vx || 0) + result.knockback.vx; target.vy = (target.vy || 0) + result.knockback.vy; }
    triggerFlash(target);
    if (attacker && result.lifeSteal > 0 && !attacker.dead) attacker.hp = Math.min(attacker.maxHp, attacker.hp + result.lifeSteal);
    for (var i = 0; i < result.statuses.length; i++) applyStatus(target, result.statuses[i]);
    return result;
  }

  /**
   * StatusSpec = { id, duration, strength?, dps?, stacks?:'refresh'|'stack'|'max' } (chance is rolled by resolveHit).
   * Default policy 'refresh': re-applying resets the duration and keeps the stronger values.
   */
  function applyStatus(ent, spec) {
    if (!ent.status) ent.status = {};
    var cur = ent.status[spec.id];
    var next = { id: spec.id, remaining: spec.duration, strength: spec.strength || 0, dps: spec.dps || 0, acc: 0, stacks: 1 };
    if (!cur) { ent.status[spec.id] = next; return next; }
    var policy = spec.stacks || 'refresh';
    next.acc = cur.acc;
    if (policy === 'stack') { next.stacks = cur.stacks + 1; next.dps = cur.dps + next.dps; next.strength = Math.max(cur.strength, next.strength); }
    else if (policy === 'max') { next.remaining = Math.max(cur.remaining, next.remaining); }
    else { next.dps = Math.max(cur.dps, next.dps); next.strength = Math.max(cur.strength, next.strength); }
    ent.status[spec.id] = next;
    return next;
  }

  /** Advances statuses by dt: burn/poison deal damage every STATUS_TICK, expired statuses are removed. Returns events. */
  function tickStatuses(ent, dt) {
    var events = [];
    if (!ent.status || ent.dead) return events;
    for (var id in ent.status) {
      var s = ent.status[id];
      var step = Math.min(dt, s.remaining);
      s.remaining -= dt;
      if (s.dps > 0) {
        s.acc += step;
        while (s.acc >= STATUS_TICK - 1e-9 && !ent.dead) {
          s.acc -= STATUS_TICK;
          var type = DOT_TYPE[id] || 'phys';
          var dealt = Math.min(ent.hp, s.dps * STATUS_TICK * (1 - resistance(ent, type)));
          ent.hp -= dealt;
          triggerFlash(ent);
          events.push({ type: 'damageNumber', x: ent.x, y: ent.y, amount: Math.round(dealt), crit: false, damageType: type, dot: id });
          if (ent.hp <= 0) { ent.hp = 0; ent.dead = true; }
        }
      }
      if (s.remaining <= 1e-9) delete ent.status[id];
    }
    return events;
  }

  function hasStatus(ent, id) { return !!(ent.status && ent.status[id]); }

  /** Movement-speed multiplier from 'slow' (strength 0..1 is the fraction removed). */
  function moveSpeedFactor(ent) {
    var s = ent.status && ent.status.slow;
    return s ? Math.max(0, 1 - Math.min(1, s.strength)) : 1;
  }

  /** Cooldowns live in ent.cooldowns = { id: secondsRemaining }. The caster's cooldownReduction shortens new ones. */
  function startCooldown(ent, id, base) {
    if (!ent.cooldowns) ent.cooldowns = {};
    ent.cooldowns[id] = base * (1 - stat(ent, 'cooldownReduction'));
    return ent.cooldowns[id];
  }
  function isReady(ent, id) { return !ent.cooldowns || !(ent.cooldowns[id] > 0); }
  function tickCooldowns(ent, dt) {
    if (!ent.cooldowns) return;
    for (var id in ent.cooldowns) {
      ent.cooldowns[id] -= dt;
      if (ent.cooldowns[id] <= 0) delete ent.cooldowns[id];
    }
  }

  RPG.world.combat = {
    ARMOR_K: ARMOR_K, HIT_FLASH: HIT_FLASH, FLASH_REST: FLASH_REST, triggerFlash: triggerFlash, tickFlash: tickFlash, STATUS_TICK: STATUS_TICK,
    stat: stat, armorMitigation: armorMitigation, resistance: resistance, directionFrom: directionFrom,
    resolveHit: resolveHit, applyHit: applyHit, applyStatus: applyStatus, tickStatuses: tickStatuses,
    hasStatus: hasStatus, moveSpeedFactor: moveSpeedFactor,
    startCooldown: startCooldown, isReady: isReady, tickCooldowns: tickCooldowns
  };
})(globalThis.RPG = globalThis.RPG || {});

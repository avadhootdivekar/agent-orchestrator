/* Weapon attack behaviours (ARCHITECTURE §world/weapons). Headless: no DOM, no rendering.
   An attack is a small state machine on player.attack = { state: 'idle'|'windup'|'active'|'recover', t, dir, cd, ... }.
   tryAttack() spends the resource and starts it; updateAttack() advances the phases and fires the behaviour once at the
   start of `active`. Hits are resolved and applied through RPG.world.combat. Projectiles and effects are plain data pushed
   onto world.projectiles / world.effects; updateProjectiles() moves projectiles and resolves their hits.
   Implemented behaviours: swing, projectile, spread, aoeGround. Other keys in the design (thrust, smash, beam, arc,
   explosive) have no code yet, so tryAttack() refuses a weapon that uses one. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var MAX_SLOTS = 3;

  function weaponDefs() { return (RPG.data && RPG.data.weapons) || {}; }

  /** stats.get for a StatBlock, plain property for a snapshot (sim/player.js keeps a snapshot in player.stats). */
  function statOf(ent, key) {
    var s = ent && (ent.stats && typeof ent.stats.get === 'function' ? ent.stats : ent.statBlock || ent.stats);
    if (!s) return 0;
    var v = typeof s.get === 'function' ? s.get(key) : s[key];
    return typeof v === 'number' ? v : 0;
  }

  /**
   * Resolves a weaponSlots entry (weapon id, { weaponId }, { defId }, or a looted ItemInstance { baseId, weaponId? })
   * to a WeaponDefinition.
   */
  function defFromSlot(slot) {
    if (!slot) return null;
    var defs = weaponDefs();
    var id = typeof slot === 'string' ? slot : slot.weaponId || slot.defId || slot.baseId;
    if (!id) return null;
    if (defs[id]) return defs[id];
    var item = RPG.data && RPG.data.items && RPG.data.items[id];
    return item && item.weaponId && defs[item.weaponId] ? defs[item.weaponId] : null;
  }

  /** { def, item } for the active weapon slot, or { def: null, item: null } when it is empty. */
  function currentWeapon(player) {
    var slot = player.weaponSlots ? player.weaponSlots[player.activeWeapon || 0] : null;
    var def = defFromSlot(slot);
    return { def: def, item: def && slot && typeof slot === 'object' ? slot : null };
  }

  function ensureAttack(player) {
    if (!player.attack) player.attack = { state: 'idle', t: 0, dir: 0 };
    var a = player.attack;
    if (a.cd === undefined) a.cd = 0;
    return a;
  }

  /** Seconds between attacks: the weapon's base interval divided by the derived attackSpeed (floored by the stat clamp). */
  function attackInterval(player) {
    var def = currentWeapon(player).def;
    if (!def) return 0;
    return def.attackInterval / Math.max(0.1, statOf(player, 'attackSpeed') || 1);
  }

  function speedFactor(player) { return Math.max(0.1, statOf(player, 'attackSpeed') || 1); }

  /** Movement multiplier while an attack is in progress (1 when idle): 1 - moveSlowWhileAttacking. */
  function moveFactor(player) {
    var a = player.attack;
    if (!a || a.state === 'idle') return 1;
    var def = currentWeapon(player).def;
    return def ? 1 - Math.min(1, Math.max(0, def.moveSlowWhileAttacking || 0)) : 1;
  }

  /** Stat view for combat.resolveHit: the player's stats with this weapon's crit bonuses added. */
  function weaponStats(player, def) {
    return {
      get: function (key) {
        var v = statOf(player, key);
        if (key === 'critChance') v += def.critChanceBonus || 0;
        else if (key === 'critDamage') v += def.critMultBonus || 0;
        return v;
      }
    };
  }

  function rollAmount(world, player, def) {
    return world.rng.int(def.baseDamage[0], def.baseDamage[1]) + statOf(player, 'damage');
  }

  /**
   * Resolves and applies one weapon hit on `target` through combat. `from` is the point the force comes from.
   * Returns the combat result. Life steal goes to the real player (the attacker passed to combat is a positioned shim).
   */
  function hitTarget(world, player, def, target, amount, fromX, fromY) {
    var combat = RPG.world.combat;
    var attacker = { x: fromX, y: fromY, stats: weaponStats(player, def) };
    var result = combat.resolveHit(attacker, target, {
      amount: amount, type: def.damageType, knockback: def.knockback, status: def.status
    }, world.rng);
    combat.applyHit(null, target, result);
    if (result.lifeSteal > 0 && !player.dead) player.hp = Math.min(player.maxHp, player.hp + result.lifeSteal);
    if (world.fx && world.fx.damageNumbers) {
      for (var i = 0; i < result.events.length; i++) {
        if (result.events[i].type === 'damageNumber') world.fx.damageNumbers.push(result.events[i]);
      }
    }
    return result;
  }

  function liveMonsters(world) {
    var out = [];
    var list = world.monsters || [];
    for (var i = 0; i < list.length; i++) if (!list[i].dead && list[i].hp > 0) out.push(list[i]);
    return out;
  }

  function pushEffect(world, spec) {
    spec.id = world.nextEntityId++;
    world.effects.push(spec);
    return spec;
  }

  function spawnProjectile(world, player, def, angle, amount) {
    var pj = def.projectile;
    var p = {
      id: world.nextEntityId++, kind: 'projectile', team: player.team || 0, ownerId: player.id, weaponId: def.id,
      x: player.x, y: player.y, vx: Math.cos(angle) * pj.speed, vy: Math.sin(angle) * pj.speed, r: pj.radius,
      angle: angle, amount: amount, life: pj.lifetime, maxLife: Math.min(pj.lifetime, def.range / pj.speed),
      pierceLeft: pj.pierce, shape: pj.shape, color: def.color, hitIds: [], dead: false
    };
    p.life = p.maxLife;
    world.projectiles.push(p);
    return p;
  }

  // ---- behaviours -------------------------------------------------------------------------------------------------
  // Each has fire(world, player, def, atk) called once at the start of `active`; aoeGround also has start() at tryAttack.

  var BEHAVIORS = {
    /** Melee arc in front of the player: every live monster inside the cone is hit at once. */
    swing: {
      fire: function (world, player, def, atk) {
        var hits = [], targets = liveMonsters(world);
        for (var i = 0; i < targets.length; i++) {
          var m = targets[i];
          if (!RPG.world.collision.inCone(player.x, player.y, atk.dir, def.arc / 2, def.range, m.x, m.y, m.r || 0)) continue;
          hitTarget(world, player, def, m, rollAmount(world, player, def), player.x, player.y);
          hits.push(m.id);
        }
        pushEffect(world, { kind: 'swing', x: player.x, y: player.y, dir: atk.dir, arc: def.arc, range: def.range,
          ttl: def.active, color: def.color, hits: hits });
      }
    },

    /** One projectile along the aim direction; combat happens as it travels (updateProjectiles). */
    projectile: {
      fire: function (world, player, def, atk) {
        spawnProjectile(world, player, def, atk.dir, rollAmount(world, player, def));
      }
    },

    /** projectileCount projectiles fanned evenly across def.arc, centred on the aim direction. */
    spread: {
      fire: function (world, player, def, atk) {
        var n = Math.max(1, def.projectileCount | 0);
        for (var i = 0; i < n; i++) {
          var off = n === 1 ? 0 : -def.arc / 2 + def.arc * i / (n - 1);
          spawnProjectile(world, player, def, atk.dir + off, rollAmount(world, player, def));
        }
      }
    },

    /** Ground circle at the aimed point (clamped to range): shows a telegraph while winding up, hits everything inside it. */
    aoeGround: {
      start: function (world, player, def, atk) {
        pushEffect(world, { kind: 'telegraph', x: atk.tx, y: atk.ty, radius: def.aoeRadius, ttl: def.windup / speedFactor(player),
          color: def.color });
      },
      fire: function (world, player, def, atk) {
        var targets = liveMonsters(world), hits = [];
        for (var i = 0; i < targets.length; i++) {
          var m = targets[i];
          var dx = m.x - atk.tx, dy = m.y - atk.ty, reach = def.aoeRadius + (m.r || 0);
          if (dx * dx + dy * dy > reach * reach) continue;
          hitTarget(world, player, def, m, rollAmount(world, player, def), atk.tx, atk.ty);
          hits.push(m.id);
        }
        pushEffect(world, { kind: 'aoe', x: atk.tx, y: atk.ty, radius: def.aoeRadius, ttl: def.active, color: def.color,
          damageType: def.damageType, hits: hits });
      }
    }
  };

  // ---- attack state machine ---------------------------------------------------------------------------------------

  /**
   * Starts an attack toward `aim` (world coordinates). Returns false when the player is mid-attack, the weapon is on
   * cooldown, the weapon is missing/unimplemented or the resource is short; true once the windup has begun.
   */
  function tryAttack(world, player, aim) {
    var def = currentWeapon(player).def;
    var atk = ensureAttack(player);
    if (!def || !BEHAVIORS[def.behavior]) return false;
    if (atk.state !== 'idle' || atk.cd > 0) return false;
    var cost = def.resourceCost || 0;
    if ((player.resource || 0) < cost) return false;

    player.resource -= cost;
    var dx = aim.x - player.x, dy = aim.y - player.y;
    var dist = Math.sqrt(dx * dx + dy * dy);
    atk.dir = dist < 1e-9 ? (player.facing || 0) : Math.atan2(dy, dx);
    player.facing = atk.dir;
    atk.weaponId = def.id;
    atk.state = 'windup';
    atk.t = 0;
    atk.cd = attackInterval(player);
    if (def.behavior === 'aoeGround') {
      var reach = Math.min(dist, def.range);
      atk.tx = player.x + Math.cos(atk.dir) * reach;
      atk.ty = player.y + Math.sin(atk.dir) * reach;
    }
    var b = BEHAVIORS[def.behavior];
    if (b.start) b.start(world, player, def, atk);
    return true;
  }

  var NEXT = { windup: 'active', active: 'recover', recover: 'idle' };

  /** Advances the cooldown and the windup -> active -> recover frames by dt; the behaviour fires on entering `active`. */
  function updateAttack(world, player, dt) {
    var atk = ensureAttack(player);
    if (atk.cd > 0) atk.cd = Math.max(0, atk.cd - dt);
    if (atk.state === 'idle') return;
    var def = weaponDefs()[atk.weaponId];
    if (!def) { atk.state = 'idle'; atk.t = 0; return; }
    var speed = speedFactor(player);
    var left = dt;
    atk.t += left;
    while (atk.state !== 'idle') {
      var dur = def[atk.state] / speed;
      if (atk.t < dur) break;
      atk.t -= dur;
      atk.state = NEXT[atk.state];
      if (atk.state === 'active') BEHAVIORS[def.behavior].fire(world, player, def, atk);
    }
    if (atk.state === 'idle') atk.t = 0;
  }

  /**
   * Advances projectiles by dt: moves them, stops at walls, expires them, and hits the first monsters they touch
   * (each monster once; the projectile is consumed after pierce + 1 hits).
   */
  function updateProjectiles(world, dt) {
    var list = world.projectiles || [];
    var map = world.area && world.area.map;
    var player = world.player;
    for (var i = list.length - 1; i >= 0; i--) {
      var p = list[i];
      if (p.skillId) continue; // skill projectiles are advanced by world/skill-effects.js
      p.life -= dt;
      if (map) { if (RPG.world.collision.sweepProjectile(map, p, dt).hitWall) p.dead = true; }
      else { p.x += p.vx * dt; p.y += p.vy * dt; }
      if (!p.dead && p.life <= 0) p.dead = true;
      if (!p.dead) {
        var def = weaponDefs()[p.weaponId];
        var targets = liveMonsters(world);
        for (var j = 0; j < targets.length && !p.dead; j++) {
          var m = targets[j];
          if (p.hitIds.indexOf(m.id) >= 0) continue;
          var dx = m.x - p.x, dy = m.y - p.y, reach = p.r + (m.r || 0);
          if (dx * dx + dy * dy > reach * reach) continue;
          p.hitIds.push(m.id);
          if (player && def) hitTarget(world, player, def, m, p.amount, p.x - Math.cos(p.angle), p.y - Math.sin(p.angle));
          if (p.pierceLeft > 0) p.pierceLeft--; else p.dead = true;
        }
      }
      if (p.dead) RPG.core.util.removeAt(list, i);
    }
  }

  /**
   * Changes the active weapon slot. Arg: a slot index (>= 0), a negative number or { delta } to step through the
   * non-empty slots (wrapping). Returns false mid-attack, for an empty/unknown slot or when nothing changes.
   */
  function switchWeapon(world, player, slotIndexOrDelta) {
    var atk = ensureAttack(player);
    if (atk.state !== 'idle') return false;
    var slots = player.weaponSlots || [];
    var cur = player.activeWeapon || 0;
    var target = -1;
    var delta = typeof slotIndexOrDelta === 'object' && slotIndexOrDelta ? slotIndexOrDelta.delta : slotIndexOrDelta < 0 ? slotIndexOrDelta : null;
    if (delta !== null && delta !== undefined) {
      var step = delta < 0 ? -1 : 1;
      for (var k = 1; k <= MAX_SLOTS; k++) {
        var idx = (((cur + step * k) % MAX_SLOTS) + MAX_SLOTS) % MAX_SLOTS;
        if (defFromSlot(slots[idx])) { target = idx; break; }
      }
    } else if (typeof slotIndexOrDelta === 'number' && slotIndexOrDelta % 1 === 0 && slotIndexOrDelta < MAX_SLOTS) {
      target = defFromSlot(slots[slotIndexOrDelta]) ? slotIndexOrDelta : -1;
    }
    if (target < 0 || target === cur) return false;
    player.activeWeapon = target;
    return true;
  }

  RPG.world.weapons = {
    BEHAVIORS: BEHAVIORS,
    tryAttack: tryAttack, updateAttack: updateAttack, updateProjectiles: updateProjectiles,
    switchWeapon: switchWeapon, currentWeapon: currentWeapon, attackInterval: attackInterval, moveFactor: moveFactor
  };
})(globalThis.RPG = globalThis.RPG || {});

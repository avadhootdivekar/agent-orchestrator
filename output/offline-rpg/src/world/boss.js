/* Boss mechanics: resolves the intents src/world/ai.js emits for a boss into world effects. Headless: no DOM.
     update(world, boss, intent, dt)
       world = { effects: [], players: [{ x, y, hp, dead? }], spawnMonster?(defId, x, y) -> monster,
                 damagePlayer?(player, amount, info), onPhaseChange?(boss, from, to, special) }
   A 'telegraph' action puts a warning effect on the ground; the matching 'attack' action only arms it, and the damage
   lands when the warning's delay has run out, to players still inside the shape. A player who left is never hit.
   'ember' circles leave a burning-ground zone that ticks every `tickEvery` seconds; a bright circle opens a vulnerable
   window (damage taken is multiplied by VULNERABLE_MULT). Phase changes fire hooks exactly once per phase. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var EMBER_TICK_EVERY = 1;       // seconds between burning-ground ticks
  var EMBER_TICK_MULT = 0.25;     // each tick deals this fraction of the boss's base damage
  var VULNERABLE_MULT = 1.5;      // damage multiplier on the boss while the vulnerable window is open
  var ENRAGE_DAMAGE_MULT = 1.25;  // outgoing damage after the 'enrage' phase special
  var ADD_RING = 70;              // summoned adds appear this far from the boss, spread evenly

  var hooks = [];

  function defOf(m) { return m.def || RPG.data.monsters[m.defId]; }
  function dist(ax, ay, bx, by) { var dx = bx - ax, dy = by - ay; return Math.sqrt(dx * dx + dy * dy); }

  function fightOf(boss) {
    if (!boss.fight) boss.fight = { firedPhases: {}, vulnerableT: 0, enraged: false, adds: [] };
    return boss.fight;
  }

  function baseDamage(boss, mult) {
    var f = fightOf(boss);
    return defOf(boss).baseStats.damage * mult * (f.enraged ? ENRAGE_DAMAGE_MULT : 1);
  }

  // Is point (px,py) inside the shape? 'line' is a capsule along (dirX,dirY) for `range`, `radius` wide.
  function inShape(e, px, py) {
    if (e.shape === 'line') {
      var rx = px - e.x, ry = py - e.y;
      var along = Math.max(0, Math.min(e.range, rx * e.dirX + ry * e.dirY));
      return dist(rx, ry, along * e.dirX, along * e.dirY) <= e.radius;
    }
    return dist(e.x, e.y, px, py) <= e.radius;
  }

  function hurt(world, p, amount, info) {
    if (world.damagePlayer) { world.damagePlayer(p, amount, info); return; }
    p.hp -= amount;
    if (p.hp <= 0) { p.hp = 0; p.dead = true; }
  }

  function playersIn(world, test) {
    return (world.players || []).filter(function (p) { return !p.dead && test(p); });
  }

  function resolveStrike(world, boss, e) {
    var amount = baseDamage(boss, e.damageMult);
    playersIn(world, function (p) { return inShape(e, p.x, p.y); }).forEach(function (p) {
      hurt(world, p, amount, { source: 'boss', attackId: e.attackId, amount: amount });
    });
    e.done = true;
  }

  function findWarning(world, attackId) {
    for (var i = 0; i < world.effects.length; i++) {
      var e = world.effects[i];
      if (e.kind === 'warning' && e.attackId === attackId && !e.armed && !e.done) return e;
    }
    return null;
  }

  function tickEffects(world, boss, dt) {
    var f = fightOf(boss);
    f.vulnerableT = Math.max(0, f.vulnerableT - dt);
    world.effects.forEach(function (e) {
      if (e.owner !== boss) return;
      if (e.kind === 'warning') {
        e.remaining -= dt;
        if (e.armed && e.remaining <= 0) resolveStrike(world, boss, e);
      } else if (e.kind === 'zone') {
        e.remaining -= dt;
        e.tickT -= dt;
        while (e.tickT <= 0 && !e.done) {
          e.tickT += e.tickEvery;
          playersIn(world, function (p) { return inShape(e, p.x, p.y); }).forEach(function (p) {
            hurt(world, p, e.damage, { source: 'boss', attackId: e.attackId, amount: e.damage, zone: e.zone });
          });
          if (e.remaining <= 0) e.done = true;
        }
        if (e.remaining <= 0) e.done = true;
      }
    });
    world.effects = world.effects.filter(function (e) { return e.owner !== boss || !e.done; });
  }

  function doSummon(world, boss, a) {
    var defs = RPG.data.monsters, f = fightOf(boss);
    if (!defs[a.defId] || !world.spawnMonster) return;
    for (var i = 0; i < a.count; i++) {
      var ang = (i / a.count) * Math.PI * 2;
      var m = world.spawnMonster(a.defId, boss.x + Math.cos(ang) * ADD_RING, boss.y + Math.sin(ang) * ADD_RING);
      if (m) f.adds.push(m);
    }
  }

  function doPhase(world, boss, a) {
    var f = fightOf(boss);
    if (f.firedPhases[a.to]) return;                 // each phase announces itself once
    f.firedPhases[a.to] = true;
    if (a.special === 'enrage') f.enraged = true;
    var args = [boss, a.from, a.to, a.special];
    hooks.slice().forEach(function (h) { h.apply(null, args); });
    if (world.onPhaseChange) world.onPhaseChange.apply(world, args);
  }

  function doAttack(world, boss, a) {
    var w = findWarning(world, a.attackId);
    if (w) {                                         // damage only ever resolves through a warning
      w.armed = true; w.damageMult = a.damageMult;
      if (w.remaining <= 0) resolveStrike(world, boss, w);
    }
    if (a.ember && w) {
      world.effects.push({
        kind: 'zone', zone: 'ember', owner: boss, attackId: a.attackId, shape: 'circle',
        x: a.x, y: a.y, radius: a.radius, remaining: a.ember, tickEvery: EMBER_TICK_EVERY,
        tickT: EMBER_TICK_EVERY, damage: baseDamage(boss, EMBER_TICK_MULT),
      });
    }
    if (a.vulnerable) fightOf(boss).vulnerableT = a.vulnerable;
  }

  /** Applies one AI intent to the world and advances every effect owned by this boss by `dt`. */
  function update(world, boss, intent, dt) {
    world.effects = world.effects || [];
    tickEffects(world, boss, dt);
    ((intent && intent.actions) || []).forEach(function (a) {
      if (a.type === 'telegraph') {
        world.effects.push({
          kind: 'warning', owner: boss, attackId: a.attackId, shape: a.shape, x: a.x, y: a.y,
          dirX: a.dirX, dirY: a.dirY, radius: a.radius, range: a.range, remaining: a.duration, armed: false,
        });
      } else if (a.type === 'attack') doAttack(world, boss, a);
      else if (a.type === 'summon') doSummon(world, boss, a);
      else if (a.type === 'phase') doPhase(world, boss, a);
    });
  }

  function onPhaseChange(fn) {
    hooks.push(fn);
    return function () { var i = hooks.indexOf(fn); if (i >= 0) hooks.splice(i, 1); };
  }

  /** Multiplier to apply to damage the boss takes right now. */
  function damageTakenMult(boss) { return boss.fight && boss.fight.vulnerableT > 0 ? VULNERABLE_MULT : 1; }

  RPG.world.boss = {
    update: update, onPhaseChange: onPhaseChange, damageTakenMult: damageTakenMult, inShape: inShape,
    EMBER_TICK_EVERY: EMBER_TICK_EVERY, VULNERABLE_MULT: VULNERABLE_MULT,
  };
})(globalThis.RPG = globalThis.RPG || {});

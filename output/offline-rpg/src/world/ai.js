/* Monster AI (DATA-DEFINITIONS §5): one behaviour per `ai` id in src/data/monsters.js. Headless: no DOM, no rendering.
   think(monster, view, rng, dt) returns an intent and never touches the world; the sim applies it.
     monster = { x, y, hp, maxHp, dead?, defId | def, ai?: state }   (the only thing think writes is `monster.ai`)
     view    = { target: { x, y, dead? } | null, summonCount?(monster) -> number }
     intent  = { vx, vy, facing, actions: [Action] }, actions are ordered; an 'attack' is always preceded by its 'telegraph'
       { type:'telegraph', attackId, shape, x, y, dirX, dirY, radius, range, duration }
       { type:'attack', attackId, kind, x, y, dirX, dirY, radius, range, damageMult, projectile?, bright?, vulnerable? }
       { type:'summon', attackId, defId, count }   { type:'explode', radius }   { type:'phase', from, to, special }
   An attack starts on one call (telegraph, no progress that call) and can only resolve on a later call, so damage can
   never land in the same tick as, or before, its warning. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var DASH_TIME = 0.35;         // seconds a boss charge lasts after its windup
  var TANK_SPEED_MULT = 0.6;    // tanks advance slower than their listed speed: a slow, inevitable approach
  var BAND = 0.15;              // kiters hold preferredDistance * (1 +/- BAND)
  var SUMMON_COUNT = 2;         // minions per summon action

  function dist(ax, ay, bx, by) { var dx = bx - ax, dy = by - ay; return Math.sqrt(dx * dx + dy * dy); }
  function unit(dx, dy) { var l = Math.sqrt(dx * dx + dy * dy); return l < 1e-9 ? { x: 1, y: 0 } : { x: dx / l, y: dy / l }; }
  function defOf(m) { return m.def || RPG.data.monsters[m.defId]; }
  function speedOf(def) { return def.baseStats.speed; }

  function newState(def, rng) {
    return {
      aggro: false, cd: 0, atk: null, dash: null, fuse: null,
      side: rng.chance(0.5) ? 1 : -1,       // flanker's preferred side
      summonT: def.aiParams.summonInterval || 0,
      phase: 0, circles: 0,
    };
  }

  function emptyIntent(m) { return { vx: 0, vy: 0, facing: m.ai.facing || 0, actions: [] }; }

  function moveToward(it, dirX, dirY, speed) {
    it.vx = dirX * speed; it.vy = dirY * speed; it.facing = Math.atan2(dirY, dirX);
  }

  // Latches aggro inside aggroRange and drops it beyond leashRange. Returns the target when engaged, else null.
  function engage(m, def, view) {
    var s = m.ai, t = view && view.target;
    if (!t || t.dead) { s.aggro = false; return null; }
    var d = dist(m.x, m.y, t.x, t.y);
    if (d <= def.aiParams.aggroRange) s.aggro = true;
    else if (d > def.aiParams.leashRange) s.aggro = false;
    return s.aggro ? t : null;
  }

  function telegraphShape(atk) {
    if (atk.telegraph) return atk.telegraph;
    return atk.kind === 'aoe' || atk.kind === 'nova' ? 'circle' : atk.kind === 'projectile' ? 'line' : 'cone';
  }

  // Where an attack is anchored: ground attacks land on the target's position at the start of the windup.
  function startAttack(m, def, atk, t, it) {
    var s = m.ai, d = unit(t.x - m.x, t.y - m.y);
    var onSelf = atk.kind === 'nova' || atk.kind === 'summon' || (atk.kind === 'aoe' && def.ai === 'exploder');
    var ground = atk.kind === 'aoe' && !onSelf;
    s.atk = { atk: atk, t: atk.windup, x: ground ? t.x : m.x, y: ground ? t.y : m.y, dirX: d.x, dirY: d.y };
    it.facing = Math.atan2(d.y, d.x);
    it.actions.push({
      type: 'telegraph', attackId: atk.id, shape: telegraphShape(atk), x: s.atk.x, y: s.atk.y,
      dirX: d.x, dirY: d.y, radius: atk.radius, range: atk.range, duration: atk.windup,
    });
  }

  // Advances a windup in progress. Returns true while the monster is committed to an attack (it must stand still).
  function tickWindup(m, def, dt, it) {
    var s = m.ai, a = s.atk;
    if (!a) return false;
    a.t -= dt;
    if (a.t > 0) return true;
    s.atk = null;
    s.cd = def.aiParams.attackCooldown;
    var atk = a.atk;
    if (atk.kind === 'summon') {
      it.actions.push({ type: 'summon', attackId: atk.id, defId: atk.summonDefId, count: SUMMON_COUNT });
      return true;
    }
    var act = {
      type: 'attack', attackId: atk.id, kind: atk.kind, x: a.x, y: a.y, dirX: a.dirX, dirY: a.dirY,
      radius: atk.radius, range: atk.range, damageMult: atk.damageMult,
    };
    if (atk.projectile) act.projectile = atk.projectile;
    if (def.ai === 'boss' && atk.id === 'ember-circles') {
      var every = def.mechanic.params.emberEvery;
      s.circles++;
      act.ember = def.mechanic.params.emberDuration;
      if (s.circles % every === 0) { act.bright = true; act.vulnerable = def.mechanic.params.vulnerableDuration; }
    }
    if (atk.kind === 'charge') s.dash = { t: DASH_TIME, dirX: a.dirX, dirY: a.dirY, speed: atk.range / DASH_TIME };
    it.actions.push(act);
    if (atk.kind === 'aoe' && def.ai === 'exploder') {
      it.actions.push({ type: 'explode', radius: atk.radius });
      m.ai.exploded = true;
    }
    return true;
  }

  function tickDash(m, dt, it) {
    var s = m.ai, d = s.dash;
    if (!d) return false;
    d.t -= dt;
    if (d.t <= 0) { s.dash = null; return false; }
    moveToward(it, d.dirX, d.dirY, d.speed);
    return true;
  }

  function pickAttack(def, rng, minPhase, allowed) {
    var list = def.attacks.filter(function (a) {
      return (!allowed || allowed.indexOf(a.id) >= 0) && (a.minPhase === undefined || a.minPhase <= minPhase);
    });
    return rng.weighted(list, function (a) { return a.weight || 1; });
  }

  // chase until in range, then wind up and strike.
  function meleeLike(speedMult) {
    return function (m, def, t, rng, dt, it, view) {
      var s = m.ai, p = def.aiParams, d = dist(m.x, m.y, t.x, t.y);
      if (d > p.attackRange * 0.9) {
        var u = unit(t.x - m.x, t.y - m.y);
        moveToward(it, u.x, u.y, speedOf(def) * speedMult);
      } else if (s.cd <= 0) {
        startAttack(m, def, pickAttack(def, rng, 0), t, it);
      } else {
        it.facing = Math.atan2(t.y - m.y, t.x - m.x);
      }
    };
  }

  // Approaches along a curved path offset to one side, only squaring up to the target once close.
  function flanker(m, def, t, rng, dt, it, view) {
    var s = m.ai, p = def.aiParams, d = dist(m.x, m.y, t.x, t.y);
    if (d <= p.attackRange * 0.9) {
      if (s.cd <= 0) startAttack(m, def, pickAttack(def, rng, 0), t, it);
      else it.facing = Math.atan2(t.y - m.y, t.x - m.x);
      return;
    }
    var base = Math.atan2(t.y - m.y, t.x - m.x);
    var close = p.attackRange * 3;
    var k = Math.max(0, Math.min(1, (d - close) / Math.max(1, p.aggroRange - close)));
    var ang = base + s.side * (p.flankAngle || 1) * k;
    moveToward(it, Math.cos(ang), Math.sin(ang), speedOf(def));
  }

  // Holds the preferred-distance band: backs off when too close, closes in when too far, fires from inside it.
  function kiter(m, def, t, rng, dt, it, view) {
    var s = m.ai, p = def.aiParams, d = dist(m.x, m.y, t.x, t.y);
    var lo = p.preferredDistance * (1 - BAND), hi = p.preferredDistance * (1 + BAND);
    var u = unit(t.x - m.x, t.y - m.y), spd = speedOf(def);
    var fleeing = def.ai === 'summoner' && m.hp / m.maxHp < (p.retreatBelowHp || 0);
    if (fleeing) { moveToward(it, -u.x, -u.y, spd); return; }
    if (d < lo) moveToward(it, -u.x, -u.y, spd);
    else if (d > hi) moveToward(it, u.x, u.y, spd);
    else it.facing = Math.atan2(u.y, u.x);
    if (d >= lo && d <= p.attackRange && s.cd <= 0) {
      var wantSummon = def.ai === 'summoner' && s.summonT <= 0 &&
        summonCount(view, m) < p.summonMax;
      var atk = wantSummon ? def.attacks.filter(function (a) { return a.kind === 'summon'; })[0]
        : pickAttack(def, rng, 0, def.ai === 'summoner' ? def.attacks.filter(function (a) { return a.kind !== 'summon'; }).map(function (a) { return a.id; }) : null);
      if (atk) startAttack(m, def, atk, t, it);
    }
  }

  function summonCount(view, m) { return view && view.summonCount ? view.summonCount(m) : 0; }

  function exploder(m, def, t, rng, dt, it, view) {
    var s = m.ai, p = def.aiParams, d = dist(m.x, m.y, t.x, t.y);
    if (d > p.attackRange) {
      var u = unit(t.x - m.x, t.y - m.y);
      moveToward(it, u.x, u.y, speedOf(def));
    } else {
      startAttack(m, def, def.attacks[0], t, it);
      s.atk.t = p.fuse;                       // the fuse, not the generic windup, sets the countdown
      it.actions[it.actions.length - 1].duration = p.fuse;
    }
  }

  function summoner(m, def, t, rng, dt, it, view) {
    var s = m.ai, p = def.aiParams;
    s.summonT = Math.max(0, s.summonT - dt);
    kiter(m, def, t, rng, dt, it, view);
    if (s.atk && s.atk.atk.kind === 'summon') s.summonT = p.summonInterval;
  }

  function phaseIndex(def, frac) {
    var idx = 0;
    for (var i = 0; i < def.phases.length; i++) if (def.phases[i].hpBelow >= frac) idx = i;
    return idx;
  }

  function boss(m, def, t, rng, dt, it, view) {
    var s = m.ai, p = def.aiParams, frac = m.maxHp > 0 ? m.hp / m.maxHp : 1;
    var idx = Math.max(s.phase, phaseIndex(def, frac));      // phases only advance
    if (idx !== s.phase) {
      it.actions.push({ type: 'phase', from: s.phase, to: idx, special: def.phases[idx].special || null });
      s.phase = idx;
      s.atk = null;                                           // a fresh phase re-announces its next attack
      s.cd = Math.max(s.cd, 1);
      return;
    }
    var ph = def.phases[s.phase], u = unit(t.x - m.x, t.y - m.y), d = dist(m.x, m.y, t.x, t.y);
    if (s.cd > 0) {
      if (d > p.attackRange * 0.5) moveToward(it, u.x, u.y, speedOf(def) * ph.speedMult);
      else it.facing = Math.atan2(u.y, u.x);
      return;
    }
    startAttack(m, def, pickAttack(def, rng, s.phase, ph.attacks), t, it);
  }

  var BEHAVIOURS = {
    melee: meleeLike(1),
    flanker: flanker,
    ranged: kiter,
    tank: meleeLike(TANK_SPEED_MULT),
    exploder: exploder,
    summoner: summoner,
    boss: boss,
  };

  /** Runs one AI tick. Mutates only `monster.ai`; everything the world must do comes back in the intent. */
  function think(monster, view, rng, dt) {
    var def = defOf(monster), beh = def && BEHAVIOURS[def.ai];
    if (!beh) throw new Error('ai: no behaviour for ' + (def && def.ai));
    if (!monster.ai) monster.ai = newState(def, rng);
    var s = monster.ai, it = emptyIntent(monster);
    if (monster.dead || s.exploded) return it;
    s.cd = Math.max(0, s.cd - dt);
    var target = engage(monster, def, view);
    if (target) {
      var wasWinding = !!s.atk;
      if (tickDash(monster, dt, it)) { /* committed to a charge */ }
      else if (wasWinding && tickWindup(monster, def, dt, it)) { /* standing still while the windup runs */ }
      else beh(monster, def, target, rng, dt, it, view);
    }
    s.facing = it.facing;
    return it;
  }

  RPG.world.ai = { think: think, BEHAVIOURS: BEHAVIOURS, phaseIndex: phaseIndex, TANK_SPEED_MULT: TANK_SPEED_MULT, BAND: BAND };
})(globalThis.RPG = globalThis.RPG || {});

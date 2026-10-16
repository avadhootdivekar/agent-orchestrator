/* Player entity and its per-tick stage (ARCHITECTURE §5, INTEGRATION-PLAN §2.2 stages 1-4). Headless: no DOM.
   `RPG.sim.stage.player` sanitises the intent, then runs dash/i-frames, regen, movement, weapon switch and attack, and
   skill casting, in that order. It reads the intent produced by input/intent.js and never touches input or render code.
   Optional dependencies (world/weapons, world/skills, world/combat) are skipped when absent, never thrown on. */
(function (RPG) {
  'use strict';

  RPG.sim = RPG.sim || {};
  RPG.sim.stage = RPG.sim.stage || {};

  // Base (level 1) values from DATA-DEFINITIONS §1, owned by RPG.world.StatBlock (world/stats.js).
  var BASE_STATS = new RPG.world.StatBlock().snapshot();
  var MOVE_SPEED_CAP = 320;

  var PLAYER_RADIUS = 10;
  // px/s^2. Reaching full speed takes moveSpeed / ACCEL seconds (~0.12 s at 170); stopping is a little snappier.
  var ACCEL = 1400;
  var DECEL = 1800;

  // Dash: a short burst along the move direction (else toward the aim). Its duration is also the i-frame window.
  var DASH_SPEED = 600;      // px/s while the burst lasts
  var DASH_DURATION = 0.15;  // s
  var DASH_COOLDOWN = 1.0;   // s
  var DASH_COST = 15;        // resource spent per dash
  var IFRAME_STATUS = 'invuln';
  var SKILL_SLOTS = 7;       // bar slots 0..6 (keys 1-5, Q, E)

  function moveSpeedOf(p) {
    return Math.min(p.stats.moveSpeed, MOVE_SPEED_CAP);
  }

  /** New player at (x, y). `world` supplies the entity id counter so ids survive save/load. */
  function create(world, x, y) {
    var p = RPG.core.entities.baseEntity('player', x, y, PLAYER_RADIUS, 0);
    p.id = world.nextEntityId++;
    p.name = 'Wayfarer';
    p.classId = 'wayfarer';
    p.level = 1;
    p.xp = 0;
    p.gold = 0;
    // `statBlock` holds base + modifiers; `stats` is its derived snapshot (call refreshStats after changing modifiers).
    p.statBlock = new RPG.world.StatBlock();
    p.stats = p.statBlock.snapshot();
    p.hp = p.maxHp = p.stats.maxHp;
    p.resource = p.stats.maxResource;
    p.resourceKind = 'energy';
    p.weaponSlots = [null, null, null];
    p.activeWeapon = 0;
    p.equipment = {};
    p.inventory = [];
    for (var i = 0; i < 24; i++) p.inventory.push(null);
    p.skills = { learned: {}, upgrades: {}, bar: [null, null, null, null, null, null, null], cooldowns: {}, points: 0, spentPoints: 0 };
    p.attack = { state: 'idle', t: 0, dir: 0 };
    // dx/dy: unit direction of the current dash burst; t: burst time left; cd: time until the next dash.
    p.dash = { t: 0, cd: 0, dx: 0, dy: 0 };
    // Previous-tick position, for render interpolation only.
    p.prevX = p.x;
    p.prevY = p.y;
    return p;
  }

  /** Recomputes `p.stats` from `p.statBlock`. */
  function refreshStats(p) {
    p.stats = p.statBlock.snapshot();
    return p.stats;
  }

  /** Moves v toward (tx, ty) by at most `maxChange` px/s. */
  function approach(p, tx, ty, maxChange) {
    var dx = tx - p.vx, dy = ty - p.vy;
    var len = Math.sqrt(dx * dx + dy * dy);
    if (len <= maxChange) { p.vx = tx; p.vy = ty; } else { p.vx += dx / len * maxChange; p.vy += dy / len * maxChange; }
  }

  /** Emits through the shared bus when it is present (headless tests may run without it). */
  function emit(type, payload) {
    var bus = RPG.core.events;
    if (bus) bus.emit(bus.NAMES[type], payload);
  }

  /** UI_SOUND with a named sound (payload convention, INTEGRATION-PLAN §4). */
  function emitSound(name) {
    emit('UI_SOUND', { name: name });
  }

  /**
   * Stage 1: reads the raw intent into plain values. `move` is re-clamped to unit length so a hand-built intent cannot
   * out-run moveSpeed; `ml` keeps the unclamped length (zero means "no movement key held"). A dead player gets an inert
   * sanitised intent so nothing acts. The input object is never mutated.
   */
  function sanitise(intent, dead) {
    if (dead || !intent) {
      return { mx: 0, my: 0, ml: 0, aim: null, attack: false, dash: false, skills: [], switchDelta: 0 };
    }
    var mv = intent.move || { x: 0, y: 0 };
    var mx = mv.x, my = mv.y;
    var ml = Math.sqrt(mx * mx + my * my);
    if (ml > 1) { mx /= ml; my /= ml; }
    var skills = [];
    for (var i = 0; i < SKILL_SLOTS; i++) skills.push(!!(intent.skills && intent.skills[i]));
    var sw = intent.switchWeapon || 0;
    return {
      mx: mx, my: my, ml: ml, aim: intent.aim || null,
      attack: !!intent.attack, dash: !!intent.dash, skills: skills,
      switchDelta: sw < 0 ? -1 : sw > 0 ? 1 : 0
    };
  }

  /** Stat multiplier on movement speed while attacking (1 when idle or without the weapons module). */
  function moveFactor(p) {
    return RPG.world.weapons ? RPG.world.weapons.moveFactor(p) : 1;
  }

  /** Decays the dash timers. Runs before a new dash can start, so a fresh dash keeps its full duration this tick. */
  function tickDash(p, dt) {
    var d = p.dash;
    if (d.t > 0) d.t = Math.max(0, d.t - dt);
    if (d.cd > 0) d.cd = Math.max(0, d.cd - dt);
  }

  /** Starts a dash when requested, off cooldown and affordable. Direction: move keys, else toward the aim, else facing. */
  function startDash(p, s) {
    var d = p.dash;
    if (!s.dash || d.cd > 0 || p.resource < DASH_COST) return;
    var dx = s.mx, dy = s.my;
    if (s.ml === 0) {
      dx = s.aim ? s.aim.x - p.x : Math.cos(p.facing || 0);
      dy = s.aim ? s.aim.y - p.y : Math.sin(p.facing || 0);
      var len = Math.sqrt(dx * dx + dy * dy);
      if (len > 1e-9) { dx /= len; dy /= len; } else { dx = Math.cos(p.facing || 0); dy = Math.sin(p.facing || 0); }
    }
    p.resource -= DASH_COST;
    d.t = DASH_DURATION;
    d.cd = DASH_COOLDOWN;
    d.dx = dx;
    d.dy = dy;
    grantIFrames(p);
  }

  /** Dash i-frames: the shared `invuln` status, so hurtPlayer (stage 10) can ignore hits for the burst. */
  function grantIFrames(p) {
    if (RPG.world.combat) RPG.world.combat.applyStatus(p, { id: IFRAME_STATUS, duration: DASH_DURATION });
  }

  /** Life and resource regen, capped at the maximum and never pushing a value above it. */
  function regen(p, dt) {
    if (p.dead) return;
    if (p.hp < p.stats.maxHp) p.hp = Math.min(p.stats.maxHp, p.hp + (p.stats.lifeRegen || 0) * dt);
    if (p.resource < p.stats.maxResource) p.resource = Math.min(p.stats.maxResource, p.resource + (p.stats.resourceRegen || 0) * dt);
  }

  /**
   * Advances the player's position by one tick. During a dash the velocity is the burst; otherwise it accelerates toward
   * the move target (scaled by the weapon's moveFactor). Movement only touches the player; collision uses the area map.
   */
  function movePlayer(world, s, dt) {
    var p = world.player;
    var map = world.area.map;
    p.prevX = p.x;
    p.prevY = p.y;

    if (p.dash.t > 0) {
      p.vx = p.dash.dx * DASH_SPEED;
      p.vy = p.dash.dy * DASH_SPEED;
    } else {
      var speed = moveSpeedOf(p) * moveFactor(p);
      approach(p, s.mx * speed, s.my * speed, (s.ml > 0 ? ACCEL : DECEL) * dt);
    }

    var wantX = p.vx * dt, wantY = p.vy * dt;
    var x0 = p.x, y0 = p.y;
    RPG.world.collision.moveCircle(map, p, wantX, wantY);
    // A wall that stopped most of an axis also kills that velocity, so the player does not "push" into it.
    if (Math.abs(p.x - x0) < Math.abs(wantX) * 0.5) p.vx = 0;
    if (Math.abs(p.y - y0) < Math.abs(wantY) * 0.5) p.vy = 0;

    if (s.aim) {
      var ax = s.aim.x - p.x, ay = s.aim.y - p.y;
      if (ax !== 0 || ay !== 0) p.facing = Math.atan2(ay, ax);
    }
  }

  /** Stage 3a: a requested weapon switch (-1 / +1 steps through non-empty slots). Emits a UI sound on success. */
  function switchWeapon(world, p, s) {
    var W = RPG.world.weapons;
    if (!W || !s.switchDelta) return;
    if (W.switchWeapon(world, p, { delta: s.switchDelta })) emitSound('ui');
  }

  /**
   * Stage 3b: starts an attack while the key is held, then advances the attack frames and the cooldown. `tryAttack`
   * returning false (cooldown, mid-attack, empty weapon, short resource) is normal, not an error.
   */
  function attack(world, p, s, dt) {
    var W = RPG.world.weapons;
    if (!W) return;
    if (s.attack && W.tryAttack(world, p, s.aim || p)) emitSound('attack');
    W.updateAttack(world, p, dt);
  }

  /** Stage 4: casts each skill whose key was pressed this tick, then ticks skill cooldowns. */
  function castSkills(world, p, s, dt) {
    var SK = RPG.world.skills;
    if (!SK) return;
    var aim = s.aim || p;
    for (var i = 0; i < SKILL_SLOTS; i++) {
      if (!s.skills[i]) continue;
      var r = SK.tryCastSkill(world, p, i, aim);
      if (r.ok) emit('SKILL_USED', { skillId: r.skillId, slot: i });
      else if (r.reason === 'cooldown' || r.reason === 'resource') emitSound('ui');
    }
    SK.updateSkills(world, p, dt);
  }

  /** Stages 1-4 of the tick, in plan order. `ctx` is `world.scratch` from sim/sim.js: { intent, dt, ... }. */
  function stagePlayer(world, ctx) {
    var p = world.player;
    var dt = ctx.dt;
    var s = sanitise(ctx.intent, p.dead);
    tickDash(p, dt);
    startDash(p, s);
    movePlayer(world, s, dt);
    regen(p, dt);
    switchWeapon(world, p, s);
    attack(world, p, s, dt);
    castSkills(world, p, s, dt);
  }

  /**
   * Movement-only step, kept as the pre-stage entry point. sim/sim.js runs `stage.player` instead once it is registered,
   * so this is only reached when a caller drives the player module directly.
   */
  function step(world, intent, dt) {
    movePlayer(world, sanitise(intent, world.player.dead), dt);
  }

  RPG.sim.stage.player = stagePlayer;

  RPG.sim.player = {
    BASE_STATS: BASE_STATS, MOVE_SPEED_CAP: MOVE_SPEED_CAP, PLAYER_RADIUS: PLAYER_RADIUS, ACCEL: ACCEL, DECEL: DECEL,
    DASH_SPEED: DASH_SPEED, DASH_DURATION: DASH_DURATION, DASH_COOLDOWN: DASH_COOLDOWN, DASH_COST: DASH_COST,
    IFRAME_STATUS: IFRAME_STATUS, SKILL_SLOTS: SKILL_SLOTS,
    create: create, step: step, refreshStats: refreshStats, stage: stagePlayer
  };
})(globalThis.RPG = globalThis.RPG || {});

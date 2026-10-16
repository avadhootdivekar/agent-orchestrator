/* Player entity and its per-tick step (ARCHITECTURE §5). Movement only: acceleration-based, collides with the tilemap.
   Headless: no DOM. Reads the intent produced by input/intent.js; never touches input or render code. */
(function (RPG) {
  'use strict';

  RPG.sim = RPG.sim || {};

  // Base (level 1) values from DATA-DEFINITIONS §1, owned by RPG.world.StatBlock (world/stats.js).
  var BASE_STATS = new RPG.world.StatBlock().snapshot();
  var MOVE_SPEED_CAP = 320;

  var PLAYER_RADIUS = 10;
  // px/s^2. Reaching full speed takes moveSpeed / ACCEL seconds (~0.12 s at 170); stopping is a little snappier.
  var ACCEL = 1400;
  var DECEL = 1800;

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
    p.dash = { t: 0, cd: 0 };
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

  /** Advances the player one tick from `intent` ({ move:{x,y}, aim:{x,y}, ... }). Mutates world.player only. */
  function step(world, intent, dt) {
    var p = world.player;
    var map = world.area.map;
    p.prevX = p.x;
    p.prevY = p.y;

    var mx = intent.move.x, my = intent.move.y;
    // Re-clamp to unit length so a hand-built intent cannot out-run moveSpeed.
    var ml = Math.sqrt(mx * mx + my * my);
    if (ml > 1) { mx /= ml; my /= ml; }
    var speed = moveSpeedOf(p);
    approach(p, mx * speed, my * speed, (ml > 0 ? ACCEL : DECEL) * dt);

    var wantX = p.vx * dt, wantY = p.vy * dt;
    var x0 = p.x, y0 = p.y;
    RPG.world.collision.moveCircle(map, p, wantX, wantY);
    // A wall that stopped most of an axis also kills that velocity, so the player does not "push" into it.
    if (Math.abs(p.x - x0) < Math.abs(wantX) * 0.5) p.vx = 0;
    if (Math.abs(p.y - y0) < Math.abs(wantY) * 0.5) p.vy = 0;

    if (intent.aim) {
      var ax = intent.aim.x - p.x, ay = intent.aim.y - p.y;
      if (ax !== 0 || ay !== 0) p.facing = Math.atan2(ay, ax);
    }
  }

  RPG.sim.player = {
    BASE_STATS: BASE_STATS, MOVE_SPEED_CAP: MOVE_SPEED_CAP, PLAYER_RADIUS: PLAYER_RADIUS, ACCEL: ACCEL, DECEL: DECEL,
    create: create, step: step, refreshStats: refreshStats
  };
})(globalThis.RPG = globalThis.RPG || {});

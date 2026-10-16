'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadSim, plain, intent, DT } = require('./load.js');
const { loadRPG } = require('../world/load.js');

const RPG = loadSim();
const P = RPG.sim.player;

function run(world, it, ticks) {
  for (let i = 0; i < ticks; i++) RPG.sim.step(world, it, DT);
}

function speedOf(p) {
  return Math.hypot(p.vx, p.vy);
}

test('player spawns on the spawn tile with the documented base stats', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  assert.equal(w.player.kind, 'player');
  assert.equal(w.player.x, 3.5 * 32);
  assert.equal(w.player.y, 14.5 * 32);
  assert.equal(w.player.stats.moveSpeed, 170);
  assert.equal(w.player.stats.maxHp, 100);
  assert.equal(w.player.hp, 100);
  assert.equal(w.player.inventory.length, 24);
});

test('acceleration is smooth: speed ramps up to moveSpeed and never exceeds it', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  const right = intent(1, 0, { x: 9999, y: w.player.y });
  RPG.sim.step(w, right, DT);
  const first = speedOf(w.player);
  assert.ok(first > 0 && first < 170 / 2, `first tick speed ${first} should be a small fraction of moveSpeed`);
  let prev = first;
  for (let i = 0; i < 30; i++) {
    RPG.sim.step(w, right, DT);
    const s = speedOf(w.player);
    assert.ok(s >= prev - 1e-9 && s <= 170 + 1e-9);
    prev = s;
  }
  assert.ok(Math.abs(speedOf(w.player) - 170) < 1e-9, 'reaches moveSpeed');
});

test('steady-state travel covers moveSpeed * time', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  const right = intent(1, 0);
  run(w, right, 30); // reach full speed
  const x0 = w.player.x;
  run(w, right, 30); // exactly 0.5 s at 170 px/s
  assert.ok(Math.abs(w.player.x - x0 - 85) < 1e-6, `moved ${w.player.x - x0}`);
});

test('diagonal movement is no faster than cardinal movement', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  const d = Math.SQRT1_2;
  run(w, intent(d, d), 40);
  assert.ok(Math.abs(speedOf(w.player) - 170) < 1e-6);
  // A hand-built over-length intent is re-clamped.
  const w2 = RPG.sim.createWorld({ seed: 1 });
  run(w2, intent(1, 1), 40);
  assert.ok(speedOf(w2.player) <= 170 + 1e-9);
});

test('releasing the keys decelerates to a stop', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  run(w, intent(1, 0), 30);
  RPG.sim.step(w, intent(0, 0), DT);
  assert.ok(speedOf(w.player) > 0 && speedOf(w.player) < 170, 'not an instant stop');
  run(w, intent(0, 0), 30);
  assert.equal(speedOf(w.player), 0);
});

test('moveSpeed is capped at 320 even with a larger stat', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  w.player.stats.moveSpeed = 1000;
  run(w, intent(1, 0), 60);
  assert.ok(speedOf(w.player) <= P.MOVE_SPEED_CAP + 1e-9);
});

test('walls block the player through the sim step', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  const left = intent(-1, 0);
  run(w, left, 240); // far longer than the 3.5-tile walk to the west wall
  const minX = 32 + P.PLAYER_RADIUS; // west wall tile column 0 ends at x = 32
  assert.ok(Math.abs(w.player.x - minX) < 1e-6, `stopped at x=${w.player.x}`);
  assert.equal(w.player.vx, 0, 'velocity into the wall is cancelled');
  assert.equal(RPG.world.collision.circleOverlapsSolid(w.area.map, w.player.x, w.player.y, w.player.r - 1e-6), false);
});

test('the player slides along a wall when moving diagonally into it', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  run(w, intent(-1, 0), 240);
  const y0 = w.player.y;
  run(w, intent(-Math.SQRT1_2, -Math.SQRT1_2), 30);
  assert.ok(w.player.y < y0 - 10, 'kept moving up along the wall');
  assert.ok(Math.abs(w.player.x - (32 + P.PLAYER_RADIUS)) < 1e-6);
});

test('the player never ends up inside a wall, however long it walks into the map edges', () => {
  const w = RPG.sim.createWorld({ seed: 7 });
  const dirs = [[1, 0], [0, 1], [-1, 0], [0, -1], [Math.SQRT1_2, Math.SQRT1_2], [-Math.SQRT1_2, Math.SQRT1_2]];
  for (const [x, y] of dirs) {
    run(w, intent(x, y), 400);
    assert.equal(RPG.world.collision.circleOverlapsSolid(w.area.map, w.player.x, w.player.y, w.player.r - 1e-6), false);
  }
});

test('facing follows the aim point', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  const p = w.player;
  RPG.sim.step(w, intent(0, 0, { x: p.x + 50, y: p.y }), DT);
  assert.ok(Math.abs(p.facing) < 1e-9);
  RPG.sim.step(w, intent(0, 0, { x: p.x, y: p.y + 50 }), DT);
  assert.ok(Math.abs(p.facing - Math.PI / 2) < 1e-9);
  RPG.sim.step(w, intent(0, 0, { x: p.x - 50, y: p.y }), DT);
  assert.ok(Math.abs(Math.abs(p.facing) - Math.PI) < 1e-9);
  const before = p.facing;
  RPG.sim.step(w, intent(0, 0, { x: p.x, y: p.y }), DT); // aim exactly on the player keeps the old facing
  assert.equal(p.facing, before);
});

// Scripted intents from an independent seeded stream, so the script itself is reproducible.
function script(seed, n) {
  const rng = RPG.core.Rng.create(seed);
  const out = [];
  let mx = 0, my = 0;
  for (let i = 0; i < n; i++) {
    if (i % 20 === 0) {
      mx = rng.int(-1, 1);
      my = rng.int(-1, 1);
    }
    const len = Math.hypot(mx, my) || 1;
    out.push(intent(mx / len, my / len, { x: rng.range(0, 1280), y: rng.range(0, 960) }));
  }
  return out;
}

function runScript(seed, steps) {
  const w = RPG.sim.createWorld({ seed });
  for (const it of steps) RPG.sim.step(w, it, DT);
  return plain({ player: w.player, tick: w.tick, time: w.time, rng: w.rng.getState() });
}

test('the sim is deterministic for a fixed seed and scripted intents', () => {
  const steps = script(42, 1200);
  assert.deepEqual(runScript(5, steps), runScript(5, steps));
  const a = runScript(5, steps);
  assert.equal(a.tick, 1200);
  assert.notEqual(a.player.x, 3.5 * 32, 'the script actually moved the player');
});

test('different scripted intents give different results', () => {
  assert.notDeepEqual(runScript(5, script(42, 600)), runScript(5, script(43, 600)));
});

test('stepping does not mutate the intent it was given', () => {
  const w = RPG.sim.createWorld({ seed: 1 });
  const it = intent(1, 0, { x: 100, y: 100 });
  const copy = plain(it);
  RPG.sim.step(w, it, DT);
  assert.deepEqual(plain(it), copy);
});

// ---- Stage 1-4 (`RPG.sim.stage.player`): dash, regen, weapon switch, attack, skills. ----
// Each test gets a fresh context. The optional modules (weapons, skills, combat) are fakes so one test controls one
// dependency; the real weapons module is covered by the block at the end.

function fakeBus(events) {
  return {
    NAMES: { SKILL_USED: 'skillUsed', UI_SOUND: 'uiSound' },
    emit(type, payload) { events.push([type, plain(payload)]); }
  };
}

function harness() {
  const R = loadSim();
  const calls = [];
  const events = [];
  const fx = { attackOk: true, switchOk: true, castResult: { ok: true, skillId: 'fake-skill' }, moveFactor: 1 };
  R.core.events = fakeBus(events);
  R.world.weapons = {
    moveFactor: () => fx.moveFactor,
    tryAttack: (w, p, aim) => { calls.push(['tryAttack', plain(aim)]); return fx.attackOk; },
    updateAttack: (w, p, dt) => { calls.push(['updateAttack', dt]); },
    switchWeapon: (w, p, arg) => { calls.push(['switchWeapon', plain(arg)]); return fx.switchOk; }
  };
  R.world.skills = {
    tryCastSkill: (w, p, slot) => { calls.push(['tryCastSkill', slot]); return fx.castResult; },
    updateSkills: (w, p, dt) => { calls.push(['updateSkills', dt]); }
  };
  R.world.combat = {
    applyStatus: (ent, spec) => {
      calls.push(['applyStatus', spec.id]);
      ent.status = ent.status || {};
      ent.status[spec.id] = { id: spec.id, remaining: spec.duration };
    }
  };
  const w = R.sim.createWorld({ seed: 1 });
  const tick = (it) => R.sim.stage.player(w, { intent: it, dt: DT });
  return { R, w, p: w.player, fx, calls, events, tick };
}

function withSkill(slot, extra) {
  const it = intent(0, 0, extra && extra.aim);
  it.skills = [];
  it.skills[slot] = true;
  return it;
}

test('the player stage is registered on RPG.sim.stage', () => {
  const { R } = harness();
  assert.equal(typeof R.sim.stage.player, 'function');
});

test('dash spends resource, starts a burst and grants i-frames for its duration', () => {
  const { R, p, calls, tick } = harness();
  const { DASH_COST, DASH_DURATION, DASH_SPEED, IFRAME_STATUS } = R.sim.player;
  tick(Object.assign(intent(1, 0), { dash: true }));
  assert.ok(calls.some((c) => c[0] === 'applyStatus' && c[1] === IFRAME_STATUS), 'invuln status applied');
  assert.equal(p.status[IFRAME_STATUS].remaining, DASH_DURATION);
  // Regen runs after the spend in the same tick (resource is below max), so allow for one tick of it.
  assert.ok(Math.abs(p.resource - (60 - DASH_COST + 6 * DT)) < 1e-9, `resource ${p.resource}`);
  assert.ok(Math.abs(p.vx - DASH_SPEED) < 1e-9, `burst speed ${p.vx}`);
  assert.ok(Math.abs(p.vy) < 1e-9);
});

test('a dash with no move key goes toward the aim point', () => {
  const { p, tick, R } = harness();
  tick(Object.assign(intent(0, 0, { x: 0, y: 0 }), { dash: true, aim: { x: p.x, y: p.y - 300 } }));
  assert.ok(Math.abs(p.vx) < 1e-9);
  assert.ok(p.vy < -R.sim.player.DASH_SPEED + 1e-6, `dashed up, vy=${p.vy}`);
});

test('dash is refused without enough resource and nothing is spent or granted', () => {
  const { R, p, calls, tick } = harness();
  p.resource = R.sim.player.DASH_COST - 1;
  tick(Object.assign(intent(1, 0), { dash: true }));
  assert.equal(p.dash.t, 0);
  assert.equal(calls.filter((c) => c[0] === 'applyStatus').length, 0);
  assert.ok(p.resource < R.sim.player.DASH_COST, 'resource not spent');
});

test('dash is gated by its cooldown and can be used again once it has elapsed', () => {
  const { R, p, calls, tick } = harness();
  const dashIt = Object.assign(intent(1, 0), { dash: true });
  tick(dashIt);
  for (let i = 0; i < 6; i++) tick(dashIt); // still on cooldown, the key is held
  assert.equal(calls.filter((c) => c[0] === 'applyStatus').length, 1, 'second press inside the cooldown is ignored');
  for (let i = 0; i < Math.ceil(R.sim.player.DASH_COOLDOWN / DT) + 2; i++) tick(intent(1, 0));
  tick(dashIt);
  assert.equal(calls.filter((c) => c[0] === 'applyStatus').length, 2, 'dash allowed after the cooldown');
  assert.ok(p.dash.cd > 0);
});

test('movement is scaled by the weapon moveFactor while attacking', () => {
  const { p, fx, tick } = harness();
  fx.moveFactor = 0.5;
  for (let i = 0; i < 60; i++) tick(intent(1, 0));
  assert.ok(Math.abs(p.vx - 85) < 1e-6, `speed ${p.vx}`);
});

test('life and resource regen fill toward the max and never overshoot it', () => {
  const { p, tick } = harness();
  p.hp = 50;
  p.resource = 0;
  for (let i = 0; i < 60; i++) tick(intent(0, 0));
  assert.ok(Math.abs(p.hp - 51) < 1e-6, `hp ${p.hp}`); // lifeRegen 1/s
  assert.ok(Math.abs(p.resource - 6) < 1e-6, `resource ${p.resource}`); // resourceRegen 6/s
  p.hp = p.stats.maxHp - 0.01;
  for (let i = 0; i < 60; i++) tick(intent(0, 0));
  assert.equal(p.hp, p.stats.maxHp);
});

test('a requested weapon switch calls weapons.switchWeapon with the step and emits a ui sound on success', () => {
  const { p, calls, events, fx, tick } = harness();
  tick(Object.assign(intent(0, 0), { switchWeapon: -1 }));
  assert.deepEqual(calls.find((c) => c[0] === 'switchWeapon'), ['switchWeapon', { delta: -1 }]);
  assert.deepEqual(events.find((e) => e[0] === 'uiSound'), ['uiSound', { name: 'ui' }]);
  events.length = 0;
  fx.switchOk = false;
  tick(Object.assign(intent(0, 0), { switchWeapon: 1 }));
  assert.equal(events.filter((e) => e[0] === 'uiSound').length, 0, 'no sound when the switch is refused');
  assert.ok(p.activeWeapon === 0);
});

test('no switch request means weapons.switchWeapon is never called', () => {
  const { calls, tick } = harness();
  tick(intent(0, 0));
  assert.equal(calls.filter((c) => c[0] === 'switchWeapon').length, 0);
});

test('attack starts while the key is held and always advances the attack frames', () => {
  const { calls, events, fx, tick } = harness();
  const attackIt = Object.assign(intent(0, 0, { x: 100, y: 0 }), { attack: true });
  tick(attackIt);
  assert.deepEqual(events.find((e) => e[0] === 'uiSound'), ['uiSound', { name: 'attack' }]);
  assert.deepEqual(calls.find((c) => c[0] === 'tryAttack')[1], { x: 100, y: 0 });
  assert.deepEqual(calls.find((c) => c[0] === 'updateAttack'), ['updateAttack', DT]);
  events.length = 0;
  fx.attackOk = false; // e.g. cooldown: tryAttack false is normal, not an error
  tick(attackIt);
  assert.equal(events.filter((e) => e[0] === 'uiSound').length, 0);
  assert.equal(calls.filter((c) => c[0] === 'updateAttack').length, 2);
});

test('pressing a skill key casts that bar slot and emits SKILL_USED; ticks skill cooldowns every tick', () => {
  const { calls, events, tick } = harness();
  tick(withSkill(2));
  assert.deepEqual(calls.find((c) => c[0] === 'tryCastSkill'), ['tryCastSkill', 2]);
  assert.deepEqual(events.find((e) => e[0] === 'skillUsed'), ['skillUsed', { skillId: 'fake-skill', slot: 2 }]);
  assert.deepEqual(calls.find((c) => c[0] === 'updateSkills'), ['updateSkills', DT]);
});

test('a failed skill press plays the ui sound for cooldown or resource, but not for an empty slot', () => {
  const { fx, events, tick } = harness();
  fx.castResult = { ok: false, reason: 'cooldown' };
  tick(withSkill(0));
  assert.deepEqual(events.find((e) => e[0] === 'uiSound'), ['uiSound', { name: 'ui' }]);
  events.length = 0;
  fx.castResult = { ok: false, reason: 'empty-slot' };
  tick(withSkill(1));
  assert.equal(events.filter((e) => e[0] === 'uiSound').length, 0);
  assert.equal(events.filter((e) => e[0] === 'skillUsed').length, 0);
});

test('a dead player does not move, regen, dash, attack, switch or cast', () => {
  const { p, calls, events, tick } = harness();
  p.dead = true;
  p.hp = 0;
  const before = { x: p.x, resource: p.resource };
  const it = Object.assign(intent(1, 0, { x: 999, y: 0 }), { dash: true, attack: true, switchWeapon: 1 });
  it.skills = [true, true];
  tick(it);
  assert.equal(p.x, before.x);
  assert.equal(p.resource, before.resource);
  assert.equal(p.hp, 0);
  assert.equal(p.dash.t, 0);
  // Cooldown ticks (updateAttack / updateSkills) still run; no action may start.
  assert.equal(calls.filter((c) => !['updateAttack', 'updateSkills'].includes(c[0])).length, 0, 'no action started');
  assert.equal(events.length, 0);
});

test('stage.player does not mutate the intent it is given', () => {
  const { tick } = harness();
  const it = Object.assign(withSkill(1, { aim: { x: 50, y: 0 } }), { move: { x: 3, y: 4 }, attack: true, dash: true, switchWeapon: 1 });
  const copy = plain(it);
  tick(it);
  assert.deepEqual(plain(it), copy);
});

// ---- Real world/weapons.js against the stage: attack cooldown, resource gating and weapon switch. ----

const REAL_WEAPON_FILES = [
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/entities.js', 'core/state.js', 'data/weapons.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/combat.js', 'world/weapons.js',
  'data/maps/test-map.js', 'sim/player.js', 'sim/sim.js'
];

function realWeaponsWorld() {
  const R = loadRPG(REAL_WEAPON_FILES);
  const events = [];
  R.core.events = fakeBus(events);
  const w = R.sim.createWorld({ seed: 1 });
  w.player.weaponSlots = ['sword', 'hunting-bow', 'arc-staff'];
  const tick = (it) => R.sim.stage.player(w, { intent: it, dt: DT });
  return { R, w, p: w.player, events, tick };
}

test('real weapons: attacks respect the attack cooldown while the key is held', () => {
  const { events, tick } = realWeaponsWorld();
  const held = Object.assign(intent(0, 0, { x: 200, y: 0 }), { attack: true });
  for (let i = 0; i < 20; i++) tick(held); // 0.33 s is inside the sword's 0.5 s interval
  assert.equal(events.filter((e) => e[0] === 'uiSound' && e[1].name === 'attack').length, 1);
  for (let i = 0; i < 40; i++) tick(held); // 1.0 s in total, so the cooldown has elapsed at least once
  assert.ok(events.filter((e) => e[0] === 'uiSound' && e[1].name === 'attack').length >= 2);
});

test('real weapons: a weapon whose resource cost is not met does not attack', () => {
  const { p, events, tick } = realWeaponsWorld();
  p.activeWeapon = 1; // hunting-bow costs 2 resource per attack
  p.resource = 1;
  const held = Object.assign(intent(0, 0, { x: 200, y: 0 }), { attack: true });
  tick(held);
  assert.equal(events.filter((e) => e[0] === 'uiSound' && e[1].name === 'attack').length, 0);
  assert.equal(p.attack.state, 'idle');
  p.resource = 5;
  tick(held);
  assert.equal(events.filter((e) => e[0] === 'uiSound' && e[1].name === 'attack').length, 1);
  assert.equal(p.attack.state !== 'idle', true);
});

test('real weapons: switching steps through the non-empty slots and wraps', () => {
  const { p, events, tick } = realWeaponsWorld();
  tick(Object.assign(intent(0, 0), { switchWeapon: -1 }));
  assert.equal(p.activeWeapon, 2, 'sword -> arc-staff (wraps backwards)');
  tick(Object.assign(intent(0, 0), { switchWeapon: 1 }));
  assert.equal(p.activeWeapon, 0, 'arc-staff -> sword (wraps forwards)');
  assert.equal(events.filter((e) => e[0] === 'uiSound' && e[1].name === 'ui').length, 2);
});

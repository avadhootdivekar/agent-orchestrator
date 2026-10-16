'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadSim, plain, intent, DT } = require('./load.js');

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

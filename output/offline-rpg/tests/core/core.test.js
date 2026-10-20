'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
// vm contexts have their own Object/Array prototypes; use loose deepEqual where values cross realms.
const deepEq = require('node:assert').deepEqual;
const fs = require('node:fs');
const path = require('node:path');
const { loadCore, CORE } = require('./helpers');

const RPG = loadCore();
const { Rng } = RPG.core;

test('rng: same seed gives same sequence, different seed differs', () => {
  const a = Rng.create(1234), b = Rng.create(1234), c = Rng.create(1235);
  const sa = [], sb = [], sc = [];
  for (let i = 0; i < 100; i++) { sa.push(a.next()); sb.push(b.next()); sc.push(c.next()); }
  deepEq(sa, sb);
  assert.notEqual(sa.join(), sc.join());
  assert.ok(sa.every(v => v >= 0 && v < 1));
});

test('rng: getState/setState resumes exactly', () => {
  const r = Rng.create(42);
  for (let i = 0; i < 17; i++) r.next();
  const st = r.getState();
  const expected = Array.from({ length: 50 }, () => r.next());
  const r2 = Rng.create(999);
  r2.setState(st);
  deepEq(Array.from({ length: 50 }, () => r2.next()), expected);
  assert.equal(Number.isInteger(st) && st >= 0 && st <= 0xffffffff, true);
});

test('rng: int bounds inclusive, weighted, shuffle, fork', () => {
  const r = Rng.create(7);
  const seen = new Set();
  for (let i = 0; i < 500; i++) seen.add(r.int(3, 6));
  deepEq([...seen].sort(), [3, 4, 5, 6]);
  assert.equal(r.weighted([{ w: 0 }, { w: 5, id: 'x' }], o => o.w).id, 'x');
  assert.equal(r.weighted([], () => 1), undefined);
  const arr = [1, 2, 3, 4, 5, 6];
  deepEq(Rng.create(3).shuffle(arr.slice()), Rng.create(3).shuffle(arr.slice()));
  deepEq(r.shuffle(arr.slice()).sort(), arr);
  const before = r.getState();
  const f1 = r.fork('loot'), f2 = r.fork('loot'), f3 = r.fork('map');
  assert.equal(r.getState(), before, 'fork must not advance parent');
  assert.equal(f1.next(), f2.next());
  assert.notEqual(Rng.create(before).fork('loot').next(), f3.next());
  assert.equal(Rng.seedFromString('hello'), Rng.seedFromString('hello'));
});

test('loop: N*dt of fake time -> N steps, one render per frame', () => {
  let t = 0, steps = 0, renders = [];
  const dt = 1 / 60;
  const loop = RPG.core.createLoop({
    dt, clock: { now: () => t }, scheduler: { request() { return 1; }, cancel() {} },
    step: () => { steps++; }, render: a => renders.push(a)
  });
  loop.frame(); // primes `last`
  assert.equal(steps, 0);
  const N = 30;
  for (let i = 0; i < N; i++) { t += dt; loop.frame(); }
  assert.equal(steps, N);
  assert.equal(renders.length, N + 1);
  assert.ok(renders.every(a => a >= 0 && a < 1));
});

test('loop: a long frame is clamped to maxSteps and alpha reflects remainder', () => {
  let t = 0, steps = 0, last;
  const dt = 1 / 60;
  const loop = RPG.core.createLoop({
    dt, maxSteps: 5, clock: { now: () => t }, scheduler: { request() { return 1; }, cancel() {} },
    step: () => { steps++; }, render: a => { last = a; }
  });
  loop.frame();
  t += 10; // huge stall
  loop.frame();
  assert.equal(steps, 5);
  // partial frame: 1.5 dt -> 1 step, alpha ~0.5
  steps = 0; loop.reset(); loop.frame();
  t += dt * 1.5; loop.frame();
  assert.equal(steps, 1);
  assert.ok(Math.abs(last - 0.5) < 1e-6);
});

test('loop: start/stop drive frames through the injected scheduler; shouldStep gates sim only', () => {
  let t = 0, steps = 0, renders = 0, queued = null, paused = false;
  const dt = 1 / 60;
  const loop = RPG.core.createLoop({
    dt, clock: { now: () => t },
    scheduler: { request(cb) { queued = cb; return 7; }, cancel() { queued = null; } },
    step: () => { steps++; }, render: () => { renders++; }, shouldStep: () => !paused
  });
  loop.start();
  assert.equal(loop.running, true);
  queued(); // first frame only primes the clock
  for (let i = 0; i < 10; i++) { t += dt; queued(); }
  assert.equal(steps, 10); assert.equal(renders, 11);
  paused = true;
  for (let i = 0; i < 5; i++) { t += dt; queued(); }
  assert.equal(steps, 10); assert.equal(renders, 16);
  loop.stop();
  assert.equal(loop.running, false);
  assert.equal(queued, null);
});

test('entities: ids, add/remove/get, sweepDead, duplicate detection', () => {
  const counter = { nextEntityId: 1 };
  const ents = RPG.core.entities.create(counter);
  const a = ents.add(RPG.core.entities.baseEntity('monster', 1, 2));
  const b = ents.add(RPG.core.entities.baseEntity('monster', 3, 4));
  const c = ents.add(RPG.core.entities.baseEntity('drop', 5, 6));
  deepEq([a.id, b.id, c.id], [1, 2, 3]);
  assert.equal(counter.nextEntityId, 4);
  assert.equal(ents.get(2), b);
  assert.equal(ents.remove(a), a);
  assert.equal(ents.size, 2);
  assert.equal(ents.get(1), null);
  assert.equal(ents.remove(99), null);
  assert.ok(ents.list.includes(b) && ents.list.includes(c));
  b.dead = true;
  deepEq(ents.sweepDead(), [b]);
  deepEq(ents.list, [c]);
  assert.throws(() => ents.add({ id: 3 }), /duplicate/);
  ents.add({ id: 50 });
  assert.equal(counter.nextEntityId, 51);
});

test('pool: reuses released objects and resets them', () => {
  let made = 0;
  const pool = RPG.core.Pool.create(() => ({ n: ++made, v: 0 }), o => { o.v = 0; }, 2);
  assert.equal(made, 2);
  const o1 = pool.acquire(); o1.v = 9;
  assert.equal(pool.active.length, 1);
  assert.equal(pool.release(o1), true);
  assert.equal(pool.release(o1), false);
  assert.equal(o1.v, 0);
  assert.equal(pool.active.length, 0);
  pool.acquire(); pool.acquire(); pool.acquire();
  assert.equal(made, 3);
});

test('util: vector/array helpers', () => {
  const u = RPG.core.util;
  assert.equal(u.clamp(5, 0, 3), 3);
  assert.equal(u.lerp(0, 10, 0.25), 2.5);
  assert.equal(u.dist(0, 0, 3, 4), 5);
  deepEq(u.norm(0, 0), { x: 0, y: 0 });
  assert.ok(Math.abs(u.norm(3, 4).x - 0.6) < 1e-12);
  assert.ok(Math.abs(u.wrapAngle(3 * Math.PI) - Math.PI) < 1e-9);
  assert.ok(Math.abs(u.wrapAngle(-Math.PI) - Math.PI) < 1e-9);
  const arr = [1, 2, 3, 4];
  u.removeAt(arr, 1);
  deepEq(arr, [1, 4, 3]);
  assert.equal(u.fmt(1234), '1.2k');
  assert.equal(u.fmt(12), '12');
  const f = u.deepFreeze({ a: { b: 1 } });
  assert.ok(Object.isFrozen(f.a));
  deepEq(u.deepClone({ a: [1] }), { a: [1] });
});

test('state: world skeleton is deterministic and snapshot excludes cosmetic data', () => {
  const w1 = RPG.core.state.createWorldState({ seed: 5 });
  const w2 = RPG.core.state.createWorldState({ seed: 5 });
  assert.equal(w1.rng.next(), w2.rng.next());
  w1.fx.rng.next();
  assert.equal(w1.rng.getState(), w2.rng.getState(), 'fx rng is separate from gameplay rng');
  const snap = RPG.core.state.snapshot(w1);
  assert.ok(!('fx' in snap) && !('grid' in snap));
  assert.equal(snap.rngState, w1.rng.getState());
});

test('state: lootRng is a separate deterministic stream; telegraphs and enemyProjectiles start empty', () => {
  const w1 = RPG.core.state.createWorldState({ seed: 5 });
  const w2 = RPG.core.state.createWorldState({ seed: 5 });
  assert.equal(w1.lootRng.next(), w2.lootRng.next());
  assert.equal(w1.rng.getState(), w2.rng.getState(), 'drawing loot does not advance the gameplay rng');
  assert.notEqual(w1.lootRng.getState(), w1.rng.getState());
  deepEq(w1.telegraphs, []);
  deepEq(w1.enemyProjectiles, []);
  assert.notEqual(w1.telegraphs, w2.telegraphs);
});

test('src/core has no window/document/localStorage references and loads headless', () => {
  for (const f of fs.readdirSync(CORE).filter(n => n.endsWith('.js'))) {
    const code = fs.readFileSync(path.join(CORE, f), 'utf8');
    assert.ok(!/\b(window|document|localStorage)\b/.test(code), `${f} references DOM globals`);
  }
});

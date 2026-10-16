'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['world/spatial.js']);
const sp = RPG.world.spatial;

// Deterministic PRNG (mulberry32) so every run exercises the same random scenes.
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Same overlap rule the grid promises: centre distance <= query radius + entity radius.
function bruteForce(ents, x, y, r) {
  return ents.filter((e) => Math.hypot(e.x - x, e.y - y) <= r + e.r).map((e) => e.id).sort((p, q) => p - q);
}

function ids(list) {
  return list.map((e) => e.id).sort((p, q) => p - q);
}

test('queryCircle matches brute force on 500 random circles', () => {
  const rand = rng(0xc0ffee);
  const ents = [];
  for (let i = 0; i < 400; i++) {
    ents.push({ id: i, x: -200 + rand() * 1600, y: -200 + rand() * 1200, r: 2 + rand() * 20 });
  }
  const g = sp.create(64);
  for (const e of ents) sp.insert(g, e);

  const out = [];
  for (let q = 0; q < 500; q++) {
    const x = -200 + rand() * 1600;
    const y = -200 + rand() * 1200;
    const r = rand() * 150;
    sp.queryCircle(g, x, y, r, out);
    assert.deepEqual(ids(out), bruteForce(ents, x, y, r), `query ${q} at (${x}, ${y}) r=${r}`);
  }
});

test('queryCircle finds entities whose centre is outside the circle but whose body overlaps it', () => {
  const g = sp.create(64);
  // Centre sits in cell 3 (x=200), but its radius of 60 reaches back toward the query at x=150.
  sp.insert(g, { id: 1, x: 200, y: 0, r: 60 });
  assert.deepEqual(ids(sp.queryCircle(g, 150, 0, 10, [])), [1]); // centre distance 50 <= 10 + 60
  assert.deepEqual(ids(sp.queryCircle(g, 100, 0, 10, [])), []); // centre distance 100 > 10 + 60
  assert.deepEqual(ids(sp.queryCircle(g, 0, 0, 10, [])), []);
});

test('queryCircle reuses the caller array: returns it, clears stale results, never replaces it', () => {
  const g = sp.create(64);
  for (let i = 0; i < 20; i++) sp.insert(g, { id: i, x: i * 10, y: 0, r: 4 });
  const out = [];
  const first = sp.queryCircle(g, 50, 0, 30, out);
  assert.equal(first, out);
  const firstIds = ids(out);
  assert.ok(firstIds.length > 0);

  // A later query into the same array must not keep the previous contents.
  const second = sp.queryCircle(g, 500, 500, 5, out);
  assert.equal(second, out);
  assert.equal(out.length, 0);

  // Repeated queries keep the same backing array identity.
  for (let i = 0; i < 1000; i++) assert.equal(sp.queryCircle(g, i % 200, 0, 40, out), out);
});

test('an entity is reported once even when its circle spans several cells', () => {
  const g = sp.create(32);
  sp.insert(g, { id: 7, x: 64, y: 64, r: 90 });
  const out = sp.queryCircle(g, 0, 0, 200, []);
  assert.deepEqual(ids(out), [7]);
});

test('negative and cell-boundary coordinates are bucketed correctly', () => {
  const g = sp.create(64);
  const ents = [
    { id: 1, x: -0.5, y: -0.5, r: 1 },
    { id: 2, x: -64, y: -64, r: 1 },
    { id: 3, x: -65, y: 0, r: 1 },
    { id: 4, x: 0, y: 0, r: 1 },
    { id: 5, x: 63.999, y: 63.999, r: 1 },
    { id: 6, x: 64, y: 64, r: 1 },
  ];
  for (const e of ents) sp.insert(g, e);
  for (const e of ents) {
    const out = sp.queryCircle(g, e.x, e.y, 0, []);
    assert.ok(out.includes(e), `entity ${e.id} not found at its own centre`);
  }
  assert.deepEqual(ids(sp.queryCircle(g, -64, -64, 2, [])), [2]);
});

test('clear empties the grid and resets the reach so stale radii do not widen later queries', () => {
  const g = sp.create(64);
  sp.insert(g, { id: 1, x: 0, y: 0, r: 500 });
  sp.clear(g);
  assert.equal(g.count, 0);
  sp.insert(g, { id: 2, x: 1000, y: 1000, r: 1 });
  assert.deepEqual(ids(sp.queryCircle(g, 0, 0, 10, [])), []);
  assert.deepEqual(ids(sp.queryCircle(g, 1000, 1000, 10, [])), [2]);
});

test('entities without a finite position are ignored instead of corrupting buckets', () => {
  const g = sp.create(64);
  sp.insert(g, { id: 1, x: NaN, y: 0, r: 5 });
  sp.insert(g, { id: 2, x: 0, y: Infinity, r: 5 });
  assert.equal(g.count, 0);
  assert.deepEqual(ids(sp.queryCircle(g, 0, 0, 1000, [])), []);
});

test('1000 entities x 1000 queries: correct and within a generous time bound', () => {
  const rand = rng(1234);
  const ents = [];
  for (let i = 0; i < 1000; i++) {
    ents.push({ id: i, x: rand() * 2000, y: rand() * 2000, r: 8 });
  }
  const g = sp.create(64);
  const out = [];
  const queries = [];
  for (let q = 0; q < 1000; q++) queries.push([rand() * 2000, rand() * 2000, 20 + rand() * 60]);

  // One frame: rebuild the grid, then run every query into the same reused array.
  const start = process.hrtime.bigint();
  sp.clear(g);
  for (const e of ents) sp.insert(g, e);
  for (const [x, y, r] of queries) sp.queryCircle(g, x, y, r, out);
  const elapsedMs = Number(process.hrtime.bigint() - start) / 1e6;

  // Correctness on the same scene, against brute force, for every query.
  for (const [x, y, r] of queries) {
    sp.queryCircle(g, x, y, r, out);
    assert.deepEqual(ids(out), bruteForce(ents, x, y, r));
  }

  console.log(`spatial: 1000 entities x 1000 queries (insert + query, cell 64px) took ${elapsedMs.toFixed(2)} ms`);
  // Generous bound: a hash grid does this in a few ms; a slow CI box still has plenty of room.
  assert.ok(elapsedMs < 2000, `took ${elapsedMs.toFixed(2)} ms`);
});

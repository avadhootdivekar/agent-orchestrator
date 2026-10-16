'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/rng.js', 'world/tilemap.js', 'world/mapgen.js']);
const { tilemap: tm, mapgen } = RPG.world;
const { Rng } = RPG.core;

// Inline AreaDefinitions: the tests must not depend on src/data/areas.js, which a sibling unit writes.
const AUTHORED = {
  id: 'town-test',
  kind: 'authored',
  theme: 'town',
  layout: [
    '############',
    '#..........#',
    '#.S........#',
    '#..........#',
    '#..........>',
    '#..........#',
    '############',
  ],
  exits: [{ to: 'forest-test' }],
  budget: 25,
};
const CAVE = { id: 'caves-test', kind: 'cave', theme: 'caves', size: [40, 30], exits: [{ to: 'town-test' }] };
const OPEN = {
  id: 'forest-test',
  kind: 'open',
  theme: 'forest',
  size: [48, 36],
  exits: [{ to: 'caves-test' }, { to: 'town-test' }],
};
const ARENA = { id: 'arena-test', kind: 'cave', theme: 'arena', size: [30, 24], boss: { bossId: 'x', slot: 'arena-center' } };

const SEEDS = [1, 2, 3, 7, 42, 1234, 99999];

function gen(def, seed) {
  return mapgen.generate(def, new Rng(seed));
}

// Compare plain data; objects built inside the vm context have another realm's prototypes.
function snapshot(result) {
  return JSON.parse(JSON.stringify({
    w: result.map.w,
    h: result.map.h,
    tiles: Array.from(result.map.tiles),
    spawn: result.spawn,
    exits: result.exits,
    encounterSlots: result.encounterSlots,
    bossSlot: result.bossSlot,
  }));
}

function tileOf(map, px) {
  return Math.floor(px / map.tileSize);
}

function walkableCount(map) {
  let n = 0;
  for (let ty = 0; ty < map.h; ty++) {
    for (let tx = 0; tx < map.w; tx++) if (!tm.isSolid(map, tx, ty)) n++;
  }
  return n;
}

test('same seed gives identical maps and features; different seeds give different maps', () => {
  for (const def of [CAVE, OPEN, ARENA]) {
    for (const seed of SEEDS) {
      assert.deepEqual(snapshot(gen(def, seed)), snapshot(gen(def, seed)), `${def.id} seed ${seed} repeats`);
    }
    const a = snapshot(gen(def, 1));
    const b = snapshot(gen(def, 2));
    assert.notDeepEqual(a.tiles, b.tiles, `${def.id}: seeds 1 and 2 must differ`);
  }
});

test('authored layouts keep their ASCII tiles whatever the seed; only encounter slots vary', () => {
  const a = gen(AUTHORED, 1);
  const b = gen(AUTHORED, 999);
  const { encounterSlots: slotsA, ...fixedA } = snapshot(a);
  const { encounterSlots: slotsB, ...fixedB } = snapshot(b);
  assert.deepEqual(fixedA, fixedB);
  assert.notDeepEqual(slotsA, slotsB, 'slots come from the seeded RNG, so they should differ across seeds');
  assert.deepEqual(snapshot(gen(AUTHORED, 1)), snapshot(a));
  assert.equal(a.map.w, 12);
  assert.equal(a.map.h, 7);
  assert.equal(a.map.tiles[idx(a.map, 1, 1)], tm.TILE.FLOOR);
  assert.equal(a.map.tiles[idx(a.map, 0, 0)], tm.TILE.WALL);
});

test('cave and open maps are connected: every walkable tile is reachable from spawn', () => {
  for (const def of [CAVE, OPEN, ARENA]) {
    for (const seed of SEEDS) {
      const r = gen(def, seed);
      const reach = tm.reachableTiles(r.map, tileOf(r.map, r.spawn.x), tileOf(r.map, r.spawn.y));
      assert.equal(reach.length, walkableCount(r.map), `${def.id} seed ${seed}: disconnected floor`);
    }
  }
});

test('spawn, exits and encounter slots sit on walkable tiles reachable from spawn', () => {
  for (const def of [AUTHORED, CAVE, OPEN, ARENA]) {
    for (const seed of SEEDS) {
      const r = gen(def, seed);
      const { map } = r;
      const reachable = new Set(
        tm.reachableTiles(map, tileOf(map, r.spawn.x), tileOf(map, r.spawn.y)).map((t) => t.ty * map.w + t.tx),
      );
      const points = [r.spawn, ...r.exits, ...r.encounterSlots, ...(r.bossSlot ? [r.bossSlot] : [])];
      for (const p of points) {
        const i = idx(map, tileOf(map, p.x), tileOf(map, p.y));
        assert.equal(tm.solidAtPx(map, p.x, p.y), false, `${def.id} seed ${seed}: point on a solid tile`);
        assert.ok(reachable.has(i), `${def.id} seed ${seed}: point not reachable from spawn`);
      }
    }
  }
});

// Step distance from spawn to every reachable tile, computed independently of mapgen.js.
function stepDistances(map, spawn) {
  const dist = new Map();
  const start = idx(map, tileOf(map, spawn.x), tileOf(map, spawn.y));
  const queue = [start];
  dist.set(start, 0);
  for (let head = 0; head < queue.length; head++) {
    const cur = queue[head];
    const tx = cur % map.w;
    const ty = Math.floor(cur / map.w);
    for (const [ox, oy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      if (tm.isSolid(map, tx + ox, ty + oy)) continue;
      const n = idx(map, tx + ox, ty + oy);
      if (dist.has(n)) continue;
      dist.set(n, dist.get(cur) + 1);
      queue.push(n);
    }
  }
  return dist;
}

test('encounter slots are on floor, have a radius, and keep clear of spawn by walking distance', () => {
  for (const seed of SEEDS) {
    const r = gen(CAVE, seed);
    assert.ok(r.encounterSlots.length > 0, `seed ${seed} has no encounter slots`);
    const dist = stepDistances(r.map, r.spawn);
    for (const s of r.encounterSlots) {
      assert.equal(tm.solidAtPx(r.map, s.x, s.y), false);
      const i = idx(r.map, tileOf(r.map, s.x), tileOf(r.map, s.y));
      assert.ok(dist.get(i) >= 5, `seed ${seed}: slot ${dist.get(i)} steps from spawn`);
      assert.equal(s.r, 2 * r.map.tileSize);
    }
  }
});

test('boss slot is placed on the farthest reachable tile when the area has a boss', () => {
  const r = gen(ARENA, 5);
  assert.ok(r.bossSlot, 'boss slot expected');
  assert.equal(gen(OPEN, 5).bossSlot, undefined, 'no boss slot without a boss definition');
  const dist = stepDistances(r.map, r.spawn);
  const bossIdx = idx(r.map, tileOf(r.map, r.bossSlot.x), tileOf(r.map, r.bossSlot.y));
  assert.equal(dist.get(bossIdx), Math.max(...dist.values()));
});

test('exits take the area definition targets in order and become portal tiles', () => {
  const r = gen(OPEN, 11);
  // Arrays from the vm context have another realm's prototype, so compare them as plain JSON.
  assert.deepEqual(JSON.parse(JSON.stringify(r.exits.map((e) => e.toArea))), ['caves-test', 'town-test']);
  for (const e of r.exits) {
    assert.equal(r.map.tiles[idx(r.map, tileOf(r.map, e.x), tileOf(r.map, e.y))], tm.TILE.PORTAL);
  }
});

test('authored exits come from portal glyphs, paired with the definition targets', () => {
  const r = gen(AUTHORED, 1);
  assert.equal(r.exits.length, 1);
  assert.equal(r.exits[0].toArea, 'forest-test');
  assert.equal(r.map.tiles[idx(r.map, tileOf(r.map, r.exits[0].x), tileOf(r.map, r.exits[0].y))], tm.TILE.PORTAL);
});

test('spawn is at the centre of the authored S glyph', () => {
  const r = gen(AUTHORED, 1);
  assert.deepEqual({ x: r.spawn.x, y: r.spawn.y }, { x: 2.5 * 32, y: 2.5 * 32 });
});

test('invalid definitions fail loudly instead of producing a silent map', () => {
  assert.throws(() => gen({ id: 'x', kind: 'rooms', size: [20, 20] }, 1), /unknown or unimplemented kind 'rooms'/);
  assert.throws(() => gen({ id: 'x', kind: 'open', size: [3, 3] }, 1), /needs size/);
  assert.throws(() => gen({ id: 'x', kind: 'authored', layout: ['...', '...'] }, 1), /no spawn/);
  assert.throws(() => gen({ id: 'x', kind: 'authored' }, 1), /no layout/);
  assert.throws(() => mapgen.generate(null, new Rng(1)), /definition is required/);
});

function idx(map, tx, ty) {
  return ty * map.w + tx;
}

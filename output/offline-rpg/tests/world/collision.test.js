'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG, WORLD_FILES } = require('./load.js');

const RPG = loadRPG([...WORLD_FILES, 'data/maps/test-map.js']);
const { tilemap: tm } = RPG.world;
const col = RPG.world.collision;

const EPS = 1e-9;

// Open 10x10 room (32 px tiles) enclosed by map-edge walls. Interior tiles are 1..8.
function room(rows) {
  return tm.fromRows(rows || [
    '##########',
    '#........#',
    '#........#',
    '#........#',
    '#........#',
    '#........#',
    '#........#',
    '#........#',
    '#........#',
    '##########',
  ]);
}

function entity(x, y, r = 8) {
  return { x, y, r, hitWall: false };
}

// Independent check: no solid tile of the whole map may overlap the circle (brute force, not bbox-limited).
function assertFree(map, ent) {
  for (let ty = 0; ty < map.h; ty++) {
    for (let tx = 0; tx < map.w; tx++) {
      if (!tm.isSolid(map, tx, ty)) continue;
      const left = tx * map.tileSize;
      const top = ty * map.tileSize;
      const cx = Math.min(Math.max(ent.x, left), left + map.tileSize);
      const cy = Math.min(Math.max(ent.y, top), top + map.tileSize);
      const d = Math.hypot(ent.x - cx, ent.y - cy);
      assert.ok(d >= ent.r - EPS, `circle (${ent.x}, ${ent.y}) r=${ent.r} overlaps solid tile ${tx},${ty}`);
    }
  }
}

test('free movement in open floor moves by exactly the delta', () => {
  const map = room();
  const e = entity(100, 100);
  col.moveCircle(map, e, 20, -10);
  assert.ok(Math.abs(e.x - 120) < EPS);
  assert.ok(Math.abs(e.y - 90) < EPS);
  assert.equal(e.hitWall, false);
});

test('moving into a wall stops flush against its face', () => {
  const map = room();
  // Wall column is tile x=9 (px 288..320). Circle r=8 must stop at x = 288 - 8 = 280.
  const e = entity(200, 100);
  col.moveCircle(map, e, 200, 0);
  assert.ok(Math.abs(e.x - 280) < EPS, `x=${e.x}`);
  assert.equal(e.hitWall, true);
  assertFree(map, e);
});

test('diagonal motion into a vertical wall slides along it', () => {
  const map = room();
  const e = entity(200, 100);
  col.moveCircle(map, e, 200, 40);
  assert.ok(Math.abs(e.x - 280) < EPS, `x=${e.x} should stop at wall face`);
  assert.ok(Math.abs(e.y - 140) < EPS, `y=${e.y} should keep its full Y motion while sliding`);
  assert.equal(e.hitWall, true);
  assertFree(map, e);
});

test('diagonal motion into a horizontal wall slides along it', () => {
  const map = room();
  // Wall row is tile y=9 (px 288..320). Circle r=8 must stop at y = 280.
  const e = entity(100, 200);
  col.moveCircle(map, e, 40, 200);
  assert.ok(Math.abs(e.y - 280) < EPS, `y=${e.y}`);
  assert.ok(Math.abs(e.x - 140) < EPS, `x=${e.x}`);
  assertFree(map, e);
});

test('map edges act as solid: a bare grid is clamped at r from the boundary', () => {
  // No wall tiles at all: out-of-bounds must be the only thing stopping the circle.
  const map = tm.create(10, 10);
  const e = entity(100, 100);
  col.moveCircle(map, e, -500, -500);
  assert.ok(Math.abs(e.x - 8) < EPS, `x=${e.x}`);
  assert.ok(Math.abs(e.y - 8) < EPS, `y=${e.y}`);
  assert.equal(e.hitWall, true);
  col.moveCircle(map, e, 500, 500);
  assert.ok(Math.abs(e.x - (10 * 32 - 8)) < EPS, `x=${e.x}`);
  assert.ok(Math.abs(e.y - (10 * 32 - 8)) < EPS, `y=${e.y}`);
  assertFree(map, e);
});

test('walled room: the boundary wall tiles stop the circle at their faces', () => {
  const map = room(); // wall tiles at x=0 and x=9, so faces are at px 32 and 288
  const e = entity(100, 100);
  col.moveCircle(map, e, -500, -500);
  assert.ok(Math.abs(e.x - 40) < EPS, `x=${e.x}`);
  assert.ok(Math.abs(e.y - 40) < EPS, `y=${e.y}`);
  col.moveCircle(map, e, 500, 500);
  assert.ok(Math.abs(e.x - 280) < EPS, `x=${e.x}`);
  assert.ok(Math.abs(e.y - 280) < EPS, `y=${e.y}`);
  assertFree(map, e);
});

test('high-speed move does not tunnel through a one-tile wall', () => {
  // Single-tile wall at column 3 (px 96..128) inside an otherwise open corridor.
  const map = tm.fromRows([
    '#######',
    '#.....#',
    '#..#..#',
    '#.....#',
    '#######',
  ]);
  const e = entity(40, 80, 6);
  // One call moves 200 px: far more than a tile, so a naive single-step test would skip the wall.
  col.moveCircle(map, e, 200, 0);
  assert.ok(e.x < 96 - 6 + EPS, `circle passed the wall: x=${e.x}`);
  assert.equal(e.hitWall, true);
  assertFree(map, e);
});

test('inside corner: circle wedged between two walls does not end up inside either', () => {
  // L-shaped inner corner: walls at (2,2) and (3,3) block both an x push and a y push.
  const map = tm.fromRows([
    '########',
    '#......#',
    '#......#',
    '#.##...#',
    '#.##...#',
    '#......#',
    '########',
  ]);
  const e = entity(60, 60, 10);
  col.moveCircle(map, e, 40, 40);
  assertFree(map, e);
  col.moveCircle(map, e, -40, 0);
  assertFree(map, e);
});

test('randomised walks never leave the circle inside a solid tile', () => {
  // Fixed-seed LCG so the walk is deterministic and replayable.
  let seed = 12345;
  const rand = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 0x100000000;
  };
  const map = RPG.world.tilemap.fromRows(RPG.data.maps['test-map'].layout.slice());
  const e = entity(3 * 32 + 16, 14 * 32 + 16, 10);
  assertFree(map, e);
  for (let i = 0; i < 1000; i++) {
    const speed = 5 + rand() * 300; // includes speeds far above one tile per frame
    const a = rand() * Math.PI * 2;
    col.moveCircle(map, e, Math.cos(a) * speed, Math.sin(a) * speed);
    assertFree(map, e);
  }
});

test('circlesOverlap and circleHitsPoint use strict distance', () => {
  assert.equal(col.circlesOverlap({ x: 0, y: 0, r: 5 }, { x: 9, y: 0, r: 5 }), true);
  assert.equal(col.circlesOverlap({ x: 0, y: 0, r: 5 }, { x: 10, y: 0, r: 5 }), false);
  assert.equal(col.circleHitsPoint({ x: 0, y: 0, r: 5 }, 4, 0), true);
  assert.equal(col.circleHitsPoint({ x: 0, y: 0, r: 5 }, 5, 0), false);
});

test('inCone: hits inside the arc, misses behind and beyond range', () => {
  const east = 0;
  const halfArc = Math.PI / 4;
  assert.equal(col.inCone(0, 0, east, halfArc, 100, 50, 0, 4), true);
  assert.equal(col.inCone(0, 0, east, halfArc, 100, -50, 0, 4), false);
  assert.equal(col.inCone(0, 0, east, halfArc, 100, 150, 0, 4), false);
  // Target just outside the half-arc edge but wide enough to overlap it.
  assert.equal(col.inCone(0, 0, east, halfArc, 100, 50, 50, 10), true);
  // Origin inside the target counts as a hit regardless of facing.
  assert.equal(col.inCone(0, 0, Math.PI, halfArc, 100, 3, 0, 10), true);
});

test('sweepProjectile stops at the last free position before a wall', () => {
  const map = room();
  // 400 px in one sweep crosses the wall column at x=288..320; the sweep must not tunnel through it.
  const p = { x: 200, y: 150, r: 0, vx: 400, vy: 0 };
  const res = col.sweepProjectile(map, p, 1);
  assert.equal(res.hitWall, true);
  // Last free position sits short of the wall face (x=288), not inside or beyond it.
  assert.ok(p.x < 288, `projectile x=${p.x}`);
  assert.ok(p.x > 270, `projectile should stop just short of the wall, x=${p.x}`);
});

test('sweepProjectile in open floor reports no hit and moves the full distance', () => {
  const map = room();
  const p = { x: 100, y: 150, r: 2, vx: 60, vy: 0 };
  const res = col.sweepProjectile(map, p, 1 / 60);
  assert.equal(res.hitWall, false);
  assert.ok(Math.abs(p.x - 101) < EPS, `x=${p.x}`);
});

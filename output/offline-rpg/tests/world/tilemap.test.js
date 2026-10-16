'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG, WORLD_FILES } = require('./load.js');

const RPG = loadRPG([...WORLD_FILES, 'data/maps/test-map.js']);
const { tilemap: tm } = RPG.world;
const TEST_MAP = RPG.data.maps['test-map'];

// Objects built inside the vm context have another realm's prototypes, so compare them as plain JSON.
const plain = (v) => JSON.parse(JSON.stringify(v));

test('fromRows builds a grid with the expected dimensions and glyph mapping', () => {
  const map = tm.fromRows(['#.~', 'S+>']);
  assert.equal(map.w, 3);
  assert.equal(map.h, 2);
  assert.equal(map.tileSize, 32);
  assert.equal(map.tiles[0], tm.TILE.WALL);
  assert.equal(map.tiles[1], tm.TILE.FLOOR);
  assert.equal(map.tiles[2], tm.TILE.WATER);
  assert.equal(map.tiles[3], tm.TILE.SPAWN);
  assert.equal(map.tiles[4], tm.TILE.DOOR);
  assert.equal(map.tiles[5], tm.TILE.PORTAL);
});

test('fromRows rejects ragged rows and unknown glyphs instead of guessing', () => {
  assert.throws(() => tm.fromRows(['...', '..']), /row 1 has 2 columns, expected 3/);
  assert.throws(() => tm.fromRows(['.?.']), /unknown glyph '\?'/);
  assert.throws(() => tm.fromRows([]), /no rows/);
});

test('out-of-bounds tiles are solid, so the map edge is a wall', () => {
  const map = tm.fromRows(['...']);
  assert.equal(tm.isSolid(map, -1, 0), true);
  assert.equal(tm.isSolid(map, 3, 0), true);
  assert.equal(tm.isSolid(map, 0, -1), true);
  assert.equal(tm.isSolid(map, 0, 1), true);
  assert.equal(tm.isSolid(map, 1, 0), false);
});

test('water and walls are solid; doors, portals, spawn and decor are walkable', () => {
  const map = tm.fromRows(['#~+>S,']);
  const solid = [0, 1, 2, 3, 4, 5].map((x) => tm.isSolid(map, x, 0));
  assert.deepEqual(solid, [true, true, false, false, false, false]);
});

test('solidAtPx maps pixels to tiles', () => {
  const map = tm.fromRows(['..#']); // wall occupies px 64..96
  assert.equal(tm.solidAtPx(map, 63.9, 5), false);
  assert.equal(tm.solidAtPx(map, 64, 5), true);
  assert.equal(tm.solidAtPx(map, 95.9, 31.9), true);
  assert.equal(tm.solidAtPx(map, 96, 5), true); // out of bounds
});

test('setTile ignores out-of-bounds writes', () => {
  const map = tm.create(2, 2);
  tm.setTile(map, 1, 1, tm.TILE.WALL);
  tm.setTile(map, 5, 5, tm.TILE.WALL);
  assert.deepEqual(Array.from(map.tiles), [0, 0, 0, 1]);
});

test('findTile returns the first tile with an id, row-major, or null', () => {
  const map = tm.fromRows(['..S', 'S..']);
  assert.deepEqual(plain(tm.findTile(map, tm.TILE.SPAWN)), { tx: 2, ty: 0 });
  assert.equal(tm.findTile(map, tm.TILE.PORTAL), null);
});

test('raycast: open floor is clear, a wall between endpoints blocks, endpoint in a wall blocks', () => {
  const map = tm.fromRows([
    '.......',
    '.......',
    '...#...',
    '.......',
    '.......',
  ]);
  const ts = 32;
  const px = (t) => t * ts + ts / 2; // tile centre
  assert.equal(tm.raycastClear(map, px(0), px(0), px(6), px(0)), true);
  assert.equal(tm.raycastClear(map, px(0), px(2), px(6), px(2)), false);
  assert.equal(tm.raycastClear(map, px(0), px(2), px(3), px(2)), false); // endpoint is the wall
  assert.equal(tm.raycastClear(map, px(3), px(2), px(3), px(2)), false); // start inside the wall
  assert.equal(tm.raycastClear(map, px(0), px(1), px(6), px(3)), false); // diagonal through the wall
  assert.equal(tm.raycastClear(map, px(0), px(0), px(6), px(1)), true); // shallow diagonal above it
});

test('raycast: a ray on the far side of a wall stays blocked in both directions', () => {
  const map = tm.fromRows(['..#..']);
  const px = (t) => t * 32 + 16;
  assert.equal(tm.raycastClear(map, px(0), px(0), px(4), px(0)), false);
  assert.equal(tm.raycastClear(map, px(4), px(0), px(0), px(0)), false);
});

test('raycast: a thin wall cannot be stepped over by a long diagonal', () => {
  const map = tm.fromRows([
    '.....#.....',
    '.....#.....',
    '.....#.....',
    '.....#.....',
    '.....#.....',
  ]);
  const px = (t) => t * 32 + 16;
  assert.equal(tm.raycastClear(map, px(0), px(0), px(10), px(4)), false);
});

test('raycast from a tile to itself is clear when the tile is open', () => {
  const map = tm.fromRows(['...']);
  assert.equal(tm.raycastClear(map, 10, 10, 12, 12), true);
});

test('reachableTiles floods only the connected walkable region', () => {
  const map = tm.fromRows([
    '..#..',
    '..#..',
    '###..',
  ]);
  const left = tm.reachableTiles(map, 0, 0);
  assert.equal(left.length, 4);
  assert.ok(left.every((t) => t.tx < 2));
  const right = tm.reachableTiles(map, 4, 0);
  assert.equal(right.length, 6);
  assert.ok(right.every((t) => t.tx >= 3));
  assert.equal(tm.reachableTiles(map, 2, 0).length, 0); // starting inside a wall
});

test('test-map: 40x30, one spawn, and every walkable tile reachable from the spawn except the sealed pocket', () => {
  const map = tm.fromRows(TEST_MAP.layout, TEST_MAP.tileSize);
  assert.equal(map.w, 40);
  assert.equal(map.h, 30);
  const spawn = tm.findTile(map, tm.TILE.SPAWN);
  assert.deepEqual(plain(spawn), { tx: 3, ty: 14 });

  const reachable = new Set(tm.reachableTiles(map, spawn.tx, spawn.ty).map((t) => `${t.tx},${t.ty}`));
  // East room, south corridor and pond shore are all connected to the spawn.
  for (const [tx, ty] of [[36, 20], [20, 26], [30, 10], [9, 22]]) {
    assert.ok(reachable.has(`${tx},${ty}`), `tile ${tx},${ty} should be reachable`);
  }
  // The sealed pocket is walkable but unreachable.
  assert.ok(!tm.isSolid(map, 35, 3));
  assert.ok(!reachable.has('35,3'));
  // Every walkable tile outside the pocket is reachable.
  let walkable = 0;
  let unreachable = 0;
  for (let ty = 0; ty < map.h; ty++) {
    for (let tx = 0; tx < map.w; tx++) {
      if (tm.isSolid(map, tx, ty)) continue;
      walkable++;
      if (!reachable.has(`${tx},${ty}`)) unreachable++;
    }
  }
  assert.equal(unreachable, 4 * 3, 'only the 4x3 sealed pocket should be unreachable');
  assert.equal(reachable.size, walkable - unreachable);
});

test('test-map: line of sight is blocked by the west wall and clear through the gap', () => {
  const map = tm.fromRows(TEST_MAP.layout, TEST_MAP.tileSize);
  const px = (t) => t * 32 + 16;
  // Wall column x=14 at y=14 blocks the spawn room from the east.
  assert.equal(tm.raycastClear(map, px(3), px(14), px(20), px(14)), false);
  // Gap at x=14, y=21..23 lets the same row-pair see across.
  assert.equal(tm.raycastClear(map, px(3), px(22), px(20), px(22)), true);
});

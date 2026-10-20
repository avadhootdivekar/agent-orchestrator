/**
 * Tilemap: uniform tile grid. Plain data (`tiles` is a row-major Uint8Array) plus pure query functions.
 * Tiles outside the grid count as solid, so the map boundary acts as a wall.
 * Headless: no window/document access at load time.
 */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  const TILE = Object.freeze({ FLOOR: 0, WALL: 1, WATER: 2, DOOR: 3, PORTAL: 4, SPAWN: 5, DECOR: 6 });

  // Water blocks movement but is walkable-looking pond art; doors, portals, spawn and decor are walkable.
  const SOLID_BY_ID = Object.freeze([false, true, true, false, false, false, false]);

  // Glyphs for ASCII layouts (`fromRows`). Authored areas use these; mapgen may pass its own table.
  const DEFAULT_GLYPHS = Object.freeze({
    '.': TILE.FLOOR,
    '#': TILE.WALL,
    '~': TILE.WATER,
    '+': TILE.DOOR,
    '>': TILE.PORTAL,
    S: TILE.SPAWN,
    ',': TILE.DECOR,
  });

  function create(w, h, tileSize = 32) {
    return { w, h, tileSize, tiles: new Uint8Array(w * h), theme: '' };
  }

  function fromRows(rows, tileSize = 32, glyphs = DEFAULT_GLYPHS) {
    if (rows.length === 0) throw new Error('tilemap.fromRows: no rows');
    const w = rows[0].length;
    const map = create(w, rows.length, tileSize);
    rows.forEach((row, ty) => {
      if (row.length !== w) throw new Error(`tilemap.fromRows: row ${ty} has ${row.length} columns, expected ${w}`);
      for (let tx = 0; tx < w; tx++) {
        const id = glyphs[row[tx]];
        if (id === undefined) throw new Error(`tilemap.fromRows: unknown glyph '${row[tx]}' at ${tx},${ty}`);
        map.tiles[ty * w + tx] = id;
      }
    });
    return map;
  }

  function inBounds(map, tx, ty) {
    return tx >= 0 && ty >= 0 && tx < map.w && ty < map.h;
  }

  function isSolid(map, tx, ty) {
    if (!inBounds(map, tx, ty)) return true;
    return SOLID_BY_ID[map.tiles[ty * map.w + tx]] === true;
  }

  function solidAtPx(map, x, y) {
    return isSolid(map, Math.floor(x / map.tileSize), Math.floor(y / map.tileSize));
  }

  function setTile(map, tx, ty, id) {
    if (inBounds(map, tx, ty)) map.tiles[ty * map.w + tx] = id;
  }

  /** First tile (row-major) with the given id, as `{tx, ty}`, or null. */
  function findTile(map, id) {
    for (let i = 0; i < map.tiles.length; i++) {
      if (map.tiles[i] === id) return { tx: i % map.w, ty: Math.floor(i / map.w) };
    }
    return null;
  }

  /**
   * Line of sight in pixel space via grid traversal (Amanatides-Woo DDA). Visits every tile the segment
   * touches, so thin walls cannot be stepped over. Blocked if any visited tile is solid.
   */
  function raycastClear(map, ax, ay, bx, by) {
    const ts = map.tileSize;
    const x = ax / ts;
    const y = ay / ts;
    const dx = bx / ts - x;
    const dy = by / ts - y;
    let tx = Math.floor(x);
    let ty = Math.floor(y);
    const endTx = Math.floor(bx / ts);
    const endTy = Math.floor(by / ts);
    if (isSolid(map, tx, ty)) return false;

    const stepX = dx > 0 ? 1 : dx < 0 ? -1 : 0;
    const stepY = dy > 0 ? 1 : dy < 0 ? -1 : 0;
    const tDeltaX = stepX !== 0 ? Math.abs(1 / dx) : Infinity;
    const tDeltaY = stepY !== 0 ? Math.abs(1 / dy) : Infinity;
    let tMaxX = stepX > 0 ? (tx + 1 - x) * tDeltaX : stepX < 0 ? (x - tx) * tDeltaX : Infinity;
    let tMaxY = stepY > 0 ? (ty + 1 - y) * tDeltaY : stepY < 0 ? (y - ty) * tDeltaY : Infinity;

    while (tx !== endTx || ty !== endTy) {
      if (tMaxX < tMaxY) {
        tMaxX += tDeltaX;
        tx += stepX;
      } else {
        tMaxY += tDeltaY;
        ty += stepY;
      }
      if (isSolid(map, tx, ty)) return false;
    }
    return true;
  }

  /**
   * Every walkable tile connected to (sx, sy) through 4-neighbour steps, in BFS order, as `{tx, ty}`.
   * Empty if the start tile is solid. Encounter placement uses this so monsters never spawn sealed off.
   */
  function reachableTiles(map, sx, sy) {
    const out = [];
    if (isSolid(map, sx, sy)) return out;
    const seen = new Uint8Array(map.w * map.h);
    const queue = [sy * map.w + sx];
    seen[queue[0]] = 1;
    const NEIGHBOURS = [[1, 0], [-1, 0], [0, 1], [0, -1]];
    for (let head = 0; head < queue.length; head++) {
      const idx = queue[head];
      const tx = idx % map.w;
      const ty = Math.floor(idx / map.w);
      out.push({ tx, ty });
      for (const [ox, oy] of NEIGHBOURS) {
        const nx = tx + ox;
        const ny = ty + oy;
        if (isSolid(map, nx, ny)) continue;
        const nIdx = ny * map.w + nx;
        if (seen[nIdx]) continue;
        seen[nIdx] = 1;
        queue.push(nIdx);
      }
    }
    return out;
  }

  RPG.world.tilemap = {
    TILE,
    DEFAULT_GLYPHS,
    create,
    fromRows,
    inBounds,
    isSolid,
    solidAtPx,
    setTile,
    findTile,
    raycastClear,
    reachableTiles,
  };
})(globalThis.RPG = globalThis.RPG || {});

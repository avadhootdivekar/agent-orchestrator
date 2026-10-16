/**
 * Spatial hash: a uniform grid of buckets that answers "which entities are near this circle?" without scanning every entity.
 * Entities are bucketed by their centre. `queryCircle` widens its search by the largest radius inserted since the last
 * `clear`, so an entity whose body reaches into the query circle is found even when its centre lies in another cell.
 * The grid is rebuilt every frame (`clear` + `insert`), so buckets keep their arrays and a query allocates nothing.
 * Headless: no window/document access at load time.
 */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  // Cell indices are packed into one numeric Map key. The offset keeps negative cells positive; the stride is wide
  // enough that distinct (cx, cy) pairs never collide for any realistic map (|cx|, |cy| < 2^19).
  const KEY_OFFSET = 1 << 19;
  const KEY_STRIDE = 1 << 20;

  function cellKey(cx, cy) {
    return (cx + KEY_OFFSET) * KEY_STRIDE + (cy + KEY_OFFSET);
  }

  /** Returns an empty grid. Cells are `cellSize` px square. */
  function create(cellSize) {
    const size = cellSize > 0 ? cellSize : 64;
    return { cellSize: size, invCell: 1 / size, buckets: new Map(), maxRadius: 0, count: 0 };
  }

  /** Empties every bucket but keeps the bucket arrays, so the next frame's inserts do not reallocate them. */
  function clear(g) {
    for (const bucket of g.buckets.values()) bucket.length = 0;
    g.maxRadius = 0;
    g.count = 0;
  }

  /** Adds `ent` (reads ent.x, ent.y, ent.r). Entities without a finite position are ignored. */
  function insert(g, ent) {
    if (!Number.isFinite(ent.x) || !Number.isFinite(ent.y)) return;
    const cx = Math.floor(ent.x * g.invCell);
    const cy = Math.floor(ent.y * g.invCell);
    const key = cellKey(cx, cy);
    let bucket = g.buckets.get(key);
    if (!bucket) {
      bucket = [];
      g.buckets.set(key, bucket);
    }
    bucket.push(ent);
    const r = ent.r || 0;
    if (r > g.maxRadius) g.maxRadius = r;
    g.count++;
  }

  /**
   * Writes into `out` every inserted entity whose circle overlaps the query circle (centre distance <= r + ent.r).
   * `out` is cleared first and returned, so callers can reuse one array for every query.
   */
  function queryCircle(g, x, y, r, out) {
    out.length = 0;
    const reach = r + g.maxRadius;
    const inv = g.invCell;
    const x0 = Math.floor((x - reach) * inv);
    const x1 = Math.floor((x + reach) * inv);
    const y0 = Math.floor((y - reach) * inv);
    const y1 = Math.floor((y + reach) * inv);
    for (let cy = y0; cy <= y1; cy++) {
      for (let cx = x0; cx <= x1; cx++) {
        const bucket = g.buckets.get(cellKey(cx, cy));
        if (!bucket) continue;
        for (let i = 0; i < bucket.length; i++) {
          const ent = bucket[i];
          const reachEnt = r + (ent.r || 0);
          const dx = ent.x - x;
          const dy = ent.y - y;
          if (dx * dx + dy * dy <= reachEnt * reachEnt) out.push(ent);
        }
      }
    }
    return out;
  }

  RPG.world.spatial = {
    create,
    clear,
    insert,
    queryCircle,
  };
})(globalThis.RPG = globalThis.RPG || {});

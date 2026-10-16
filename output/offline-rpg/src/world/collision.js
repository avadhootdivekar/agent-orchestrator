/**
 * Collision: circle-vs-tile movement with wall sliding, plus circle/cone overlap helpers and projectile sweeps.
 * Pure functions over plain entities (`{x, y, r}`); the only mutation is the entity passed in.
 * Depends on RPG.world.tilemap at run time (same layer, so load order is only a convention here).
 */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  // A move is split into sub-steps no longer than this fraction of min(radius, tileSize), so a circle
  // cannot jump over a wall tile in one step (a tile is at least one tile thick, and the forbidden zone
  // around it is wider than a sub-step).
  const MOVE_SUBSTEP_FRACTION = 0.5;
  // Projectiles are swept against tiles in sub-steps of this fraction of a tile.
  const PROJECTILE_SUBSTEP_FRACTION = 0.25;
  // Points (r = 0) are tested as a 1 px circle so the solid-overlap test still has a non-empty area.
  const MIN_PROJECTILE_RADIUS = 1;
  // Pushing out of one tile can land in a neighbour at an inside corner; a few passes settle it.
  const MAX_PUSH_PASSES = 4;

  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }

  /**
   * Pushes the circle out of every solid tile it overlaps, along one axis only. Returns true if it moved.
   * Axis-separated resolution is what gives wall sliding: a blocked X move never cancels the Y move.
   */
  function resolveAxis(tm, map, ent, axis) {
    const ts = map.tileSize;
    const r = ent.r;
    let moved = false;
    for (let pass = 0; pass < MAX_PUSH_PASSES; pass++) {
      let changed = false;
      const x0 = Math.floor((ent.x - r) / ts);
      const x1 = Math.floor((ent.x + r) / ts);
      const y0 = Math.floor((ent.y - r) / ts);
      const y1 = Math.floor((ent.y + r) / ts);
      for (let ty = y0; ty <= y1; ty++) {
        for (let tx = x0; tx <= x1; tx++) {
          if (!tm.isSolid(map, tx, ty)) continue;
          const left = tx * ts;
          const top = ty * ts;
          const right = left + ts;
          const bottom = top + ts;
          // Offset from the closest point of the tile to the circle centre.
          const ddx = ent.x - clamp(ent.x, left, right);
          const ddy = ent.y - clamp(ent.y, top, bottom);
          if (ddx * ddx + ddy * ddy >= r * r) continue;
          if (axis === 'x') {
            // Zero offset means the centre sits over the tile's face, so this axis cannot resolve it.
            if (ddx > 0) ent.x = right + r;
            else if (ddx < 0) ent.x = left - r;
            else continue;
          } else if (ddy > 0) {
            ent.y = bottom + r;
          } else if (ddy < 0) {
            ent.y = top - r;
          } else {
            continue;
          }
          changed = true;
          moved = true;
        }
      }
      if (!changed) break;
    }
    return moved;
  }

  /**
   * Moves `ent` by (dx, dy) against the tilemap: sub-stepped, X then Y per sub-step, sliding along walls.
   * Sets `ent.hitWall` to true if any sub-step had to push the circle out of a solid tile.
   */
  function moveCircle(map, ent, dx, dy) {
    const tm = RPG.world.tilemap;
    const ts = map.tileSize;
    const stepMax = Math.max(Math.min(ent.r, ts) * MOVE_SUBSTEP_FRACTION, MIN_PROJECTILE_RADIUS);
    const n = Math.max(1, Math.ceil(Math.max(Math.abs(dx), Math.abs(dy)) / stepMax));
    const sx = dx / n;
    const sy = dy / n;
    ent.hitWall = false;
    for (let i = 0; i < n; i++) {
      ent.x += sx;
      if (resolveAxis(tm, map, ent, 'x')) ent.hitWall = true;
      ent.y += sy;
      if (resolveAxis(tm, map, ent, 'y')) ent.hitWall = true;
    }
  }

  /** True if a circle at (x, y) with radius r overlaps any solid tile. */
  function circleOverlapsSolid(map, x, y, r) {
    const tm = RPG.world.tilemap;
    const ts = map.tileSize;
    const x0 = Math.floor((x - r) / ts);
    const x1 = Math.floor((x + r) / ts);
    const y0 = Math.floor((y - r) / ts);
    const y1 = Math.floor((y + r) / ts);
    for (let ty = y0; ty <= y1; ty++) {
      for (let tx = x0; tx <= x1; tx++) {
        if (!tm.isSolid(map, tx, ty)) continue;
        const left = tx * ts;
        const top = ty * ts;
        const ddx = x - clamp(x, left, left + ts);
        const ddy = y - clamp(y, top, top + ts);
        if (ddx * ddx + ddy * ddy < r * r) return true;
      }
    }
    return false;
  }

  /**
   * Sweeps a projectile by (vx, vy) * dt in tile-sized sub-steps. Stops at the last free position on
   * contact and sets `p.x`/`p.y` there. Returns `{ hitWall }`.
   */
  function sweepProjectile(map, p, dt) {
    const ts = map.tileSize;
    const mx = p.vx * dt;
    const my = p.vy * dt;
    const r = Math.max(p.r || 0, MIN_PROJECTILE_RADIUS);
    const dist = Math.hypot(mx, my);
    const n = Math.max(1, Math.ceil(dist / (ts * PROJECTILE_SUBSTEP_FRACTION)));
    let fx = p.x;
    let fy = p.y;
    for (let i = 1; i <= n; i++) {
      const nx = p.x + (mx * i) / n;
      const ny = p.y + (my * i) / n;
      if (circleOverlapsSolid(map, nx, ny, r)) {
        p.x = fx;
        p.y = fy;
        return { hitWall: true };
      }
      fx = nx;
      fy = ny;
    }
    p.x = fx;
    p.y = fy;
    return { hitWall: false };
  }

  function circlesOverlap(a, b) {
    const dx = a.x - b.x;
    const dy = a.y - b.y;
    const rr = a.r + b.r;
    return dx * dx + dy * dy < rr * rr;
  }

  function circleHitsPoint(c, x, y) {
    const dx = c.x - x;
    const dy = c.y - y;
    return dx * dx + dy * dy < c.r * c.r;
  }

  /**
   * Cone/arc hit test: target circle (tx, ty, tr) is within `range` of the origin and its centre lies within
   * `halfArc` of `dirAngle`, widened by the angle the target circle subtends so edges count as hits.
   */
  function inCone(ox, oy, dirAngle, halfArc, range, tx, ty, tr) {
    const dx = tx - ox;
    const dy = ty - oy;
    const d = Math.hypot(dx, dy);
    if (d > range + tr) return false;
    if (d <= tr) return true;
    let diff = Math.atan2(dy, dx) - dirAngle;
    diff = Math.atan2(Math.sin(diff), Math.cos(diff));
    return Math.abs(diff) <= halfArc + Math.asin(Math.min(1, tr / d));
  }

  RPG.world.collision = {
    moveCircle,
    circleOverlapsSolid,
    sweepProjectile,
    circlesOverlap,
    circleHitsPoint,
    inCone,
  };
})(globalThis.RPG = globalThis.RPG || {});

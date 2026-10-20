/* Map drawer: draws only the tiles the camera can see, in world space (the caller sets the transform).
   Floors get a sparse, deterministic alt-tone from a hash of tile coords, so the same map always looks the same.
   `pal` is a palette object (render/palette.js shape); it defaults to RPG.render.palette. Headless: no DOM. */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  var TILE = null; // RPG.world.tilemap.TILE, resolved on first draw
  var ALT_FLOOR_MASK = 3; // one floor tile in four takes the alt tone (hash & 3 === 0)
  var WALL_EDGE_PX = 2;

  /** Integer hash of tile coords -> uint32. Pure and stable across runs. */
  function tileHash(tx, ty) {
    var h = Math.imul(tx, 374761393) ^ Math.imul(ty, 668265263);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return (h ^ (h >>> 16)) >>> 0;
  }

  /** Tile index range [tx0..tx1] x [ty0..ty1] visible through the camera, clamped to the map. Empty if off-map. */
  function visibleRange(map, cam, viewW, viewH) {
    var ts = map.tileSize;
    var halfW = viewW / 2 / cam.zoom, halfH = viewH / 2 / cam.zoom;
    return {
      tx0: Math.max(0, Math.floor((cam.x - halfW) / ts)),
      tx1: Math.min(map.w - 1, Math.floor((cam.x + halfW) / ts)),
      ty0: Math.max(0, Math.floor((cam.y - halfH) / ts)),
      ty1: Math.min(map.h - 1, Math.floor((cam.y + halfH) / ts))
    };
  }

  function drawWall(ctx, x, y, ts, pal) {
    var topH = Math.max(2, Math.round(ts * 0.18));
    ctx.fillStyle = pal.wall;
    ctx.fillRect(x, y, ts, ts);
    ctx.fillStyle = pal.wallTop;
    ctx.fillRect(x, y, ts, topH);
    // Outline: a darker rim on the bottom and right faces, so adjacent walls read as separate blocks.
    ctx.fillStyle = pal.wallEdge;
    ctx.fillRect(x, y + ts - WALL_EDGE_PX, ts, WALL_EDGE_PX);
    ctx.fillRect(x + ts - WALL_EDGE_PX, y, WALL_EDGE_PX, ts);
  }

  function drawWater(ctx, x, y, ts, pal) {
    ctx.fillStyle = pal.water;
    ctx.fillRect(x, y, ts, ts);
  }

  function drawDoor(ctx, x, y, ts, pal) {
    ctx.fillStyle = pal.floor;
    ctx.fillRect(x, y, ts, ts);
    ctx.fillStyle = pal.door;
    ctx.fillRect(x + ts * 0.2, y, ts * 0.6, ts);
  }

  function drawPortal(ctx, x, y, ts, pal) {
    ctx.fillStyle = pal.floor;
    ctx.fillRect(x, y, ts, ts);
    ctx.fillStyle = pal.portal;
    ctx.fillRect(x + ts * 0.25, y + ts * 0.25, ts * 0.5, ts * 0.5);
  }

  function drawDecor(ctx, x, y, ts, pal) {
    ctx.fillStyle = pal.floor;
    ctx.fillRect(x, y, ts, ts);
    ctx.fillStyle = pal.decor;
    ctx.fillRect(x + ts * 0.35, y + ts * 0.35, ts * 0.3, ts * 0.3);
  }

  function drawFloor(ctx, x, y, ts, pal, tx, ty) {
    ctx.fillStyle = (tileHash(tx, ty) & ALT_FLOOR_MASK) === 0 ? pal.floorAlt : pal.floor;
    ctx.fillRect(x, y, ts, ts);
  }

  /**
   * drawMap(ctx, map, camera, viewW, viewH, pal?) — culls to the camera's visible tiles, then draws each one.
   * `viewW`/`viewH` are CSS px; the camera's zoom sets how many world tiles fit. Off-map views draw nothing.
   */
  function drawMap(ctx, map, cam, viewW, viewH, pal) {
    TILE = TILE || RPG.world.tilemap.TILE;
    pal = pal || RPG.render.palette;
    var ts = map.tileSize;
    var r = visibleRange(map, cam, viewW, viewH);
    for (var ty = r.ty0; ty <= r.ty1; ty++) {
      for (var tx = r.tx0; tx <= r.tx1; tx++) {
        var id = map.tiles[ty * map.w + tx];
        var x = tx * ts, y = ty * ts;
        if (id === TILE.WALL) drawWall(ctx, x, y, ts, pal);
        else if (id === TILE.WATER) drawWater(ctx, x, y, ts, pal);
        else if (id === TILE.DOOR) drawDoor(ctx, x, y, ts, pal);
        else if (id === TILE.PORTAL) drawPortal(ctx, x, y, ts, pal);
        else if (id === TILE.DECOR) drawDecor(ctx, x, y, ts, pal);
        else drawFloor(ctx, x, y, ts, pal, tx, ty);
      }
    }
  }

  RPG.render.drawMap = drawMap;
})(globalThis.RPG = globalThis.RPG || {});

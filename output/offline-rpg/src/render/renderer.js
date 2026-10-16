/* Canvas renderer. Reads the world, never mutates it. Draw order (INTEGRATION-PLAN §3, W4-11):
   map -> drops -> effects below (telegraphs) -> enemy projectiles -> monsters -> player -> player projectiles
   -> effects above -> damage numbers -> HUD. Entity and effect drawers are looked up by name at call time and
   skipped when absent, so a layer that has not landed yet leaves the rest of the frame intact. */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  var DEFAULT_ZOOM = 1.5;

  /** Calls RPG.render[mod][fn](...args) if both exist. Looked up per call so tests can stub them. */
  function call(mod, fn, args) {
    var m = RPG.render[mod];
    if (m && typeof m[fn] === 'function') m[fn].apply(m, args);
  }

  /** Draws every item of `list` as fn(ctx, item, ...tail). A missing list is an empty layer. */
  function each(mod, fn, list, ctx, tail) {
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      call(mod, fn, [ctx, list[i]].concat(tail));
    }
  }

  /** create(canvas) -> { camera, viewW, viewH, resize(cssW, cssH, dpr), snapTo(world), draw(world, alpha, dt, ui) } */
  function create(canvas) {
    var ctx = canvas.getContext('2d');
    var cam = RPG.render.camera.create();
    cam.zoom = DEFAULT_ZOOM;
    var r = { camera: cam, viewW: 0, viewH: 0, dpr: 1 };

    r.resize = function (cssW, cssH, dpr) {
      r.viewW = cssW;
      r.viewH = cssH;
      r.dpr = dpr;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    };

    r.snapTo = function (world) {
      RPG.render.camera.snap(cam, world.player.x, world.player.y);
    };

    /**
     * alpha: 0..1 progress between the last two sim ticks. dt: real frame seconds, drives camera follow.
     * ui: optional HUD inputs (xpToNext, skillCooldowns, debug, fps, toasts). Passed straight to hud.buildHudModel.
     */
    r.draw = function (world, alpha, dt, ui) {
      var pal = RPG.render.palette;
      var p = world.player;
      var map = world.area.map;
      var camera = RPG.render.camera;
      var px = p.prevX + (p.x - p.prevX) * alpha;
      var py = p.prevY + (p.y - p.prevY) * alpha;
      camera.follow(cam, px, py, dt || 0);
      camera.clampToMap(cam, map.w * map.tileSize, map.h * map.tileSize, r.viewW, r.viewH);
      camera.setShake(cam, world.fx ? world.fx.shake : 0);
      var jitter = camera.shakeOffset(cam, world.fx && world.fx.rng);
      var view = { x: cam.x + jitter.x, y: cam.y + jitter.y, zoom: cam.zoom };

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = pal.bg;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // World space -> device px, equivalent to camera.worldToScreen scaled by dpr.
      var k = r.dpr * view.zoom;
      ctx.setTransform(k, 0, 0, k, r.dpr * (r.viewW / 2) - view.x * k, r.dpr * (r.viewH / 2) - view.y * k);
      RPG.render.drawMap(ctx, map, view, r.viewW, r.viewH, pal);

      each('entities', 'drawDrop', world.drops, ctx, [world.time, pal]);
      call('effects', 'drawEffects', [ctx, world, alpha, pal, 'below']);
      each('entities', 'drawProjectile', world.enemyProjectiles, ctx, [alpha, pal]);
      each('entities', 'drawMonster', world.monsters, ctx, [alpha, pal]);
      call('entities', 'drawPlayer', [ctx, p, px, py, pal]);
      each('entities', 'drawProjectile', world.projectiles, ctx, [alpha, pal]);
      call('effects', 'drawEffects', [ctx, world, alpha, pal, 'above']);
      call('effects', 'drawDamageNumbers', [ctx, world, alpha, pal]);

      // HUD is laid out in CSS px, so it gets the dpr scale only, not the world view.
      ctx.setTransform(r.dpr, 0, 0, r.dpr, 0, 0);
      if (RPG.render.hud) {
        RPG.render.hud.draw(ctx, RPG.render.hud.buildHudModel(world, ui), r.viewW, r.viewH);
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    };

    return r;
  }

  RPG.render.create = create;
})(globalThis.RPG = globalThis.RPG || {});

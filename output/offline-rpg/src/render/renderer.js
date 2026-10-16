/* Canvas renderer: flat tiles, soft shadow, outlined circle player with a facing marker.
   Reads the world, never mutates it. Shadows are a plain alpha ellipse (no shadowBlur). */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  var TWO_PI = Math.PI * 2;
  var DEFAULT_ZOOM = 1.5;

  function drawPlayer(ctx, p, x, y, pal) {
    var r = p.r;
    // Soft shadow: stacked translucent ellipses under the feet.
    ctx.fillStyle = pal.shadow;
    for (var i = 0; i < 3; i++) {
      ctx.globalAlpha = 0.10;
      ctx.beginPath();
      ctx.ellipse(x, y + r * 0.55, r * (1.25 - i * 0.12), r * (0.62 - i * 0.07), 0, 0, TWO_PI);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    ctx.beginPath();
    ctx.arc(x, y, r, 0, TWO_PI);
    ctx.fillStyle = pal.player;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = pal.playerOutline;
    ctx.stroke();

    // Facing marker: a small dark wedge just inside the rim, pointing along `facing`.
    var c = Math.cos(p.facing), s = Math.sin(p.facing);
    var tipX = x + c * (r + 4), tipY = y + s * (r + 4);
    var baseX = x + c * (r * 0.35), baseY = y + s * (r * 0.35);
    var px = -s * r * 0.45, py = c * r * 0.45;
    ctx.beginPath();
    ctx.moveTo(tipX, tipY);
    ctx.lineTo(baseX + px, baseY + py);
    ctx.lineTo(baseX - px, baseY - py);
    ctx.closePath();
    ctx.fillStyle = pal.playerOutline;
    ctx.fill();
  }

  /** create(canvas) -> { camera, viewW, viewH, resize(cssW, cssH, dpr), snapTo(world), draw(world, alpha, dt) } */
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

    /** alpha: 0..1 progress between the last two sim ticks. dt: real frame seconds, drives camera follow. */
    r.draw = function (world, alpha, dt) {
      var pal = RPG.render.palette;
      var p = world.player;
      var px = p.prevX + (p.x - p.prevX) * alpha;
      var py = p.prevY + (p.y - p.prevY) * alpha;
      RPG.render.camera.follow(cam, px, py, dt || 0);

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = pal.bg;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // World space -> device px, equivalent to camera.worldToScreen scaled by dpr.
      var k = r.dpr * cam.zoom;
      ctx.setTransform(k, 0, 0, k, r.dpr * (r.viewW / 2) - cam.x * k, r.dpr * (r.viewH / 2) - cam.y * k);
      RPG.render.drawMap(ctx, world.area.map, cam, r.viewW, r.viewH, pal);
      drawPlayer(ctx, p, px, py, pal);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    };

    return r;
  }

  RPG.render.create = create;
})(globalThis.RPG = globalThis.RPG || {});

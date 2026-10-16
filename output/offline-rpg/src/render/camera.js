/* Camera: (x, y) is the world point at the viewport centre, in world px. Pure math; no DOM.
   worldToScreen / screenToWorld agree with input/intent.js screenToWorld (screen units are CSS px). */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  // Higher = snappier follow. Frame-rate independent via the exponential form below.
  var FOLLOW_RATE = 10;

  function create() {
    return { x: 0, y: 0, zoom: 1, shake: 0 };
  }

  function snap(cam, tx, ty) {
    cam.x = tx;
    cam.y = ty;
  }

  function follow(cam, tx, ty, dt) {
    var k = 1 - Math.exp(-FOLLOW_RATE * dt);
    cam.x += (tx - cam.x) * k;
    cam.y += (ty - cam.y) * k;
  }

  function worldToScreen(cam, x, y, viewW, viewH) {
    return { x: (x - cam.x) * cam.zoom + viewW / 2, y: (y - cam.y) * cam.zoom + viewH / 2 };
  }

  function screenToWorld(cam, sx, sy, viewW, viewH) {
    return { x: cam.x + (sx - viewW / 2) / cam.zoom, y: cam.y + (sy - viewH / 2) / cam.zoom };
  }

  RPG.render.camera = {
    create: create, snap: snap, follow: follow, worldToScreen: worldToScreen, screenToWorld: screenToWorld
  };
})(globalThis.RPG = globalThis.RPG || {});

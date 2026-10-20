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

  /** Keeps the viewport inside the map. A map smaller than the viewport is centred instead of clamped. */
  function clampToMap(cam, mapW, mapH, viewW, viewH) {
    var halfW = viewW / (2 * cam.zoom), halfH = viewH / (2 * cam.zoom);
    cam.x = axisClamp(cam.x, mapW, halfW);
    cam.y = axisClamp(cam.y, mapH, halfH);
  }

  function axisClamp(c, size, half) {
    if (size <= 2 * half) return size / 2;
    return c < half ? half : (c > size - half ? size - half : c);
  }

  /** shake is the amplitude in world px (the sim owns its decay). Negative or NaN input means no shake. */
  function setShake(cam, amount) {
    cam.shake = amount > 0 ? amount : 0;
  }

  /** Per-frame jitter in world px, drawn from the injected RNG so a replayed state shakes the same way. */
  function shakeOffset(cam, rng) {
    if (!(cam.shake > 0) || !rng) return { x: 0, y: 0 };
    return { x: rng.range(-cam.shake, cam.shake), y: rng.range(-cam.shake, cam.shake) };
  }

  function worldToScreen(cam, x, y, viewW, viewH) {
    return { x: (x - cam.x) * cam.zoom + viewW / 2, y: (y - cam.y) * cam.zoom + viewH / 2 };
  }

  function screenToWorld(cam, sx, sy, viewW, viewH) {
    return { x: cam.x + (sx - viewW / 2) / cam.zoom, y: cam.y + (sy - viewH / 2) / cam.zoom };
  }

  RPG.render.camera = {
    create: create, snap: snap, follow: follow, clampToMap: clampToMap, setShake: setShake, shakeOffset: shakeOffset,
    worldToScreen: worldToScreen, screenToWorld: screenToWorld
  };
})(globalThis.RPG = globalThis.RPG || {});

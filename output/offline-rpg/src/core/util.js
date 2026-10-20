/* Math, vector and array helpers. Pure; no DOM. */
(function (RPG) {
  'use strict';

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeOutCubic(t) { var u = 1 - t; return 1 - u * u * u; }
  function dist2(ax, ay, bx, by) { var dx = bx - ax, dy = by - ay; return dx * dx + dy * dy; }
  function dist(ax, ay, bx, by) { return Math.sqrt(dist2(ax, ay, bx, by)); }
  function angleTo(ax, ay, bx, by) { return Math.atan2(by - ay, bx - ax); }
  function norm(x, y) {
    var l = Math.sqrt(x * x + y * y);
    return l === 0 ? { x: 0, y: 0 } : { x: x / l, y: y / l };
  }
  function fromAngle(a, len) {
    if (len === undefined) len = 1;
    return { x: Math.cos(a) * len, y: Math.sin(a) * len };
  }
  /** Wrap to (-PI, PI]. */
  function wrapAngle(a) {
    var TWO = Math.PI * 2;
    a = a % TWO;
    if (a <= -Math.PI) a += TWO;
    else if (a > Math.PI) a -= TWO;
    return a;
  }
  /** Swap-remove: O(1), does not preserve order. Returns the removed element. */
  function removeAt(arr, i) {
    var last = arr.length - 1;
    var removed = arr[i];
    if (i < last) arr[i] = arr[last];
    arr.length = last;
    return removed;
  }
  function deepFreeze(obj) {
    if (obj && typeof obj === 'object' && !Object.isFrozen(obj)) {
      Object.freeze(obj);
      Object.keys(obj).forEach(function (k) { deepFreeze(obj[k]); });
    }
    return obj;
  }
  /** JSON-safe values only. */
  function deepClone(obj) { return obj === undefined ? undefined : JSON.parse(JSON.stringify(obj)); }
  function fmt(n) {
    var a = Math.abs(n);
    if (a >= 1e6) return trim(n / 1e6) + 'M';
    if (a >= 1e3) return trim(n / 1e3) + 'k';
    return String(Math.round(n));
  }
  function trim(v) { return (Math.round(v * 10) / 10).toString(); }

  RPG.core.util = {
    clamp: clamp, lerp: lerp, easeOutCubic: easeOutCubic, dist2: dist2, dist: dist, angleTo: angleTo,
    norm: norm, fromAngle: fromAngle, wrapAngle: wrapAngle, removeAt: removeAt,
    deepFreeze: deepFreeze, deepClone: deepClone, fmt: fmt
  };
})(globalThis.RPG);

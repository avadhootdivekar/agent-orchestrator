/* Seeded RNG (mulberry32). State is a single uint32, so it serializes trivially. */
(function (RPG) {
  'use strict';

  function Rng(seed) { this.s = seed >>> 0; }

  Rng.create = function (seed) { return new Rng(seed === undefined ? 0 : seed); };

  /** FNV-1a 32-bit. */
  Rng.hashString = function (str) {
    var h = 0x811c9dc5;
    str = String(str);
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return h >>> 0;
  };
  Rng.seedFromString = function (str) { return Rng.hashString(str); };

  var P = Rng.prototype;

  /** -> float in [0,1) */
  P.next = function () {
    var t = (this.s = (this.s + 0x6D2B79F5) >>> 0);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  /** integer in [lo, hi] inclusive */
  P.int = function (lo, hi) { return lo + Math.floor(this.next() * (hi - lo + 1)); };
  P.range = function (lo, hi) { return lo + this.next() * (hi - lo); };
  P.chance = function (p) { return this.next() < p; };
  P.pick = function (arr) { return arr.length ? arr[Math.floor(this.next() * arr.length)] : undefined; };
  /** weightFn(item) -> weight >= 0. Returns undefined if total weight is 0. */
  P.weighted = function (items, weightFn) {
    var total = 0, i, w;
    for (i = 0; i < items.length; i++) { w = weightFn(items[i]); if (w > 0) total += w; }
    if (total <= 0) return undefined;
    var r = this.next() * total, last;
    for (i = 0; i < items.length; i++) {
      w = weightFn(items[i]);
      if (!(w > 0)) continue;
      last = items[i];
      r -= w;
      if (r < 0) return items[i];
    }
    return last;
  };
  /** In-place Fisher-Yates. */
  P.shuffle = function (arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(this.next() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  };
  /** Child RNG seeded from (current state, label). Does not advance this RNG. */
  P.fork = function (label) {
    return new Rng((this.s ^ Rng.hashString(label)) >>> 0);
  };
  P.getState = function () { return this.s >>> 0; };
  P.setState = function (s) { this.s = s >>> 0; };

  RPG.core.Rng = Rng;
})(globalThis.RPG);

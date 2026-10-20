/* Object pool. `active` holds acquired objects; release swap-removes in O(1) after an indexOf. */
(function (RPG) {
  'use strict';

  function create(factory, reset, prealloc) {
    var free = [];
    var active = [];
    for (var i = 0; i < (prealloc || 0); i++) free.push(factory());
    return {
      active: active,
      acquire: function () {
        var o = free.length ? free.pop() : factory();
        active.push(o);
        return o;
      },
      /** Returns false if obj is not currently active (double release is a no-op). */
      release: function (obj) {
        var idx = active.indexOf(obj);
        if (idx < 0) return false;
        RPG.core.util.removeAt(active, idx);
        if (reset) reset(obj);
        free.push(obj);
        return true;
      },
      freeCount: function () { return free.length; }
    };
  }

  RPG.core.Pool = { create: create };
})(globalThis.RPG);

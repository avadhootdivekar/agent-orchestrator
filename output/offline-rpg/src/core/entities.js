/* Entity container: integer ids from a counter, O(1) id lookup, swap-remove, optional pooling.
   Pass `world`-like { nextEntityId } as the counter holder so ids survive save/load. */
(function (RPG) {
  'use strict';

  /** @param {{nextEntityId:number}} counter  object holding the id counter (usually the world) */
  function create(counter) {
    var list = [];
    var byId = Object.create(null);
    return {
      list: list,
      add: function (ent) {
        if (ent.id === undefined || ent.id === null) ent.id = counter.nextEntityId++;
        else if (ent.id >= counter.nextEntityId) counter.nextEntityId = ent.id + 1;
        if (byId[ent.id]) throw new Error('duplicate entity id ' + ent.id);
        byId[ent.id] = ent;
        list.push(ent);
        return ent;
      },
      get: function (id) { return byId[id] || null; },
      has: function (id) { return !!byId[id]; },
      remove: function (idOrEnt) {
        var id = typeof idOrEnt === 'object' ? idOrEnt.id : idOrEnt;
        var ent = byId[id];
        if (!ent) return null;
        delete byId[id];
        RPG.core.util.removeAt(list, list.indexOf(ent));
        return ent;
      },
      /** Remove all entities with `dead` set; returns removed ones (e.g. to release to a pool). */
      sweepDead: function () {
        var removed = [];
        for (var i = list.length - 1; i >= 0; i--) {
          if (list[i].dead) {
            var e = list[i];
            delete byId[e.id];
            RPG.core.util.removeAt(list, i);
            removed.push(e);
          }
        }
        return removed;
      },
      clear: function () { list.length = 0; byId = Object.create(null); },
      get size() { return list.length; }
    };
  }

  /** Base entity fields per ARCHITECTURE §5. */
  function baseEntity(kind, x, y, r, team) {
    return {
      id: null, kind: kind, x: x || 0, y: y || 0, vx: 0, vy: 0, r: r === undefined ? 10 : r, facing: 0,
      hp: 1, maxHp: 1, team: team || 0, dead: false, flash: 0, status: {}
    };
  }

  RPG.core.entities = { create: create, baseEntity: baseEntity };
})(globalThis.RPG);

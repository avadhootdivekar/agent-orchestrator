/* Sim -> UI/audio signal bus. Each emit() is delivered synchronously to the listeners registered for that
   type, and is also appended to a queue that the owner of the tick drains once per frame with drain().
   Listeners run in registration order; a listener that throws is counted in `errors` and never stops the
   others or propagates to the emitter. Headless: no DOM, no timers. */
(function (RPG) {
  'use strict';

  /** Every event type the sim emits. Use these constants instead of string literals. */
  var NAMES = Object.freeze({
    HIT: 'hit',
    KILL: 'kill',
    PLAYER_DIED: 'playerDied',
    LEVEL_UP: 'levelUp',
    PICKUP: 'pickup',
    SKILL_USED: 'skillUsed',
    BOSS_PHASE: 'bossPhase',
    BOSS_WARN: 'bossWarn',
    AREA_CHANGED: 'areaChanged',
    ITEM_DROPPED: 'itemDropped',
    SAVE: 'save',
    LOAD: 'load',
    UI_SOUND: 'uiSound'
  });

  /** Creates an independent bus. `RPG.core.events` is the shared instance the game uses. */
  function create() {
    var listeners = Object.create(null); // type -> fn[]
    var queue = [];
    var errorCount = 0;

    var api = {
      NAMES: NAMES,
      create: create,

      /** Subscribes `fn(payload, type)` to `type`. Registering the same fn twice is a no-op.
       *  Returns an unsubscribe function. */
      on: function (type, fn) {
        var list = listeners[type] || (listeners[type] = []);
        if (list.indexOf(fn) === -1) list.push(fn);
        return function () { api.off(type, fn); };
      },

      /** Removes `fn` from `type`. Unknown pairs are ignored. */
      off: function (type, fn) {
        var list = listeners[type];
        if (!list) return;
        var i = list.indexOf(fn);
        if (i !== -1) list.splice(i, 1);
      },

      /** Delivers `payload` to the listeners now and queues it for drain(). */
      emit: function (type, payload) {
        queue.push({ type: type, payload: payload });
        var list = listeners[type];
        if (!list) return;
        // Iterate over a snapshot so a listener that calls off() or on() cannot skip or repeat others.
        var snapshot = list.slice();
        for (var i = 0; i < snapshot.length; i++) {
          try {
            snapshot[i](payload, type);
          } catch (err) {
            errorCount++;
          }
        }
      },

      /** Returns the events emitted since the last drain, oldest first, and clears the queue.
       *  Each entry is `{ type, payload }`. */
      drain: function () {
        var out = queue;
        queue = [];
        return out;
      },

      /** Number of listener exceptions swallowed so far. */
      get errors() { return errorCount; }
    };
    return api;
  }

  RPG.core.events = create();
})(globalThis.RPG);

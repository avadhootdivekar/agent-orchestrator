// Storage adapter over localStorage with an in-memory fallback, plus saveGame/loadGame/deleteSave built on it.
// A write goes to `<key>:tmp` first, is verified, then swapped into `<key>`; any failure restores the previous value.
// Nothing here touches localStorage at load time (headless rule); the default adapter is created lazily.
(function (RPG) {
  'use strict';
  var P = RPG.persistence = RPG.persistence || {};
  var KEY_PREFIX = 'offline-rpg:v1:';
  var SLOTS = ['auto', '1', '2', '3'];

  function memoryBackend() {
    var m = new Map();
    return {
      getItem: function (k) { return m.has(k) ? m.get(k) : null; },
      setItem: function (k, v) { m.set(k, String(v)); },
      removeItem: function (k) { m.delete(k); }
    };
  }

  function probeLocalStorage() {
    try {
      var ls = globalThis.localStorage;
      if (!ls) return null;
      var k = KEY_PREFIX + 'probe';
      ls.setItem(k, '1');
      var ok = ls.getItem(k) === '1';
      ls.removeItem(k);
      return ok ? ls : null;
    } catch (e) { return null; }
  }

  function msg(e) { return e && e.message ? e.message : String(e); }

  // createStorage({backend?}) -> adapter. backend needs getItem/setItem/removeItem (localStorage-shaped); omitted -> localStorage if usable, else memory.
  function createStorage(opts) {
    var backend = opts && opts.backend, persistent = true;
    if (!backend) { backend = probeLocalStorage(); if (!backend) { backend = memoryBackend(); persistent = false; } }
    function key(slot) {
      slot = String(slot);
      if (SLOTS.indexOf(slot) < 0) throw new Error('unknown slot "' + slot + '"');
      return KEY_PREFIX + 'slot:' + slot;
    }
    function safeGet(k) { try { return backend.getItem(k); } catch (e) { return null; } }
    function safeRemove(k) { try { backend.removeItem(k); return true; } catch (e) { return false; } }

    var a = {
      backend: backend,
      available: function () { return persistent; },
      read: function (slot) { return safeGet(key(slot)); },
      readPending: function (slot) { return safeGet(key(slot) + ':tmp'); },
      readCorrupt: function (slot) { return safeGet(key(slot) + ':corrupt'); },
      // write(slot, json) -> {ok:true} | {ok:false, error}. On failure the previous value of the slot is still there.
      write: function (slot, json) {
        var k = key(slot), tmp = k + ':tmp', prev = safeGet(k);
        if (typeof json !== 'string' || !json) return { ok: false, error: 'nothing to write' };
        function restore() {
          try { if (safeGet(k) !== prev) { if (prev === null) backend.removeItem(k); else backend.setItem(k, prev); } } catch (e) { /* best effort */ }
        }
        try { backend.setItem(tmp, json); } catch (e) { safeRemove(tmp); return { ok: false, error: 'write failed: ' + msg(e) }; }
        if (safeGet(tmp) !== json) { safeRemove(tmp); return { ok: false, error: 'write verification failed' }; }
        try { backend.setItem(k, json); } catch (e) { restore(); safeRemove(tmp); return { ok: false, error: 'write failed: ' + msg(e) }; }
        if (safeGet(k) !== json) { restore(); safeRemove(tmp); return { ok: false, error: 'write verification failed' }; }
        safeRemove(tmp);
        return { ok: true };
      },
      // Moves an unreadable slot aside to `<key>:corrupt` (kept for recovery/export) instead of deleting it.
      quarantine: function (slot) {
        var k = key(slot), v = safeGet(k);
        if (v === null) return false;
        try { backend.setItem(k + ':corrupt', v); } catch (e) { return false; }
        return safeRemove(k);
      },
      remove: function (slot) {
        var k = key(slot);
        var r = safeRemove(k);
        safeRemove(k + ':tmp');
        return r;
      },
      list: function () {
        var out = [];
        SLOTS.forEach(function (slot) {
          var raw = a.read(slot);
          if (raw === null) return;
          try {
            var d = JSON.parse(raw), m = d.meta || {};
            out.push({ slot: slot, savedAt: d.savedAt, level: m.level, area: m.area, playtime: m.playtime });
          } catch (e) { out.push({ slot: slot, corrupt: true }); }
        });
        return out;
      }
    };
    return a;
  }

  var def = null;
  function getDefault() { return def || (def = createStorage()); }
  function setDefault(s) { def = s; }

  var storage = {
    KEY_PREFIX: KEY_PREFIX, SLOTS: SLOTS, createStorage: createStorage, getDefault: getDefault, setDefault: setDefault,
    list: function () { return getDefault().list(); },
    read: function (slot) { return getDefault().read(slot); },
    write: function (slot, json) { return getDefault().write(slot, json); },
    remove: function (slot) { return getDefault().remove(slot); },
    available: function () { return getDefault().available(); }
  };

  // saveGame(slot, state, {storage?, indent?, now?}) -> {ok:true} | {ok:false, code:'shape'|'storage', error}. Previous save survives any failure.
  function saveGame(slot, state, opts) {
    var s = (opts && opts.storage) || getDefault(), json;
    try { json = P.serialize(state, opts); } catch (e) { return { ok: false, code: e.code || 'shape', error: msg(e) }; }
    var check = P.deserialize(json);
    if (!check.ok) return { ok: false, code: check.code, error: 'refusing to write unreadable save: ' + check.error };
    var r;
    try { r = s.write(slot, json); } catch (e) { return { ok: false, code: 'storage', error: msg(e) }; }
    return r.ok ? { ok: true } : { ok: false, code: 'storage', error: r.error };
  }

  // loadGame(slot, {storage?}) -> deserialize result, or {ok:false, code:'missing'|...}. An interrupted write is recovered from `:tmp`.
  // Unreadable (parse/shape) slots are moved to `:corrupt`; newer-version saves are left untouched.
  function loadGame(slot, opts) {
    var s = (opts && opts.storage) || getDefault(), raw, r, recovered = false;
    try { raw = s.read(slot); } catch (e) { return { ok: false, code: 'missing', error: 'unknown slot' }; }
    if (raw === null) { raw = s.readPending(slot); recovered = raw !== null; }
    if (raw === null) return { ok: false, code: 'missing', error: 'no save in slot ' + slot };
    r = P.deserialize(raw);
    if (!r.ok && !recovered && (r.code === 'parse' || r.code === 'shape')) s.quarantine(slot);
    if (r.ok && recovered) r.warnings.push('recovered from an interrupted save');
    return r;
  }

  function deleteSave(slot, opts) {
    var s = (opts && opts.storage) || getDefault();
    try { s.remove(slot); return { ok: true }; } catch (e) { return { ok: false, code: 'storage', error: msg(e) }; }
  }

  P.storage = storage;
  P.saveGame = saveGame;
  P.loadGame = loadGame;
  P.deleteSave = deleteSave;
})(globalThis.RPG = globalThis.RPG || {});

// Save migrations: one step per version bump, applied to a deep clone. Never edit a published step; add a new one.
(function (RPG) {
  'use strict';
  var P = RPG.persistence = RPG.persistence || {};
  var SAVE_VERSION = 1;

  function fail(code, message) {
    var e = new Error(message);
    e.code = code;
    return e;
  }

  // MIGRATIONS[v] transforms a save at version v into version v+1.
  var MIGRATIONS = {
    // v0 -> v1: stub for pre-release saves (no rngState). Fills safe defaults only.
    0: function (s) {
      if (s.rngState === undefined && typeof s.seed === 'number') s.rngState = { world: s.seed, loot: s.seed };
      delete s.checksum; // content changed; the old checksum no longer applies
      s.version = 1;
      return s;
    }
  };

  // migrate(raw) -> migrated deep clone at SAVE_VERSION. Throws {code:'version'|'shape'} on unusable input; raw is never mutated.
  function migrate(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw fail('shape', 'save is not an object');
    if (typeof raw.version !== 'number' || !isFinite(raw.version) || Math.floor(raw.version) !== raw.version || raw.version < 0) {
      throw fail('shape', 'save version must be a non-negative integer');
    }
    if (raw.version > SAVE_VERSION) throw fail('version', 'saved by a newer game version (' + raw.version + ' > ' + SAVE_VERSION + ')');
    var s = JSON.parse(JSON.stringify(raw));
    while (s.version < SAVE_VERSION) {
      var step = MIGRATIONS[s.version];
      if (typeof step !== 'function') throw fail('version', 'no migration from save version ' + s.version);
      var from = s.version;
      s = step(s);
      if (!s || s.version !== from + 1) throw fail('version', 'migration from version ' + from + ' did not advance the version');
    }
    return s;
  }

  P.SAVE_VERSION = SAVE_VERSION;
  P.MIGRATIONS = MIGRATIONS;
  P.migrate = migrate;
  migrate.MIGRATIONS = MIGRATIONS;
})(globalThis.RPG = globalThis.RPG || {});

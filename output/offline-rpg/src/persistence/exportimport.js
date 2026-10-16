// Export / import as pure string functions (file picker, Blob download and clipboard UI live in ui/).
(function (RPG) {
  'use strict';
  var P = RPG.persistence = RPG.persistence || {};

  // exportJson(state, opts?) -> pretty JSON string (indent 2) in the save format. Throws {code:'shape'} for an invalid state.
  function exportJson(state, opts) {
    var o = {};
    if (opts) Object.keys(opts).forEach(function (k) { o[k] = opts[k]; });
    o.indent = 2;
    return P.serialize(state, o);
  }

  // importJson(text) -> deserialize result. Pure: validates and migrates only; the caller writes it to a slot (via saveGame) on success.
  function importJson(text) {
    if (typeof text === 'string') text = text.replace(/^﻿/, '');
    return P.deserialize(text);
  }

  // Suggested download file name, e.g. game-save-7-2026-10-16.json.
  function exportFileName(state) {
    var lvl = state && state.meta && state.meta.level, d = state && state.savedAt ? String(state.savedAt).slice(0, 10) : '';
    return lvl && d ? 'game-save-' + lvl + '-' + d + '.json' : 'game-save.json';
  }

  P.exportJson = exportJson;
  P.importJson = importJson;
  P.exportFileName = exportFileName;
})(globalThis.RPG = globalThis.RPG || {});

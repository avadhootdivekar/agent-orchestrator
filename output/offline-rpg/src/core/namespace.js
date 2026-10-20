/* Creates the single global namespace. Safe to load twice; works in node (globalThis). */
(function (root) {
  'use strict';
  var RPG = root.RPG = root.RPG || {};
  RPG.VERSION = RPG.VERSION || '1.0.0';
  RPG.SAVE_VERSION = RPG.SAVE_VERSION || 1;
  ['core', 'data', 'world', 'sim', 'render', 'ui', 'input', 'audio', 'persistence'].forEach(function (ns) {
    RPG[ns] = RPG[ns] || {};
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);

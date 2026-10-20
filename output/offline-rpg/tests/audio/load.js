'use strict';

// Loads audio modules into a bare vm context (no AudioContext, no window, no document) unless the caller passes
// globals in, so the tests exercise the real classic scripts rather than a copy of them.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC_ROOT = path.resolve(__dirname, '../../src');

/** Runs the given src files in one context. `globals` are installed first (e.g. a fake `AudioContext`). Returns `RPG`. */
function loadRPG(globals, rels) {
  const context = vm.createContext(Object.assign({}, globals));
  for (const rel of rels) {
    vm.runInContext(fs.readFileSync(path.join(SRC_ROOT, rel), 'utf8'), context, { filename: rel });
  }
  return context.RPG;
}

/** Returns `RPG.audio`. */
function loadAudio(globals) {
  return loadRPG(globals, ['core/namespace.js', 'audio/audio.js']).audio;
}

/** Returns the whole `RPG` namespace with the event bus, `RPG.audio` and `RPG.audio.bridge` loaded. */
function loadBridge(globals) {
  return loadRPG(globals, ['core/namespace.js', 'core/events.js', 'audio/audio.js', 'audio/bridge.js']);
}

module.exports = { loadAudio, loadBridge };

'use strict';

// Loads the audio module into a bare vm context (no AudioContext, no window, no document) unless the caller passes
// globals in, so the tests exercise the real classic script rather than a copy of it.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC_ROOT = path.resolve(__dirname, '../../src');

/** `globals` are installed on the context before the scripts run (e.g. a fake `AudioContext`). Returns `RPG.audio`. */
function loadAudio(globals) {
  const context = vm.createContext(Object.assign({}, globals));
  for (const rel of ['core/namespace.js', 'audio/audio.js']) {
    vm.runInContext(fs.readFileSync(path.join(SRC_ROOT, rel), 'utf8'), context, { filename: rel });
  }
  return context.RPG.audio;
}

module.exports = { loadAudio };

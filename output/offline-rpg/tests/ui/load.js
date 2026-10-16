'use strict';

// Loads the UI classic scripts into one shared global object, the same way the built page does, so the tests
// exercise the real files rather than copies of them. The keymap is loaded too, so screens resolve real key bindings.

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC_ROOT = path.resolve(__dirname, '../../src');

/** `relPaths` are relative to `src/`. Returns the `RPG` namespace the scripts populated. */
function loadRPG(relPaths) {
  const context = vm.createContext({});
  for (const rel of relPaths) {
    vm.runInContext(fs.readFileSync(path.join(SRC_ROOT, rel), 'utf8'), context, { filename: rel });
  }
  return context.RPG;
}

const UI_FILES = ['input/keymap.js', 'ui/screens.js'];

module.exports = { loadRPG, UI_FILES };

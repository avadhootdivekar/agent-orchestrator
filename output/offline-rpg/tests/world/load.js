'use strict';

// Loads the browser's classic scripts into one shared global object, the same way the built page does,
// so the tests exercise the real files rather than copies of them.

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

const WORLD_FILES = ['world/tilemap.js', 'world/collision.js'];

module.exports = { loadRPG, WORLD_FILES };

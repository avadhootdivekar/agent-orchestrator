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

// Everything areas.js needs, in build order: data, world helpers, mapgen, difficulty, encounters and the player.
const AREAS_FILES = [
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js',
  'core/state.js', 'data/difficulties.js', 'data/monsters.js', 'data/areas.js', 'world/stats.js',
  'world/tilemap.js', 'world/collision.js', 'world/spatial.js', 'world/mapgen.js', 'world/world.js',
  'world/difficulty.js', 'world/encounters.js', 'world/areas.js', 'sim/player.js',
];

module.exports = { loadRPG, WORLD_FILES, AREAS_FILES };

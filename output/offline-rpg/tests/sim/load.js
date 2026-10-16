'use strict';

// Loads the classic scripts the simulation needs into one vm context, in load order, like the built page does.

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC_ROOT = path.resolve(__dirname, '../../src');

const SIM_FILES = [
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/entities.js', 'core/state.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'data/maps/test-map.js', 'sim/player.js', 'sim/sim.js',
];

// Superset for tests that need pooled spawning, the spatial grid and the skill executor (JS_ORDER subset).
const FULL_FILES = [
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js', 'core/state.js',
  'data/maps/test-map.js', 'data/skills.js', 'data/monsters.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/spatial.js', 'world/world.js', 'world/combat.js',
  'world/skills.js', 'world/skill-effects.js', 'sim/player.js', 'sim/sim.js',
];

function loadSim(files) {
  const context = vm.createContext({});
  for (const rel of files || SIM_FILES) {
    vm.runInContext(fs.readFileSync(path.join(SRC_ROOT, rel), 'utf8'), context, { filename: rel });
  }
  return context.RPG;
}

// Values built inside the vm realm fail deepStrictEqual against host literals; round-trip to plain data.
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

const DT = 1 / 60;

function intent(mx, my, aim) {
  return { move: { x: mx, y: my }, aim: aim || { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} };
}

module.exports = { FULL_FILES, loadSim, plain, intent, DT };

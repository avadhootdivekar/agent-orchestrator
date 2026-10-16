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

function loadSim() {
  const context = vm.createContext({});
  for (const rel of SIM_FILES) {
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

module.exports = { loadSim, plain, intent, DT };

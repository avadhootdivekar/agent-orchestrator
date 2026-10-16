'use strict';

// Loads the render-layer classic scripts into one shared global object, the same way the built page does.
// Render files are headless here: the HUD drawer is exercised against a recording fake 2D context, not a DOM canvas.

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

const RENDER_FILES = ['render/palette.js', 'render/hud.js'];

module.exports = { loadRPG, RENDER_FILES };

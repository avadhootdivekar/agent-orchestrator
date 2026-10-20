'use strict';
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const CORE = path.join(__dirname, '..', '..', 'src', 'core');
const ORDER = ['namespace', 'util', 'rng', 'pool', 'entities', 'state', 'loop'];

/** Load core files into a fresh vm context that has NO window/document, like tests/run.js will. */
function loadCore(files) {
  const ctx = vm.createContext({});
  ctx.globalThis = ctx;
  for (const f of files || ORDER) {
    vm.runInContext(fs.readFileSync(path.join(CORE, f + '.js'), 'utf8'), ctx, { filename: f + '.js' });
  }
  return ctx.RPG;
}
module.exports = { loadCore, CORE, ORDER };

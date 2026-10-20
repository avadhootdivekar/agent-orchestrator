'use strict';
// Loads core/game.js plus the persistence files it uses into a fresh vm context with no window/document (headless rule).
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', '..', 'src');
const FILES = [
  'core/namespace', 'core/util', 'core/rng', 'core/events', 'core/loop', 'core/game',
  'persistence/migrate', 'persistence/save', 'persistence/storage', 'persistence/exportimport'
];

function loadGame() {
  const ctx = vm.createContext({});
  ctx.globalThis = ctx;
  for (const f of FILES) vm.runInContext(fs.readFileSync(path.join(SRC, f + '.js'), 'utf8'), ctx, { filename: f + '.js' });
  return ctx.RPG;
}
module.exports = { loadGame, FILES };

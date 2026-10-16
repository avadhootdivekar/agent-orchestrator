'use strict';
// Loads the classic-script input modules into a bare vm context (no window, no document), in load order.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC_DIR = path.join(__dirname, '..', '..', 'src', 'input');
const FILES = ['keymap.js', 'raw.js', 'intent.js', 'input.js'];

function loadInput() {
  const ctx = {};
  vm.createContext(ctx);
  for (const file of FILES) {
    vm.runInContext(fs.readFileSync(path.join(SRC_DIR, file), 'utf8'), ctx, { filename: file });
  }
  return ctx.RPG.input;
}

// Objects built inside the vm realm fail deepStrictEqual against host literals; round-trip to plain data first.
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

module.exports = { loadInput, plain };

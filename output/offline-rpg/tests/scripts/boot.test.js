'use strict';

// Boots the built page's script headlessly: every JS_ORDER file plus main.js runs in one vm context with a DOM stub,
// the same way index.html loads them. Checks the page wiring does not throw and that the loop and menus are live.

const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join, resolve } = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const PROJECT_ROOT = resolve(__dirname, '..', '..');
const CANVAS_WIDTH = 800;
const CANVAS_HEIGHT = 600;
const FRAME_MS = 1000 / 60;

function stubContext2d() {
  // Any 2D-context method is a no-op; properties are plain assignments.
  return new Proxy({ canvas: { width: CANVAS_WIDTH, height: CANVAS_HEIGHT } }, {
    get: (target, name) => (name in target ? target[name] : () => ({ width: 0 })),
    set: (target, name, value) => { target[name] = value; return true; },
  });
}

function bootPage() {
  const listeners = {};
  const frames = [];
  const document = {
    hidden: false,
    addEventListener() {},
    getElementById(id) {
      assert.equal(id, 'game');
      return canvas;
    },
  };
  const canvas = {
    clientWidth: CANVAS_WIDTH, clientHeight: CANVAS_HEIGHT, width: CANVAS_WIDTH, height: CANVAS_HEIGHT,
    ownerDocument: document,
    getContext: () => ctx2d,
    getBoundingClientRect: () => ({ left: 0, top: 0 }),
    addEventListener() {},
    removeEventListener() {},
  };
  const ctx2d = stubContext2d();
  let now = 0;
  const sandbox = {
    document,
    performance: { now: () => now },
    devicePixelRatio: 1,
    requestAnimationFrame: (cb) => frames.push(cb),
    cancelAnimationFrame() {},
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    console,
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  return { context, listeners, frames, advance: (ms) => { now += ms; } };
}

async function loadScripts(context) {
  const { JS_ORDER } = await import(pathToFileURL(join(PROJECT_ROOT, 'scripts', 'build.mjs')).href);
  for (const file of JS_ORDER) {
    vm.runInContext(readFileSync(join(PROJECT_ROOT, file), 'utf8'), context, { filename: file });
  }
}

test('index.html + main.js boot headlessly, open the main menu and start the loop', async () => {
  const page = bootPage();
  await loadScripts(page.context);
  const RPG = page.context.RPG;

  assert.equal(typeof RPG.main.start, 'function');
  const game = RPG.main.game;
  assert.equal(game.state, 'mainmenu');
  assert.equal(game.screens.top(), 'mainmenu');
  assert.ok(page.frames.length > 0, 'the loop requested its first animation frame');

  // Two real frames through the loop: menu draws, nothing throws.
  for (let i = 0; i < 2; i += 1) {
    page.advance(FRAME_MS);
    page.frames.splice(0).forEach((cb) => cb());
  }
  assert.ok(page.frames.length > 0, 'the loop keeps scheduling frames');
});

test('main-menu keys reach the menu: Enter, Enter starts a game that then steps', async () => {
  const page = bootPage();
  await loadScripts(page.context);
  const game = page.context.RPG.main.game;
  const runFrames = (count) => {
    for (let i = 0; i < count; i += 1) {
      page.advance(FRAME_MS);
      page.frames.splice(0).forEach((cb) => cb());
    }
  };

  const keydown = page.listeners.keydown;
  assert.ok(keydown.length >= 2, 'input and the menu key queue both listen for keydown');
  const press = (code) => keydown.forEach((fn) => fn({ code, preventDefault() {}, target: {} }));
  press('Enter'); // opens the new-game form
  runFrames(2);
  press('Enter'); // starts it
  runFrames(2);
  assert.equal(game.state, 'playing');
  assert.ok(game.world, 'a world exists after starting');

  const before = game.world.time;
  runFrames(30);
  assert.ok(game.world.time > before, 'simulation time advances while playing');
});

test('all browser hooks the page needs are present after boot', async () => {
  const page = bootPage();
  await loadScripts(page.context);
  const { screens } = page.context.RPG.main.game;
  ['mainmenu', 'pause', 'saveload', 'settings', 'dead', 'victory', 'playing'].forEach((id) => {
    assert.doesNotThrow(() => screens.open(id), id);
    screens.close(id);
  });
});

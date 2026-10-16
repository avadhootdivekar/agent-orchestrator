'use strict';
// HUD buttons (W7-03): on-canvas Inventory/Skills/Character/Pause buttons opened by pointer. Same harness as game-pointer.test.js.
// Mouse/touch presses reach the menu screens through game.queuePointer(), exactly as start()'s pointerdown listener
// does, and do what the keyboard path does (docs/INTEGRATION-PLAN.md §3, W6-01). Real screens, overlays and settings
// model; stub canvas context. Geometry below is the screens' own: the layout is the 800x600 stub view at (0, 0).

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine, memoryBackend, DT } = require('../integration/helpers.js');

const NO_TOGGLES = () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });

function stubCtx() {
  const texts = [];
  const target = { canvas: { width: 800, height: 600 }, texts };
  return new Proxy(target, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === 'fillText') return (text) => { texts.push(String(text)); };
      if (k === 'measureText') return (text) => ({ width: String(text).length * 7 });
      return () => {};
    },
    set(t, k, v) { t[k] = v; return true; }
  });
}

async function setup(opts) {
  opts = opts || {};
  const RPG = await loadEngine();
  const backend = opts.backend || memoryBackend();
  const storage = RPG.persistence.storage.createStorage({ backend });
  const screens = RPG.ui.screens.create();
  const ctx = stubCtx();
  const frames = [];
  const input = { buildFrame: () => Object.assign(NO_TOGGLES(), frames.shift() || {}) };
  const renderer = { camera: {}, viewW: 800, viewH: 600, dpr: 1, draw() {}, snapTo() {}, resize() {} };
  const settingsStore = RPG.ui.settings.create({ storage: backend });
  if (opts.difficulty) settingsStore.setDifficultyPreset(opts.difficulty);
  const game = RPG.core.game.create({ input, renderer, screens, ctx, storage, settingsStore, clock: { now: () => 0 } });
  const api = {
    RPG, game, screens, ctx, settingsStore, storage,
    frame(toggles, keys) {
      (keys || []).forEach((k) => game.queueKey(k));
      frames.push({ toggles: Object.assign({}, toggles) });
      if (game.shouldStep()) game.tick(DT);
      game.render(0);
    },
    key(code) { api.frame({}, [code]); },
    click(x, y, button) { game.queuePointer(x, y, button); api.frame({}); },
    startGame() { api.key('Enter'); api.key('Enter'); }
  };
  api.frame({}); // first render sizes the live layout from the renderer
  return api;
}


const centre = (g, id) => {
  const r = g.game.overlays().buttonRects({ w: 800, h: 600 }).find((b) => b.id === id);
  return [r.x + r.w / 2, r.y + r.h / 2];
};

for (const [id, screen] of [['inventory', 'inventory'], ['skills', 'skills'], ['character', 'character'], ['pause', 'pause']]) {
  test('HUD button ' + id + ': a pointer press opens its screen', async () => {
    const g = await setup();
    g.startGame();
    assert.equal(g.game.state, 'playing');
    g.click(...centre(g, id));
    assert.equal(g.screens.top(), screen);
    if (id === 'pause') assert.equal(g.game.state, 'paused');
  });
}

test('HUD buttons: presses outside their rectangles stay gameplay clicks', async () => {
  const g = await setup();
  g.startGame();
  g.click(400, 300);
  g.click(5, 5);
  const [x, y] = centre(g, 'inventory');
  g.click(x - 40, y);                 // left of the first button
  g.click(x, y - 40);                 // above it
  assert.equal(g.screens.top(), 'playing');
  assert.equal(g.game.state, 'playing');
});

test('HUD buttons: not active while an overlay is open, and a button press there goes to that screen', async () => {
  const g = await setup();
  g.startGame();
  g.click(...centre(g, 'inventory'));
  assert.equal(g.screens.top(), 'inventory');
  g.click(...centre(g, 'skills'));    // handled by the inventory screen's pointer path (back), not as a skills button
  assert.notEqual(g.screens.top(), 'skills');
});

test('HUD buttons are drawn on the canvas while playing only', async () => {
  const g = await setup();
  g.startGame();
  g.ctx.texts.length = 0;
  g.frame({});
  assert.ok(g.ctx.texts.includes('I') && g.ctx.texts.includes('||'));
  g.click(...centre(g, 'inventory'));
  g.ctx.texts.length = 0;
  g.frame({});
  assert.ok(!g.ctx.texts.includes('||'));
});

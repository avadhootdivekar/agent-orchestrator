'use strict';
// core/game.js wired to the real screens, overlays and settings model, driven headlessly with a stub canvas context
// (docs/INTEGRATION-PLAN.md §3, W5-04). Keys go through game.queueKey(), exactly as start()'s keydown listener does.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine, memoryBackend, DT, BOSS_ID } = require('../integration/helpers.js');

const NO_TOGGLES = () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });

// Every canvas method is a recorded no-op; every property can be assigned. Records the text drawn.
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
  const events = RPG.core.events;
  const screens = RPG.ui.screens.create();
  const ctx = stubCtx();
  const frames = [];
  const input = { buildFrame: () => Object.assign(NO_TOGGLES(), frames.shift() || {}) };
  const draws = [];
  const renderer = { camera: {}, viewW: 800, viewH: 600, dpr: 1, draw(w, a, dt, ui) { draws.push(ui); }, snapTo() {}, resize() {} };
  const settingsStore = opts.noSettings ? null : RPG.ui.settings.create({ storage: backend });
  const game = RPG.core.game.create({ input, renderer, screens, ctx, storage, settingsStore, clock: { now: () => 0 } });
  const api = {
    RPG, game, screens, ctx, backend, storage, settingsStore, draws, events, NAMES: events.NAMES,
    /** One frame: queued keys + optional toggle frame, then tick and render like the loop does. */
    frame(toggles, keys) {
      (keys || []).forEach((k) => game.queueKey(k));
      frames.push({ toggles: Object.assign({}, toggles) });
      if (game.shouldStep()) game.tick(DT);
      game.render(0);
    },
    key(code) { api.frame({}, [code]); },
    stack: () => screens.stack()
  };
  return api;
}

test('mainmenu -> new game -> pause -> overlays -> dead -> respawn, all through the real screens', async () => {
  const g = await setup();
  assert.equal(g.game.state, 'mainmenu');
  assert.equal(g.screens.top(), 'mainmenu');

  g.key('Enter');                 // New game -> form
  g.key('Enter');                 // Start game
  assert.equal(g.game.state, 'playing');
  assert.equal(g.screens.top(), 'playing');
  assert.ok(g.game.world.player, 'new game built a world');

  g.frame({ pause: true });       // the pause key while playing
  assert.equal(g.game.state, 'paused');
  assert.equal(g.screens.top(), 'pause');
  assert.ok(g.ctx.texts.includes('Paused'), 'pause screen was drawn');
  assert.equal(g.game.shouldStep(), false);
  g.key('Escape');
  assert.equal(g.game.state, 'playing');
  assert.equal(g.screens.paused(), false);

  for (const [toggle, id, closeKey] of [['inventory', 'inventory', 'KeyI'], ['skills', 'skills', 'KeyK'], ['character', 'character', 'KeyC']]) {
    g.frame({ [toggle]: true });
    assert.equal(g.screens.top(), id, id + ' opened by its toggle');
    assert.equal(g.game.shouldStep(), false, id + ' pauses the sim');
    g.key(closeKey);
    assert.equal(g.screens.top(), 'playing', id + ' closed by its key');
    assert.equal(g.game.shouldStep(), true);
  }

  const w = g.game.world;
  w.player.hp = 0;
  w.player.dead = true;           // the sim sees this on its next step and emits PLAYER_DIED
  g.frame({});
  assert.equal(g.game.state, 'dead');
  assert.equal(g.screens.top(), 'dead');
  g.key('Escape');                // terminal screen: Esc must not dismiss it
  assert.equal(g.game.state, 'dead');
  g.key('Enter');
  assert.equal(g.game.state, 'playing');
  assert.equal(g.screens.top(), 'playing');
  assert.equal(g.game.world.player.dead, false);
  assert.ok(g.game.world.player.hp > 0);
});

test('victory: the final boss opens the victory screen once and Enter continues', async () => {
  const g = await setup();
  g.key('Enter'); g.key('Enter');
  g.game.world.progress.bossesDefeated[BOSS_ID] = true;
  g.frame({});
  assert.equal(g.game.state, 'victory');
  assert.equal(g.screens.top(), 'victory');
  g.key('Enter');
  assert.equal(g.game.state, 'playing');
  assert.equal(g.screens.top(), 'playing');
});

test('the victory screen is reset for a later run', async () => {
  const g = await setup();
  g.key('Enter'); g.key('Enter');
  g.game.world.progress.bossesDefeated[BOSS_ID] = true;
  g.frame({}); g.key('Enter');
  g.game.newGame({ seed: 7 });
  g.game.world.progress.bossesDefeated[BOSS_ID] = true;
  g.frame({});
  assert.equal(g.screens.top(), 'victory', 'second victory shows the screen again');
});

test('quit to menu from pause returns to the main menu', async () => {
  const g = await setup();
  g.key('Enter'); g.key('Enter');
  g.frame({ pause: true });
  g.game.quitToMenu();
  assert.equal(g.game.state, 'mainmenu');
  assert.equal(g.screens.top(), 'mainmenu');
  assert.equal(g.game.world, null);
});

test('inventory and skills screens act on the current player after a new game', async () => {
  const g = await setup();
  g.game.newGame({ seed: 3 });
  const second = g.game.world.player;
  g.frame({ skills: true });
  g.key('Enter');                 // learn the cursor skill; must not throw even when it is refused
  g.key('KeyK');
  g.game.newGame({ seed: 4 });
  assert.notEqual(g.game.world.player, second);
  g.frame({ inventory: true });
  g.key('ArrowRight');
  assert.equal(g.screens.top(), 'inventory');
});

test('the HUD receives xpToNext, skill cooldowns, debug and fps', async () => {
  const g = await setup();
  g.game.newGame({ seed: 5 });
  g.frame({});
  const ui = g.draws[g.draws.length - 1];
  assert.equal(typeof ui.xpToNext, 'function');
  assert.ok(ui.xpToNext(1) > 0);
  assert.ok(Object.keys(ui.skillCooldowns).length > 0);
  assert.equal(ui.debug, false);
  assert.equal(typeof ui.fps, 'number');
  g.frame({ debug: true });
  assert.equal(g.draws[g.draws.length - 1].debug, true);
});

test('Delete Save leaves the standalone settings key intact', async () => {
  const g = await setup();
  const KEY = g.RPG.ui.settings.STORAGE_KEY;
  g.settingsStore.setVolume(0.25);
  assert.equal(g.game.persistSettings().ok, true);
  assert.notEqual(g.backend.getItem(KEY), null, 'settings written under their own key');

  g.game.newGame({ seed: 9 });
  assert.equal(g.game.saveToSlot('1').ok, true);
  assert.equal(g.game.deleteSave('1').ok, true);
  assert.equal(g.game.loadFromSlot('1').ok, false, 'the save is gone');
  assert.equal(JSON.parse(g.backend.getItem(KEY)).volume, 0.25, 'settings survived the delete');
});

test('stored settings are loaded at create and win over a loaded save\'s embedded copy', async () => {
  const backend = memoryBackend();
  const first = await setup({ backend });
  first.settingsStore.setVolume(0.1);
  first.settingsStore.setDifficultyPreset('hard');
  first.game.persistSettings();
  first.game.newGame({ seed: 2 });
  first.game.saveToSlot('1');

  const second = await setup({ backend });
  assert.equal(second.game.settings.volume, 0.1);
  second.settingsStore.setVolume(0.9);
  second.game.persistSettings();
  second.game.loadFromSlot('1');
  assert.equal(second.game.settings.volume, 0.1, 'the save did not override the settings');
  assert.equal(JSON.parse(backend.getItem(second.RPG.ui.settings.STORAGE_KEY)).volume, 0.9, 'load does not rewrite the key');
});

test('screenShake is a boolean everywhere; legacy numeric values are normalised', async () => {
  const g = await setup();
  assert.equal(g.game.settings.screenShake, true);
  g.game.applySettings({ screenShake: 0 });
  assert.equal(g.game.settings.screenShake, false);
  g.game.applySettings({ screenShake: 1 });
  assert.equal(g.game.settings.screenShake, true);
  assert.equal(g.settingsStore.snapshot().screenShake, true);
  g.game.newGame({ seed: 1 });
  g.game.world.fx.shake = 5;
  g.game.applySettings({ screenShake: false });
  g.game.render(0);
  assert.equal(g.game.world.fx.shake, 0, 'shake is off when the setting is off');
});

test('difficulty chosen in new-game or settings reaches setDifficulty', async () => {
  const g = await setup();
  g.game.newGame({ seed: 1, difficulty: 'hard' });
  assert.equal(g.game.difficultyId, 'hard');
  assert.equal(g.game.world.difficulty.id, 'hard');

  g.settingsStore.setDifficultyPreset('easy');
  g.game.applySettings(g.settingsStore.snapshot());
  assert.equal(g.game.difficultyId, 'easy', 'settings change retargets the running world');
  assert.equal(g.game.world.difficulty.id, 'easy');

  g.game.quitToMenu();
  g.game.newGame({ seed: 2 });    // no explicit difficulty: the settings choice is the default
  assert.equal(g.game.world.difficulty.id, 'easy');
});

test('a save keeps its own difficulty when settings say otherwise', async () => {
  const g = await setup();
  g.game.newGame({ seed: 1, difficulty: 'hard' });
  g.game.saveToSlot('1');
  g.settingsStore.setDifficultyPreset('easy');
  g.game.applySettings(g.settingsStore.snapshot());
  g.game.loadFromSlot('1');
  assert.equal(g.game.world.difficulty.id, 'hard');
});

test('core/game.js stays free of DOM globals at load', async () => {
  const RPG = await loadEngine();
  assert.equal(typeof RPG.core.game.create, 'function');
  assert.equal(typeof RPG.core.game.start, 'function');
});

test('the settings screen is reachable from the menu and the pause menu, and closing it applies the changes', async () => {
  const g = await setup();
  g.frame({ settings: true });
  assert.equal(g.screens.top(), 'mainmenu', 'no overlay on the main menu via the toggle');
  g.key('ArrowDown'); g.key('ArrowDown'); g.key('ArrowDown'); g.key('Enter'); // Settings row
  assert.equal(g.screens.top(), 'settings');
  g.settingsStore.setVolume(0.33);
  g.key('Escape');
  assert.equal(g.screens.top(), 'mainmenu');
  assert.equal(g.game.settings.volume, 0.33);

  g.game.newGame({ seed: 1 });
  g.frame({ settings: true });
  assert.equal(g.screens.top(), 'settings');
  g.key('Escape');
  assert.equal(g.screens.top(), 'playing');
});

test('a difficulty change in settings rescales monsters already in the area', async () => {
  const g = await setup();
  g.game.newGame({ seed: 4, difficulty: 'normal' });
  const world = g.game.world;
  g.RPG.world.areas.enter(world, 'forest', { silent: true });
  const live = world.monsters.filter((m) => m.diffScale);
  assert.ok(live.length > 0, 'area has scaled monsters');
  const before = live.map((m) => [m.maxHp, m.damage]);

  g.settingsStore.setDifficultyPreset('hard');
  g.game.applySettings(g.settingsStore.snapshot());
  const hard = g.RPG.data.difficulties.hard;
  live.forEach((m, i) => {
    assert.ok(m.maxHp > before[i][0], 'hp rose');
    assert.ok(Math.abs(m.damage / before[i][1] - hard.enemyDamage) < 1e-9, 'damage follows the new difficulty');
  });
});

'use strict';

// Main menu, pause menu and save/load screen, driven through the real screen manager and keymap. The game is a fake
// that records calls (core/game.js has its own tests); storage is the real adapter over an in-memory backend.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const FILES = ['core/namespace.js', 'core/util.js', 'data/difficulties.js', 'data/areas.js', 'input/keymap.js',
  'persistence/storage.js', 'ui/screens.js', 'ui/saveload.js', 'ui/mainmenu.js', 'ui/pause.js'];

// Canvas stand-in: records every text drawn, so assertions read what the player would see.
function fakeCtx() {
  const texts = [];
  return {
    texts,
    fillRect() {}, strokeRect() {},
    fillText(text) { texts.push(String(text)); },
    font: '', fillStyle: '', strokeStyle: '', lineWidth: 0,
  };
}

// Fake game: every method records its call. `results` overrides the outcome of the next call of that kind.
function fakeGame() {
  const calls = [];
  const ok = { ok: true };
  const game = {
    calls,
    results: {},
    newGame(opts) { calls.push(['newGame', opts]); return ok; },
    loadFromSlot(slot) { calls.push(['loadFromSlot', slot]); return game.results.load || ok; },
    saveToSlot(slot) { calls.push(['saveToSlot', slot]); return game.results.save || ok; },
    importSave(text, slot) { calls.push(['importSave', text, slot]); return game.results.import || ok; },
    exportSave(slot) {
      calls.push(['exportSave', slot]);
      return game.results.export || { ok: true, filename: 'game-save-7-2026-10-15.json', text: '{"format":"x"}' };
    },
    deleteSave(slot) { calls.push(['deleteSave', slot]); return ok; },
    setPaused(on) { calls.push(['setPaused', on]); },
    quitToMenu() { calls.push(['quitToMenu']); },
  };
  return game;
}

function memoryBackend() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
  };
}

// Builds the three screens on one manager, the way the browser wiring is expected to.
function setup({ backend, storage: storageOverride, readImportText, download, openSettings } = {}) {
  const RPG = loadRPG(FILES);
  const storage = storageOverride || RPG.persistence.storage.createStorage({ backend: backend || memoryBackend() });
  const screens = RPG.ui.screens.create();
  const game = fakeGame();
  const saveload = RPG.ui.saveload.createScreen({ game, screens, storage, readImportText, download });
  const mainmenu = RPG.ui.mainmenu.createScreen({ game, screens, saveload, storage });
  const pause = RPG.ui.pause.createScreen({ game, screens, saveload, openSettings });
  screens.register(saveload);
  screens.register(mainmenu);
  screens.register(pause);
  return { RPG, screens, storage, game, saveload, mainmenu, pause };
}

// The pause menu is only reachable during play, so tests open it over the playfield.
function openPause(ctx) {
  ctx.screens.open('playing');
  ctx.screens.open('pause');
}

// A save as the storage adapter's list() reads it: only savedAt and meta matter here.
function saveJson({ savedAt, level, area, playtime }) {
  return JSON.stringify({ savedAt, meta: { level, area, playtime } });
}

function press(screens, ...codes) {
  codes.forEach((code) => screens.handleKey(code));
}

function drawn(screens) {
  const ctx = fakeCtx();
  screens.draw(ctx);
  return ctx.texts.join('\n');
}

// Values built inside the vm context carry that realm's prototypes; round-trip them so deepEqual compares plainly.
const plain = (v) => JSON.parse(JSON.stringify(v));
const flush = () => new Promise((resolve) => setImmediate(resolve));
const OLDER = '2026-10-14T09:00:00.000Z';
const NEWER = '2026-10-15T20:30:00.000Z';

// ---- main menu ----------------------------------------------------------------------------------------------------

test('main menu offers New game, Continue and Import save', () => {
  const { screens } = setup();
  const text = drawn(screens);
  assert.match(text, /New game/);
  assert.match(text, /Continue/);
  assert.match(text, /Import save/);
});

test('Continue is disabled without a save and does nothing when chosen', () => {
  const { screens, game } = setup();
  press(screens, 'KeyS', 'Enter'); // Down to Continue, Enter
  assert.equal(screens.top(), 'mainmenu');
  assert.deepEqual(game.calls, []);
  assert.match(drawn(screens), /No save to continue/);
});

test('Continue loads the newest readable save and ignores unreadable ones', () => {
  const backend = memoryBackend();
  const { screens, game, storage } = setup({ backend });
  storage.write('1', saveJson({ savedAt: OLDER, level: 3, area: 'town', playtime: 600 }));
  storage.write('2', saveJson({ savedAt: NEWER, level: 9, area: 'caves', playtime: 5000 }));
  storage.write('3', 'this is not json');
  press(screens, 'KeyS', 'Enter');
  assert.deepEqual(plain(game.calls), [['loadFromSlot', '2']]);
});

test('a failed Continue keeps the menu open and shows the game error', () => {
  const { screens, game, storage } = setup();
  storage.write('1', saveJson({ savedAt: NEWER, level: 2, area: 'town', playtime: 10 }));
  game.results.load = { ok: false, code: 'shape', error: 'invalid save: player.hp missing' };
  press(screens, 'KeyS', 'Enter');
  assert.equal(screens.top(), 'mainmenu');
  assert.match(drawn(screens), /invalid save: player\.hp missing/);
});

test('New game passes the chosen difficulty and seed to the game', () => {
  const { screens, game } = setup();
  press(screens, 'Enter'); // open New game (difficulty row, default Normal)
  press(screens, 'ArrowRight'); // Normal -> Hard
  press(screens, 'ArrowDown'); // to the seed field
  press(screens, 'KeyA', 'Digit7', 'KeyB', 'Backspace', 'Digit9'); // "a7b", delete "b", add "9"
  press(screens, 'Enter');
  assert.deepEqual(plain(game.calls), [['newGame', { seed: 'a79', difficulty: 'hard' }]]);
});

test('a blank seed is passed as undefined so the game picks one; the seed is capped at 12 characters', () => {
  const blank = setup();
  press(blank.screens, 'Enter', 'Enter');
  assert.equal(blank.game.calls[0][1].seed, undefined, 'no seed typed: the game chooses');
  assert.equal(blank.game.calls[0][1].difficulty, 'normal');

  const capped = setup();
  press(capped.screens, 'Enter', 'ArrowDown');
  for (let i = 0; i < 20; i++) press(capped.screens, 'KeyZ');
  press(capped.screens, 'Enter');
  assert.equal(capped.game.calls[0][1].seed, 'zzzzzzzzzzzz');
});

test('typing W or S into the seed does not move the cursor', () => {
  const { screens, game } = setup();
  press(screens, 'Enter', 'ArrowDown', 'KeyW', 'KeyS', 'Enter');
  assert.equal(game.calls[0][1].seed, 'ws');
});

test('Escape leaves the New game form without starting a game', () => {
  const { screens, game } = setup();
  press(screens, 'Enter', 'Escape');
  assert.equal(screens.top(), 'mainmenu');
  assert.match(drawn(screens), /New game/);
  assert.deepEqual(game.calls, []);
});

test('Import from the main menu opens the save screen in import mode, and imports into the chosen slot', async () => {
  const readImportText = () => Promise.resolve('{"format":"offline-rpg-save"}');
  const { screens, game } = setup({ readImportText });
  press(screens, 'KeyS', 'KeyS', 'Enter'); // Down to Import save, Enter
  assert.equal(screens.top(), 'saveload');
  assert.match(drawn(screens), /Import save/);

  press(screens, 'KeyS', 'KeyG'); // slot 1 (the auto slot is refused for imports), then G
  await flush();
  assert.deepEqual(plain(game.calls), [['importSave', '{"format":"offline-rpg-save"}', '1']]);
});

test('importing into the auto slot is refused before any file is read', () => {
  let reads = 0;
  const { screens, game } = setup({ readImportText: () => { reads++; return Promise.resolve(null); } });
  press(screens, 'KeyS', 'KeyS', 'Enter'); // Import save; the cursor starts on the auto slot
  press(screens, 'KeyG');
  assert.equal(reads, 0);
  assert.deepEqual(game.calls, []);
  assert.match(drawn(screens), /auto save is written by the game/);
});

test('a failed import reports the game error and keeps the save screen open', async () => {
  const { screens, game } = setup({ readImportText: () => Promise.resolve('{}') });
  game.results.import = { ok: false, code: 'shape', error: 'not an offline-rpg save' };
  press(screens, 'KeyS', 'KeyS', 'Enter', 'KeyS', 'KeyG');
  await flush();
  assert.equal(screens.top(), 'saveload');
  assert.match(drawn(screens), /not an offline-rpg save/);
});

test('a cancelled file pick is reported and changes nothing', async () => {
  const { screens, game } = setup({ readImportText: () => Promise.resolve(null) });
  press(screens, 'KeyS', 'KeyS', 'Enter', 'KeyS', 'KeyG');
  await flush();
  assert.match(drawn(screens), /Import cancelled/);
  assert.deepEqual(game.calls, []);
});

// ---- save / load screen -------------------------------------------------------------------------------------------

test('Delete asks for confirmation; any key other than Enter cancels it', () => {
  const { screens, game, storage } = setup();
  storage.write('1', saveJson({ savedAt: NEWER, level: 7, area: 'forest', playtime: 3725 }));
  openPause({ screens });
  press(screens, 'KeyS', 'KeyS', 'Enter'); // pause: Down twice to Load game, Enter
  assert.equal(screens.top(), 'saveload');
  press(screens, 'KeyS', 'KeyD'); // to slot 1, ask to delete
  assert.match(drawn(screens), /Delete Slot 1\?/);
  press(screens, 'KeyS'); // cancels; this S does not move the cursor
  assert.deepEqual(game.calls, []);
  press(screens, 'KeyD', 'Enter'); // ask again, confirm
  assert.deepEqual(plain(game.calls), [['deleteSave', '1']]);
});

test('the save slot list shows level, area and time from the save metadata', () => {
  const { screens, storage } = setup();
  storage.write('2', saveJson({ savedAt: NEWER, level: 7, area: 'forest', playtime: 3725 }));
  openPause({ screens });
  press(screens, 'KeyS', 'KeyS', 'Enter'); // Load game
  const text = drawn(screens);
  assert.match(text, /Slot 2: Lv 7 \| Whispering Forest \| 2026-10-15 20:30 UTC \| 1h 02m/);
  assert.match(text, /Slot 1: empty/);
});

test('saving over an occupied slot needs confirmation; the auto slot cannot be written by hand', () => {
  const { screens, game, storage } = setup();
  storage.write('1', saveJson({ savedAt: NEWER, level: 4, area: 'town', playtime: 90 }));
  openPause({ screens });
  press(screens, 'KeyS', 'Enter'); // Save game
  assert.equal(screens.top(), 'saveload');

  press(screens, 'Enter'); // cursor is on the auto slot
  assert.match(drawn(screens), /auto save is written by the game/);

  press(screens, 'KeyS', 'Enter'); // slot 1 is occupied: asks first
  assert.match(drawn(screens), /Slot 1 will be overwritten/);
  assert.deepEqual(game.calls, []);

  press(screens, 'Enter'); // confirmed
  assert.deepEqual(plain(game.calls), [['saveToSlot', '1']]);
  assert.equal(screens.top(), 'pause', 'a successful save returns to the pause menu');
});

test('a failed save keeps the save screen open and shows the error', () => {
  const { screens, game } = setup();
  game.results.save = { ok: false, code: 'storage', error: 'write verification failed' };
  openPause({ screens });
  press(screens, 'KeyS', 'Enter', 'KeyS', 'Enter');
  assert.equal(screens.top(), 'saveload');
  assert.match(drawn(screens), /write verification failed/);
});

test('the save screen warns when storage is only the in-memory fallback', () => {
  const RPG = loadRPG(FILES);
  const memoryOnly = RPG.persistence.storage.createStorage(); // no backend and no localStorage: memory fallback
  assert.equal(memoryOnly.available(), false);
  const fallback = setup({ storage: memoryOnly });
  openPause({ screens: fallback.screens });
  press(fallback.screens, 'KeyS', 'KeyS', 'Enter');
  assert.match(drawn(fallback.screens), /lost when this page closes/);

  const persistent = setup();
  openPause({ screens: persistent.screens });
  press(persistent.screens, 'KeyS', 'KeyS', 'Enter');
  assert.doesNotMatch(drawn(persistent.screens), /lost when this page closes/);
});

test('export hands the slot to the download hook; without one it says so and does not throw', () => {
  const downloads = [];
  const withHook = setup({ download: (name, text) => downloads.push([name, text]) });
  withHook.storage.write('1', saveJson({ savedAt: NEWER, level: 7, area: 'forest', playtime: 3725 }));
  openPause({ screens: withHook.screens });
  press(withHook.screens, 'KeyS', 'KeyS', 'Enter', 'KeyS', 'KeyX');
  assert.deepEqual(plain(withHook.game.calls), [['exportSave', '1']]);
  assert.deepEqual(plain(downloads), [['game-save-7-2026-10-15.json', '{"format":"x"}']]);

  const noHook = setup();
  noHook.storage.write('1', saveJson({ savedAt: NEWER, level: 7, area: 'forest', playtime: 3725 }));
  openPause({ screens: noHook.screens });
  press(noHook.screens, 'KeyS', 'KeyS', 'Enter', 'KeyS', 'KeyX');
  assert.match(drawn(noHook.screens), /Export needs a file download hook/);
});

test('exporting an empty slot is refused', () => {
  const { screens, game } = setup();
  openPause({ screens });
  press(screens, 'KeyS', 'KeyS', 'Enter', 'KeyS', 'KeyX');
  assert.deepEqual(game.calls, []);
  assert.match(drawn(screens), /Nothing to export in Slot 1/);
});

// ---- pause menu ---------------------------------------------------------------------------------------------------

test('Resume closes the pause menu through the game', () => {
  const { screens, game } = setup();
  openPause({ screens });
  press(screens, 'Enter'); // Resume is the first item
  assert.deepEqual(plain(game.calls), [['setPaused', false]]);
  assert.equal(screens.top(), 'playing');
  assert.equal(screens.has('pause'), false);
});

test('Quit to menu asks for a second Enter before it leaves the game', () => {
  const { screens, game } = setup();
  openPause({ screens });
  press(screens, 'KeyS', 'KeyS', 'KeyS', 'KeyS', 'Enter'); // Quit to menu
  assert.match(drawn(screens), /Quit to menu without saving\?/);
  assert.equal(game.calls.some((c) => c[0] === 'quitToMenu'), false);
  press(screens, 'KeyP'); // any other key cancels
  assert.equal(game.calls.some((c) => c[0] === 'quitToMenu'), false);
  assert.equal(screens.top(), 'pause', 'the cancel key only cancels; Esc/P does not also close the menu');

  press(screens, 'Enter', 'Enter'); // ask, then confirm
  assert.equal(game.calls.some((c) => c[0] === 'quitToMenu'), true);
});

test('Settings in the pause menu is disabled until a settings screen is supplied', () => {
  const disabled = setup();
  openPause({ screens: disabled.screens });
  press(disabled.screens, 'KeyS', 'KeyS', 'KeyS', 'Enter'); // Settings
  assert.match(drawn(disabled.screens), /W4-13c/);

  let opened = 0;
  const enabled = setup({ openSettings: () => { opened++; } });
  openPause({ screens: enabled.screens });
  press(enabled.screens, 'KeyS', 'KeyS', 'KeyS', 'Enter');
  assert.equal(opened, 1);
});

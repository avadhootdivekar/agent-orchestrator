'use strict';

// The Settings canvas screen, driven through the real screen manager and keymap, plus its two entry points: the main
// menu's Settings row and the custom difficulty on the new-game form. The model is the real ui/settings.js over fakes.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const FILES = [
  'core/namespace.js', 'core/util.js', 'data/difficulties.js', 'world/difficulty.js', 'input/keymap.js',
  'ui/screens.js', 'ui/settings.js', 'ui/mainmenu.js',
];
const RPG = loadRPG(FILES);
const KEYMAP = RPG.input.keymap;

// Row positions in the screen's flat list: 7 general rows, 10 custom sliders, then one row per key action, then reset.
const ROW = Object.freeze({ volume: 0, sfx: 1, muted: 2, difficulty: 6, enemyHp: 7, attack: 17 + KEYMAP.ACTIONS.indexOf('attack'), reset: 17 + KEYMAP.ACTIONS.length });

// Canvas stand-in that records every string drawn.
function fakeCtx() {
  const texts = [];
  return {
    texts,
    fillRect() {}, strokeRect() {},
    fillText(text) { texts.push(String(text)); },
    font: '', fillStyle: '', strokeStyle: '', lineWidth: 0,
  };
}

function memoryBackend() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
  };
}

// RPG.input stand-in that keeps bindings with the real keymap rules.
function fakeInput() {
  const input = {
    bindings: KEYMAP.defaultBindings(),
    setBinding(action, code) { input.bindings = KEYMAP.withBinding(input.bindings, action, code); },
    resetBindings() { input.bindings = KEYMAP.defaultBindings(); },
  };
  return input;
}

function fakeAudio() {
  const audio = { volume: null, muted: null, setVolume(m, s) { audio.volume = [m, s]; }, mute(on) { audio.muted = on; } };
  return audio;
}

function drawn(screens) {
  const ctx = fakeCtx();
  screens.draw(ctx);
  return ctx.texts.join('\n');
}

const press = (screens, ...codes) => codes.forEach((code) => screens.handleKey(code));
const down = (screens, n) => { for (let i = 0; i < n; i++) screens.handleKey('ArrowDown'); };
const plain = (v) => JSON.parse(JSON.stringify(v));

// Settings screen on a manager with the settings open over the playfield.
function setup({ storage } = {}) {
  const input = fakeInput();
  const audio = fakeAudio();
  const settings = RPG.ui.settings.create({ storage: storage || memoryBackend(), input, audio });
  const screens = RPG.ui.screens.create();
  screens.register(RPG.ui.settings.createScreen({ settings }));
  screens.open('playing');
  screens.open('settings');
  return { RPG, settings, screens, input, audio };
}

// ---- the screen ---------------------------------------------------------------------------------------------------

test('settings screen draws every setting row, the custom sliders and every key binding without throwing', () => {
  const { screens } = setup();
  const text = drawn(screens);
  for (const label of ['Master volume', 'Sound effects', 'Mute', 'Quality', 'Damage numbers', 'Screen shake',
    'Difficulty', 'Enemy HP', 'Gold multiplier', 'Move up', 'Skill 6', 'Toggle inventory', 'Reset keys to defaults']) {
    assert.ok(text.includes(label), `missing row "${label}"`);
  }
  assert.ok(text.includes('KeyW / ArrowUp'), 'bound keys are shown');
  assert.ok(text.includes('50%'), 'master volume shows its percentage');
});

test('Left and Right step the master volume, and the change reaches the audio layer', () => {
  const { screens, settings, audio } = setup();
  press(screens, 'ArrowRight');
  assert.equal(settings.snapshot().volume, 0.55);
  assert.deepEqual(audio.volume, [0.55, 0.8]);
  press(screens, 'ArrowLeft', 'ArrowLeft', 'ArrowLeft', 'ArrowLeft', 'ArrowLeft', 'ArrowLeft', 'ArrowLeft',
    'ArrowLeft', 'ArrowLeft', 'ArrowLeft', 'ArrowLeft', 'ArrowLeft');
  assert.equal(settings.snapshot().volume, 0, 'clamped at zero, never negative');
  assert.equal(audio.volume[0], 0);
});

test('Enter toggles mute and the change reaches the audio layer', () => {
  const { screens, settings, audio } = setup();
  down(screens, ROW.muted);
  press(screens, 'Enter');
  assert.equal(settings.snapshot().muted, true);
  assert.equal(audio.muted, true);
});

test('the difficulty row cycles presets, custom included, and a slider switches to custom', () => {
  const { screens, settings } = setup();
  down(screens, ROW.difficulty);
  press(screens, 'ArrowRight');
  assert.equal(settings.snapshot().difficulty, 'hard', 'normal -> hard');

  down(screens, ROW.enemyHp - ROW.difficulty);
  press(screens, 'ArrowRight');
  assert.equal(settings.snapshot().difficulty, 'custom');
  assert.ok(settings.customConfig().enemyHp > 1, 'the slider stepped above the baseline');
  assert.equal(settings.currentDifficulty().id, 'custom');
});

test('custom sliders stay inside their ranges however often Left or Right is pressed', () => {
  const { screens, settings } = setup();
  down(screens, ROW.enemyHp);
  for (let i = 0; i < 40; i++) press(screens, 'ArrowRight');
  const [lo, hi] = plain(settings.customRanges()).enemyHp;
  assert.equal(settings.customConfig().enemyHp, hi);
  for (let i = 0; i < 80; i++) press(screens, 'ArrowLeft');
  assert.equal(settings.customConfig().enemyHp, lo);
});

test('rebinding: a key another action holds is refused with a message, and a free key is bound', () => {
  const { screens, settings, input } = setup();
  down(screens, ROW.attack);
  press(screens, 'Enter');
  press(screens, 'KeyW'); // moveUp holds KeyW
  assert.deepEqual(plain(settings.bindingsOf('attack')), ['Mouse0', 'KeyJ'], 'the refused key changes nothing');
  assert.match(drawn(screens), /KeyW is already used by Move up/);
  assert.equal(screens.top(), 'settings', 'capture stays open after a refusal');

  press(screens, 'KeyG');
  assert.deepEqual(plain(settings.bindingsOf('attack')), ['KeyG']);
  assert.deepEqual(plain(input.bindings.attack), ['KeyG'], 'the input layer follows the model');
  assert.match(drawn(screens), /Attack is now KeyG/);
});

test('Escape cancels a pending rebind without closing the screen; a second Escape closes it', () => {
  const { screens, settings } = setup();
  down(screens, ROW.attack);
  press(screens, 'Enter', 'Escape');
  assert.equal(screens.top(), 'settings');
  assert.deepEqual(plain(settings.bindingsOf('attack')), ['Mouse0', 'KeyJ']);
  assert.match(drawn(screens), /Rebinding cancelled/);

  press(screens, 'Escape');
  assert.equal(screens.top(), 'playing');
});

test('reset keys restores every default binding', () => {
  const { screens, settings, input } = setup();
  down(screens, ROW.attack);
  press(screens, 'Enter', 'KeyG');
  down(screens, ROW.reset - ROW.attack);
  press(screens, 'Enter');
  assert.deepEqual(plain(settings.snapshot().keys), {});
  assert.deepEqual(plain(input.bindings), plain(KEYMAP.defaultBindings()));
});

test('every change is saved at once, so a fresh model loads it', () => {
  const storage = memoryBackend();
  const { screens } = setup({ storage });
  press(screens, 'ArrowRight');
  const reloaded = RPG.ui.settings.create({ storage, input: fakeInput(), audio: fakeAudio() });
  assert.equal(reloaded.load().ok, true);
  assert.equal(reloaded.snapshot().volume, 0.55);
});

// ---- entry points: main menu ---------------------------------------------------------------------------------------

// Main menu wired to a recording game, and a settings model the custom difficulty reads from.
function menuSetup() {
  const game = { calls: [], newGame(opts) { game.calls.push(opts); return { ok: true }; }, loadFromSlot() { return { ok: true }; } };
  const settings = RPG.ui.settings.create({ storage: memoryBackend(), input: fakeInput(), audio: fakeAudio() });
  const screens = RPG.ui.screens.create();
  const saveload = { start() {} };
  const storage = { list: () => [], available: () => true };
  screens.register(RPG.ui.settings.createScreen({ settings }));
  screens.register(RPG.ui.mainmenu.createScreen({ game, screens, saveload, storage, settings }));
  return { screens, game, settings };
}

test('the main menu opens the settings screen from its Settings row', () => {
  const { screens } = menuSetup();
  assert.equal(screens.top(), 'mainmenu');
  down(screens, 3);
  press(screens, 'Enter');
  assert.equal(screens.top(), 'settings');
  press(screens, 'Escape');
  assert.equal(screens.top(), 'mainmenu', 'Esc returns to the main menu');
});

test('the new-game form offers custom difficulty, and starting with it passes the settings sliders', () => {
  const { screens, game, settings } = menuSetup();
  settings.setCustomField('enemyHp', 2);
  press(screens, 'Enter'); // New game
  // Setting a custom slider selected custom in the settings, and the form opens on the persisted difficulty.
  assert.match(drawn(screens), /Difficulty: < Custom >/);
  press(screens, 'ArrowRight', 'ArrowRight', 'ArrowRight', 'ArrowRight', 'ArrowRight'); // a full lap of the 5 choices
  assert.match(drawn(screens), /Difficulty: < Custom >/);
  press(screens, 'Enter');
  assert.equal(game.calls.length, 1);
  assert.equal(game.calls[0].difficulty.id, 'custom');
  assert.equal(game.calls[0].difficulty.enemyHp, 2, 'the game receives the custom values, not only the id');
});

test('starting a preset from the new-game form still passes its id', () => {
  const { screens, game } = menuSetup();
  press(screens, 'Enter', 'ArrowRight', 'Enter');
  assert.equal(game.calls[0].difficulty, 'hard');
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

// The settings model needs the difficulty data and logic plus the keymap; nothing from the DOM or canvas.
const FILES = [
  'core/namespace.js', 'core/util.js', 'data/difficulties.js', 'world/difficulty.js',
  'input/keymap.js', 'ui/settings.js',
];
const RPG = loadRPG(FILES);
const KEYMAP = RPG.input.keymap;

// Objects built inside the vm context have another realm's prototypes, so compare them as plain JSON.
const plain = (v) => JSON.parse(JSON.stringify(v));

// Localstorage-shaped fake backed by a Map. `failWrites` makes setItem throw, like a full quota.
function fakeStorage(opts) {
  const m = new Map();
  const failWrites = !!(opts && opts.failWrites);
  return {
    map: m,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => {
      if (failWrites) throw new Error('quota exceeded');
      m.set(k, String(v));
    },
    removeItem: (k) => m.delete(k),
  };
}

// Input stand-in that keeps its own bindings with the real keymap rules, so tests can check it mirrors the model.
function fakeInput() {
  const input = {
    bindings: KEYMAP.defaultBindings(),
    calls: [],
    setBinding(action, code) {
      input.calls.push([action, code]);
      input.bindings = KEYMAP.withBinding(input.bindings, action, code);
    },
    resetBindings() {
      input.bindings = KEYMAP.defaultBindings();
    },
  };
  return input;
}

function fakeAudio() {
  const audio = {
    volume: null,
    muted: null,
    setVolume(master, sfx) { audio.volume = [master, sfx]; },
    mute(on) { audio.muted = on; },
  };
  return audio;
}

function fresh(extra) {
  const storage = fakeStorage();
  const input = fakeInput();
  const audio = fakeAudio();
  const settings = RPG.ui.settings.create(Object.assign({ storage, input, audio }, extra));
  return { settings, storage, input, audio };
}

test('custom sliders are clamped to their ranges, never rejected', () => {
  const { settings } = fresh();
  const ranges = plain(settings.customRanges());

  settings.setCustomField('enemyHp', 99);
  assert.equal(settings.currentDifficulty().enemyHp, ranges.enemyHp[1]);

  settings.setCustomField('enemyHp', -5);
  assert.equal(settings.currentDifficulty().enemyHp, ranges.enemyHp[0]);

  settings.setCustomField('eliteChance', 0.3);
  assert.equal(settings.currentDifficulty().eliteChance, 0.3);

  // Every slider clamps the same way, and the stored config matches clampCustom's output exactly.
  for (const field of Object.keys(ranges)) {
    settings.setCustomField(field, Number.MAX_SAFE_INTEGER);
    assert.equal(settings.currentDifficulty()[field], ranges[field][1], `${field} high end`);
    settings.setCustomField(field, -Number.MAX_SAFE_INTEGER);
    assert.equal(settings.currentDifficulty()[field], ranges[field][0], `${field} low end`);
  }
  assert.equal(settings.currentDifficulty().id, 'custom');
});

test('a custom slider rejects non-numbers and unknown fields, and selecting one switches to custom', () => {
  const { settings } = fresh();
  assert.throws(() => settings.setCustomField('enemyHp', Number.NaN), /finite number/);
  assert.throws(() => settings.setCustomField('enemyHp', 'big'), /finite number/);
  assert.throws(() => settings.setCustomField('notASlider', 1), /no custom difficulty slider/);

  settings.setDifficultyPreset('hard');
  settings.setCustomField('goldMultiplier', 2);
  assert.equal(settings.currentDifficulty().id, 'custom');
  assert.equal(settings.currentDifficulty().goldMultiplier, 2);
});

test('presets resolve to their own config, and unknown ids throw', () => {
  const { settings } = fresh();
  settings.setDifficultyPreset('nightmare');
  assert.equal(settings.currentDifficulty().id, 'nightmare');
  assert.equal(settings.currentDifficulty().enemyHp, plain(RPG.data.difficulties.nightmare).enemyHp);
  assert.throws(() => settings.setDifficultyPreset('insane'), /unknown difficulty/);
  assert.equal(settings.currentDifficulty().id, 'nightmare', 'a failed selection leaves the old one');
});

test('rebinding to a key another action holds is rejected and changes nothing', () => {
  const { settings, input } = fresh();
  const before = plain(settings.snapshot());
  const callsBefore = input.calls.length;

  // KeyW is moveUp by default.
  const result = settings.rebind('attack', 'KeyW');
  assert.deepEqual(plain(result), { ok: false, reason: 'conflict', conflictsWith: 'moveUp' });
  assert.deepEqual(plain(settings.snapshot()), before);
  assert.equal(input.calls.length, callsBefore, 'input is not touched on a rejected rebind');
  assert.deepEqual(plain(settings.bindingsOf('moveUp')), ['KeyW', 'ArrowUp']);
});

test('rebinding with swap gives the other action the key this one had', () => {
  const { settings, input } = fresh();
  // skill6 is KeyQ, skill7 is KeyE. Moving skill6 onto KeyE hands KeyQ to skill7.
  const result = settings.rebind('skill6', 'KeyE', { swap: true });
  assert.deepEqual(plain(result), { ok: true, swapped: 'skill7' });
  assert.deepEqual(plain(settings.bindingsOf('skill6')), ['KeyE']);
  assert.deepEqual(plain(settings.bindingsOf('skill7')), ['KeyQ']);
  // The input layer ends with the same bindings the model holds.
  assert.deepEqual(plain(input.bindings.skill6), ['KeyE']);
  assert.deepEqual(plain(input.bindings.skill7), ['KeyQ']);
});

test('a plain rebind to a free key succeeds, and only that action changes', () => {
  const { settings, input } = fresh();
  assert.deepEqual(plain(settings.rebind('interact', 'KeyG')), { ok: true });
  assert.deepEqual(plain(settings.bindingsOf('interact')), ['KeyG']);
  assert.deepEqual(input.calls, [['interact', 'KeyG']]);
});

test('rebinding an unknown action throws', () => {
  const { settings } = fresh();
  assert.throws(() => settings.rebind('fly', 'KeyF'), /unknown input action/);
});

test('reset restores default keys and the snapshot keeps only changed ones', () => {
  const { settings, input } = fresh();
  assert.deepEqual(plain(settings.snapshot().keys), {}, 'defaults are not stored');

  settings.rebind('interact', 'KeyG');
  assert.deepEqual(Object.keys(settings.snapshot().keys), ['interact']);

  settings.resetKeys();
  assert.deepEqual(plain(settings.snapshot().keys), {});
  assert.deepEqual(plain(input.bindings), plain(KEYMAP.defaultBindings()));
});

test('settings survive a round trip through fake storage', () => {
  const storage = fakeStorage();
  const first = RPG.ui.settings.create({ storage, input: fakeInput(), audio: fakeAudio() });
  first.setVolume(0.25);
  first.setSfx(0.6);
  first.setMuted(true);
  first.setQuality('low');
  first.setShowDamageNumbers(false);
  first.setScreenShake(false);
  first.setCustomField('spawnDensity', 2.5);
  first.setDifficultyPreset('custom');
  first.rebind('interact', 'KeyG');
  assert.deepEqual(plain(first.save()), { ok: true });

  const input = fakeInput();
  const audio = fakeAudio();
  const second = RPG.ui.settings.create({ storage, input, audio });
  assert.deepEqual(plain(second.load()), { ok: true, applied: true, warnings: [] });

  assert.deepEqual(plain(second.snapshot()), plain(first.snapshot()));
  assert.deepEqual(plain(second.currentDifficulty()), plain(first.currentDifficulty()));
  assert.deepEqual(plain(second.bindingsOf('interact')), ['KeyG']);
  // The restored values reach the audio and input layers, not just the model.
  assert.deepEqual(audio.volume, [0.25, 0.6]);
  assert.equal(audio.muted, true);
  assert.deepEqual(plain(input.bindings.interact), ['KeyG']);
});

test('settings are stored under their own key, separate from the save slots', () => {
  const { settings, storage } = fresh();
  settings.setVolume(0.9);
  settings.save();
  assert.deepEqual([...storage.map.keys()], [RPG.ui.settings.STORAGE_KEY]);
  assert.equal(RPG.ui.settings.STORAGE_KEY.indexOf('slot:'), -1);
});

test('a damaged settings document keeps defaults for bad fields and reports them', () => {
  const storage = fakeStorage();
  storage.setItem(
    RPG.ui.settings.STORAGE_KEY,
    JSON.stringify({ volume: 7, sfx: 0.4, muted: 'yes', quality: 'ultra', custom: { enemyHp: 99 } }),
  );
  const { settings } = fresh({ storage });
  const result = settings.load();
  assert.equal(result.ok, true);
  assert.equal(result.applied, true);
  assert.deepEqual(plain(result.warnings).sort(), ['muted ignored', 'quality ignored', 'volume ignored'].sort());

  const snap = settings.snapshot();
  assert.equal(snap.volume, 0.5, 'out-of-range volume keeps the default');
  assert.equal(snap.sfx, 0.4, 'valid fields still apply');
  assert.equal(snap.muted, false);
  assert.equal(snap.quality, 'high');
});

test('a stored custom config is clamped on load, so loaded sliders stay in range', () => {
  const storage = fakeStorage();
  storage.setItem(
    RPG.ui.settings.STORAGE_KEY,
    JSON.stringify({ difficulty: 'custom', custom: { enemyHp: 1000, bossHp: -3 } }),
  );
  const { settings } = fresh({ storage });
  settings.load();
  const cfg = settings.currentDifficulty();
  assert.equal(cfg.enemyHp, plain(settings.customRanges()).enemyHp[1]);
  assert.equal(cfg.bossHp, plain(settings.customRanges()).bossHp[0]);
});

test('invalid JSON or a non-object document is refused without touching the defaults', () => {
  for (const raw of ['{not json', '[1,2]', '"text"']) {
    const storage = fakeStorage();
    storage.setItem(RPG.ui.settings.STORAGE_KEY, raw);
    const { settings } = fresh({ storage });
    const result = settings.load();
    assert.equal(result.ok, false, raw);
    assert.equal(result.applied, false, raw);
    assert.equal(settings.snapshot().volume, 0.5, raw);
  }
});

test('a missing settings key loads as a no-op', () => {
  const { settings } = fresh();
  assert.deepEqual(plain(settings.load()), { ok: true, applied: false, warnings: [] });
});

test('a failed write reports the error instead of throwing', () => {
  const storage = fakeStorage({ failWrites: true });
  const { settings } = fresh({ storage });
  const result = settings.save();
  assert.equal(result.ok, false);
  assert.match(result.error, /quota exceeded/);
});

test('volume, sfx and mute reach the audio layer, and volume is range-checked', () => {
  const { settings, audio } = fresh();
  settings.setVolume(0.3);
  settings.setSfx(0.6);
  assert.deepEqual(audio.volume, [0.3, 0.6]);
  settings.setMuted(true);
  assert.equal(audio.muted, true);
  assert.throws(() => settings.setVolume(1.5), /\[0, 1\]/);
  assert.throws(() => settings.setSfx(-0.1), /\[0, 1\]/);
  assert.deepEqual(audio.volume, [0.3, 0.6], 'a rejected value changes nothing');
});

test('quality accepts only the renderer levels', () => {
  const { settings } = fresh();
  settings.setQuality('low');
  assert.equal(settings.snapshot().quality, 'low');
  assert.throws(() => settings.setQuality('ultra'), /quality must be one of/);
  assert.equal(settings.snapshot().quality, 'low');
});

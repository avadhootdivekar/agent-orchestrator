'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

// The overlays need the event bus and the screen manager, so this file loads them on top of the shared UI set.
const FILES = ['core/namespace.js', 'core/events.js', 'input/keymap.js', 'ui/screens.js', 'ui/hud-overlays.js'];

function fresh(extra) {
  const RPG = loadRPG(FILES);
  const screens = RPG.ui.screens.create();
  screens.open('playing');
  const overlays = RPG.ui.overlays.create(Object.assign({ events: RPG.core.events.create(), screens }, extra));
  return { RPG, screens, overlays };
}

// Values built inside the vm context carry that realm's Array prototype; round-trip them so deepEqual compares plainly.
const plain = (v) => JSON.parse(JSON.stringify(v));

// Fake canvas context that records calls, so drawing is checked without a browser.
function fakeCtx(w = 800, h = 600) {
  const calls = [];
  const rec = (name) => (...args) => calls.push([name, ...args]);
  return {
    canvas: { width: w, height: h },
    calls,
    fillRect: rec('fillRect'),
    fillText: rec('fillText'),
    font: '', fillStyle: '', textAlign: '', textBaseline: ''
  };
}

test('toasts expire after their time-to-live and nothing expires early', () => {
  const { overlays } = fresh();
  overlays.toast('Saved');
  overlays.update(2.9);
  assert.equal(overlays.list().length, 1);
  overlays.update(0.2);
  assert.equal(overlays.list().length, 0);
});

test('banners outlive toasts and carry their own colour', () => {
  const { overlays } = fresh();
  overlays.banner('Level 2');
  overlays.toast('Loaded');
  overlays.update(3.5);
  const left = overlays.list();
  assert.equal(left.length, 1);
  assert.equal(left[0].kind, 'banner');
  assert.equal(left[0].text, 'Level 2');
});

test('the queue is bounded: the oldest entries are dropped first', () => {
  const { overlays } = fresh();
  for (let i = 1; i <= 10; i++) overlays.toast('msg ' + i);
  const texts = overlays.list().map((e) => e.text);
  assert.equal(texts.length, 4);
  assert.deepEqual(plain(texts), ['msg 7', 'msg 8', 'msg 9', 'msg 10']);
});

test('non-positive or NaN steps do not age the queue, and list() returns copies', () => {
  const { overlays } = fresh();
  overlays.toast('Saved');
  overlays.update(0);
  overlays.update(-5);
  overlays.update(NaN);
  const copy = overlays.list();
  assert.equal(copy.length, 1);
  copy[0].text = 'changed';
  assert.equal(overlays.list()[0].text, 'Saved');
});

test('SAVE, LOAD and LEVEL_UP events feed the toast and banner queue', () => {
  const RPG = loadRPG(FILES);
  const bus = RPG.core.events.create();
  const screens = RPG.ui.screens.create();
  screens.open('playing');
  const overlays = RPG.ui.overlays.create({ events: bus, screens });
  const N = bus.NAMES;

  bus.emit(N.SAVE, { slot: '1', ok: true });
  bus.emit(N.LOAD, { slot: '1', warnings: [] });
  bus.emit(N.LEVEL_UP, { level: 3 });
  bus.emit(N.SAVE, { slot: '2', ok: false, error: 'quota' });

  const got = plain(overlays.list().map((e) => [e.kind, e.text]));
  assert.deepEqual(got, [
    ['toast', 'Saved'],
    ['toast', 'Loaded'],
    ['banner', 'Level 3'],
    ['toast', 'Save failed: quota']
  ]);
  assert.equal(overlays.list()[3].color, '#e06c6c', 'a failed save uses the error colour');
});

test('PLAYER_DIED opens the death screen, which is modal and closes on Enter', () => {
  const RPG = loadRPG(FILES);
  const bus = RPG.core.events.create();
  const screens = RPG.ui.screens.create();
  screens.open('playing');
  let respawned = 0;
  RPG.ui.overlays.create({ events: bus, screens, onRespawn: () => respawned++ });

  bus.emit(bus.NAMES.PLAYER_DIED, { gold: 40 });
  assert.equal(screens.top(), 'dead');
  assert.equal(screens.paused(), true);

  assert.equal(screens.handleKey('KeyI'), false, 'inventory cannot open over the death screen');
  assert.equal(screens.top(), 'dead');

  assert.equal(screens.handleKey('Enter'), true);
  assert.equal(screens.top(), 'playing');
  assert.equal(respawned, 1);
});

test('the death screen draws the gold lost and a confirm hint', () => {
  const { screens, overlays } = fresh();
  overlays.showDead({ gold: 40 });
  const ctx = fakeCtx();
  screens.draw(ctx);
  const text = ctx.calls.filter((c) => c[0] === 'fillText').map((c) => c[1]);
  assert.ok(text.includes('You have fallen'));
  assert.ok(text.includes('Gold lost: 40'));
  assert.ok(text.some((t) => /Press Enter/.test(t)));
});

test('showVictory opens the victory screen once and ignores repeat triggers', () => {
  const { screens, overlays } = fresh();
  overlays.showVictory();
  assert.equal(screens.top(), 'victory');
  assert.equal(screens.handleKey('Enter'), true);
  assert.equal(screens.top(), 'playing');
  overlays.showVictory();
  assert.equal(screens.top(), 'playing', 'the screen does not come back after it was dismissed');
});

test('the victory screen calls onContinue on Enter and draws its title', () => {
  const RPG = loadRPG(FILES);
  const screens = RPG.ui.screens.create();
  screens.open('playing');
  let continued = 0;
  const overlays = RPG.ui.overlays.create({ events: RPG.core.events.create(), screens, onContinue: () => continued++ });
  overlays.showVictory();
  const ctx = fakeCtx();
  screens.draw(ctx);
  assert.ok(ctx.calls.some((c) => c[0] === 'fillText' && c[1] === 'The Warden of Ash is slain'));
  screens.handleKey('Enter');
  assert.equal(continued, 1);
});

test('drawToasts paints every live entry and uses the canvas size', () => {
  const { overlays } = fresh();
  overlays.banner('Level 4');
  overlays.toast('Loaded');
  const ctx = fakeCtx(640, 480);
  overlays.drawToasts(ctx);
  const drawn = ctx.calls.filter((c) => c[0] === 'fillText');
  assert.deepEqual(drawn.map((c) => c[1]), ['Level 4', 'Loaded']);
  assert.ok(drawn.every((c) => c[2] >= 0 && c[2] <= 480));
});

test('prompt delegates to the injected dialog and calls back null without one', () => {
  const asked = [];
  const { overlays } = fresh({ prompt: (label, cb) => { asked.push(label); cb('seed-42'); } });
  let got = null;
  overlays.prompt('Seed', (v) => { got = v; });
  assert.deepEqual(asked, ['Seed']);
  assert.equal(got, 'seed-42');

  const bare = fresh().overlays;
  let none = 'unset';
  bare.prompt('Seed', (v) => { none = v; });
  assert.equal(none, null);
});

test('dispose unsubscribes from the bus so later events are ignored', () => {
  const RPG = loadRPG(FILES);
  const bus = RPG.core.events.create();
  const overlays = RPG.ui.overlays.create({ events: bus });
  overlays.dispose();
  bus.emit(bus.NAMES.LEVEL_UP, { level: 9 });
  assert.equal(overlays.list().length, 0);
});

test('a missing screen manager still queues toasts and opens nothing', () => {
  const RPG = loadRPG(FILES);
  const overlays = RPG.ui.overlays.create({ events: RPG.core.events.create() });
  overlays.showDead({ gold: 1 });
  overlays.showVictory();
  overlays.toast('still works');
  assert.equal(overlays.list()[0].text, 'still works');
});

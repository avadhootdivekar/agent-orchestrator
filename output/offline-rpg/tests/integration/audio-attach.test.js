'use strict';
// Boot-level regression for the audio bridge (w12-02 finding): the shipped game built via RPG.core.game.create must
// route real sim events to RPG.audio without any test-side bridge.attach call.

const test = require('node:test');
const assert = require('node:assert/strict');
const { makeGame, DT } = require('./helpers');
const { FakeAudioContext } = require('../audio/fake-audio-context');

const attackIntent = () => ({ move: { x: 0, y: 0 }, aim: { x: 1000, y: 0 }, attack: true, skills: [], dash: false, toggles: {} });

async function boot() {
  const g = await makeGame({ withAudio: true, globals: { AudioContext: FakeAudioContext } });
  g.RPG.audio.init(FakeAudioContext); // the first-gesture step in start()
  return g;
}
const nodeCount = () => FakeAudioContext.last.created.length;

test('a real attack event reaches audio after newGame', async () => {
  const g = await boot();
  g.newGame(1);
  const before = nodeCount();
  g.drive(30, attackIntent);
  assert.ok(nodeCount() > before, 'attack produced no audio nodes');
});

test('bridge survives a second newGame and a load, without double-playing', async () => {
  const g = await boot();
  g.newGame(1);
  g.newGame(2);
  assert.equal(g.game.saveToSlot('1').ok, true);
  assert.equal(g.game.loadFromSlot('1').ok, true);
  const plays = [];
  const orig = g.RPG.audio.play;
  g.RPG.audio.play = (n, o) => { plays.push(n); return orig(n, o); };
  g.events.emit(g.NAMES.UI_SOUND, { name: 'attack' });
  assert.deepEqual(plays, ['attack']);
});

test('destroy detaches the bridge', async () => {
  const g = await boot();
  g.newGame(1);
  g.game.destroy();
  const before = nodeCount();
  g.events.emit(g.NAMES.UI_SOUND, { name: 'attack' });
  assert.equal(nodeCount(), before);
});

test('muted audio stays silent', async () => {
  const g = await boot();
  g.RPG.audio.mute(true);
  g.newGame(1);
  const before = nodeCount();
  g.drive(30, attackIntent);
  assert.equal(nodeCount(), before);
});

test('game runs with no AudioContext', async () => {
  const g = await makeGame({ withAudio: true });
  g.newGame(1);
  const r = g.drive(60, attackIntent);
  assert.equal(r.stepErrors, 0);
  assert.equal(g.RPG.audio.status().available, false);
});

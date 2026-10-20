'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadBridge } = require('./load.js');
const { FakeAudioContext } = require('./fake-audio-context.js');

const TEAM_MONSTER = 0;
const TEAM_PLAYER = 1;

// Every event that plan section 4 maps to a sound. ITEM_DROPPED is deliberately absent: its row says "none".
const PLAN_EVENTS = ['HIT', 'KILL', 'PLAYER_DIED', 'LEVEL_UP', 'PICKUP', 'SKILL_USED', 'BOSS_WARN', 'BOSS_PHASE',
  'AREA_CHANGED', 'SAVE', 'LOAD', 'UI_SOUND'];

// A recording stand-in for RPG.audio: `state` is what status() reports, `calls` is every play() call.
function fakeAudio(state) {
  const audio = {
    state: Object.assign({ muted: false, failed: false }, state),
    calls: [],
    status() { return Object.assign({}, this.state); },
    play(name, opts) { this.calls.push({ name, opts }); return true; }
  };
  return audio;
}

// Representative payloads, one per plan event, so each row is exercised with the shape the sim really emits.
const SAMPLE = {
  HIT: { targetId: 1, dealt: 5, crit: false, type: 'physical', x: 0, y: 0, team: TEAM_MONSTER },
  KILL: { id: 7, x: 0, y: 0 },
  PLAYER_DIED: {},
  LEVEL_UP: { level: 2 },
  PICKUP: { item: 'potion' },
  SKILL_USED: { skill: 'whirl' },
  BOSS_WARN: {},
  BOSS_PHASE: { phase: 2 },
  AREA_CHANGED: { area: 'forest' },
  SAVE: {},
  LOAD: {},
  UI_SOUND: { name: 'attack' }
};

test('the table has a row for every plan section 4 event and none for ITEM_DROPPED', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const covered = new Set(RPG.audio.bridge.TABLE.map((row) => row.event));
  for (const key of PLAN_EVENTS) assert.ok(covered.has(NAMES[key]), key);
  assert.equal(covered.has(NAMES.ITEM_DROPPED), false, 'ITEM_DROPPED plays nothing');
});

test('every event in the plan 4 table maps to a known sound', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const known = new Set(['attack', 'hit', 'hurt', 'kill', 'skill', 'levelup', 'pickup', 'bossWarn', 'ui']);
  for (const key of PLAN_EVENTS) {
    const bus = RPG.core.events.create();
    const audio = fakeAudio();
    const detach = RPG.audio.bridge.attach(bus, audio);
    bus.emit(NAMES[key], SAMPLE[key]);
    detach();
    assert.equal(audio.calls.length, 1, `${key} plays one sound`);
    assert.ok(known.has(audio.calls[0].name), `${key} -> ${audio.calls[0].name} is a known sound`);
  }
});

test('the fixed sound names used by the table all exist in the audio module', () => {
  const RPG = loadBridge({});
  const known = new Set(['attack', 'hit', 'hurt', 'kill', 'skill', 'levelup', 'pickup', 'bossWarn', 'ui']);
  for (const row of RPG.audio.bridge.TABLE) {
    if (typeof row.sound === 'string') assert.ok(known.has(row.sound), row.sound);
  }
});

test('HIT on a monster plays hit, HIT on the player plays hurt', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio();
  RPG.audio.bridge.attach(bus, audio);

  bus.emit(NAMES.HIT, { team: TEAM_MONSTER, crit: false });
  bus.emit(NAMES.HIT, { team: TEAM_PLAYER, crit: false });
  assert.deepEqual(audio.calls.map((c) => c.name), ['hit', 'hurt']);
});

test('a critical hit on a monster raises the pitch by a tenth; a normal hit keeps pitch 1', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio();
  RPG.audio.bridge.attach(bus, audio);

  bus.emit(NAMES.HIT, { team: TEAM_MONSTER, crit: true });
  bus.emit(NAMES.HIT, { team: TEAM_MONSTER, crit: false });
  assert.equal(audio.calls[0].opts.pitch, 1.1);
  assert.equal(audio.calls[1].opts.pitch, 1);
});

test('player death plays hurt at a lower pitch than a normal player hit', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio();
  RPG.audio.bridge.attach(bus, audio);

  bus.emit(NAMES.PLAYER_DIED, {});
  bus.emit(NAMES.HIT, { team: TEAM_PLAYER, crit: false });
  assert.equal(audio.calls[0].name, 'hurt');
  assert.equal(audio.calls[1].name, 'hurt');
  assert.ok(audio.calls[0].opts.pitch < 1, 'death is below the normal pitch of 1');
  assert.equal(audio.calls[1].opts, undefined, 'a normal player hit plays at pitch 1');
});

test('UI_SOUND plays the named sound and forwards pitch and vol (stage 3 attack lands here)', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio();
  RPG.audio.bridge.attach(bus, audio);

  bus.emit(NAMES.UI_SOUND, { name: 'attack' });
  bus.emit(NAMES.UI_SOUND, { name: 'ui', pitch: 1.5, vol: 0.4 });
  // Compared field by field: the objects come from the bus realm, so deepStrictEqual would fail on prototypes.
  assert.equal(audio.calls[0].name, 'attack');
  assert.equal(audio.calls[0].opts.pitch, undefined);
  assert.equal(audio.calls[0].opts.vol, undefined);
  assert.equal(audio.calls[1].name, 'ui');
  assert.equal(audio.calls[1].opts.pitch, 1.5);
  assert.equal(audio.calls[1].opts.vol, 0.4);
});

test('a payload-less event still plays its sound and does not throw', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio();
  RPG.audio.bridge.attach(bus, audio);

  assert.doesNotThrow(() => bus.emit(NAMES.LEVEL_UP, undefined));
  assert.equal(audio.calls[0].name, 'levelup');
});

test('when muted, no event reaches play()', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio({ muted: true });
  RPG.audio.bridge.attach(bus, audio);

  for (const key of PLAN_EVENTS) bus.emit(NAMES[key], SAMPLE[key]);
  assert.equal(audio.calls.length, 0);
});

test('when the context failed, no event reaches play()', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio({ failed: true });
  RPG.audio.bridge.attach(bus, audio);

  for (const key of PLAN_EVENTS) bus.emit(NAMES[key], SAMPLE[key]);
  assert.equal(audio.calls.length, 0);
});

test('with a real audio module: muted events create no oscillators or buffers', () => {
  FakeAudioContext.instances = 0;
  FakeAudioContext.last = null;
  const RPG = loadBridge({ AudioContext: FakeAudioContext });
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  RPG.audio.bridge.attach(bus, RPG.audio);
  RPG.audio.init();
  RPG.audio.mute(true);
  const created = FakeAudioContext.last.created.length;

  for (const key of PLAN_EVENTS) bus.emit(NAMES[key], SAMPLE[key]);
  assert.equal(FakeAudioContext.last.created.length, created, 'muted bridge schedules nothing');
});

test('with a real audio module: a failed context is a silent no-op that never throws', () => {
  class Blocked {
    constructor() { throw new Error('blocked by policy'); }
  }
  const RPG = loadBridge({ AudioContext: Blocked });
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  RPG.audio.bridge.attach(bus, RPG.audio);
  RPG.audio.init();

  assert.doesNotThrow(() => {
    for (const key of PLAN_EVENTS) bus.emit(NAMES[key], SAMPLE[key]);
  });
  assert.equal(bus.errors, 0, 'no listener threw');
  assert.equal(RPG.audio.status().failed, true);
});

test('with a real audio module and audio not yet initialised, the first event creates the context', () => {
  FakeAudioContext.instances = 0;
  FakeAudioContext.last = null;
  const RPG = loadBridge({ AudioContext: FakeAudioContext });
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  RPG.audio.bridge.attach(bus, RPG.audio);

  bus.emit(NAMES.KILL, SAMPLE.KILL);
  assert.equal(FakeAudioContext.instances, 1);
  assert.ok(FakeAudioContext.last.ofKind('oscillator').length > 0, 'the kill sound was scheduled');
});

test('detach removes every subscription', () => {
  const RPG = loadBridge({});
  const { NAMES } = RPG.core.events;
  const bus = RPG.core.events.create();
  const audio = fakeAudio();
  const detach = RPG.audio.bridge.attach(bus, audio);

  bus.emit(NAMES.KILL, SAMPLE.KILL);
  assert.equal(audio.calls.length, 1);
  detach();
  for (const key of PLAN_EVENTS) bus.emit(NAMES[key], SAMPLE[key]);
  assert.equal(audio.calls.length, 1, 'nothing plays after detach');
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadAudio } = require('./load.js');
const { FakeAudioContext } = require('./fake-audio-context.js');

const SOUND_NAMES = ['attack', 'hit', 'hurt', 'kill', 'skill', 'levelup', 'pickup', 'bossWarn', 'ui'];

// Loads the module with the fake installed as the browser's AudioContext and resets the instance counter.
function withFakeContext(globals) {
  FakeAudioContext.instances = 0;
  FakeAudioContext.last = null;
  return loadAudio(Object.assign({ AudioContext: FakeAudioContext }, globals));
}

test('without AudioContext every call is a silent no-op and never throws', () => {
  const audio = loadAudio({});
  assert.equal(audio.init(), false);
  for (const name of SOUND_NAMES) assert.equal(audio.play(name), false, name);
  assert.equal(audio.play('ui', null), false);
  assert.equal(audio.play('ui', { vol: 'loud', pitch: NaN }), false);
  assert.doesNotThrow(() => {
    audio.setVolume(0.2, 0.3);
    audio.mute(true);
    audio.mute(false);
  });
  const status = audio.status();
  assert.equal(status.available, false);
  assert.equal(status.error, 'AudioContext is not available');
});

test('a constructor that throws leaves audio disabled and silent, with the reason recorded', () => {
  const audio = loadAudio({
    AudioContext: class {
      constructor() { throw new Error('blocked by policy'); }
    }
  });
  assert.doesNotThrow(() => audio.init());
  assert.equal(audio.init(), false);
  assert.equal(audio.play('attack'), false);
  assert.equal(audio.status().available, false);
  assert.equal(audio.status().failed, true);
  assert.equal(audio.status().error, 'blocked by policy');
});

test('a context that was never requested reports neither available nor failed', () => {
  const status = withFakeContext().status();
  assert.equal(status.available, false);
  assert.equal(status.failed, false);
});

test('exposes exactly the nine named sounds', () => {
  const audio = loadAudio({});
  assert.deepEqual([...audio.SOUND_NAMES].sort(), [...SOUND_NAMES].sort());
});

test('each named sound is generated from oscillators or noise bursts, and every source is stopped', () => {
  const audio = withFakeContext();
  assert.equal(audio.init(), true);
  for (const name of SOUND_NAMES) assert.equal(audio.play(name), true, name);

  const ctx = FakeAudioContext.last;
  const oscillators = ctx.ofKind('oscillator');
  const bursts = ctx.ofKind('buffer-source');
  assert.ok(oscillators.length > 0, 'tones are synthesised');
  assert.ok(bursts.length > 0, 'noise bursts are synthesised');
  for (const osc of oscillators) {
    assert.ok(osc.frequency.log.length > 0, 'frequency is scheduled');
    assert.ok(osc.stopped > osc.started, 'oscillator stops after it starts');
  }
  for (const src of bursts) {
    assert.ok(src.stopped > src.started, 'noise burst stops after it starts');
    assert.equal(src.buffer.sampleRate, ctx.sampleRate);
  }
});

test('hurt is procedural: a falling tone plus a noise crunch, with no file or buffer source of its own', () => {
  const audio = withFakeContext();
  audio.init();
  assert.equal(audio.play('hurt'), true);
  const ctx = FakeAudioContext.last;
  const tone = ctx.ofKind('oscillator').pop();
  const crunch = ctx.ofKind('buffer-source').pop();
  const start = tone.frequency.log[0][1];
  const end = tone.frequency.log.find(([kind]) => kind === 'exp')[1];
  assert.ok(end < start, 'the tone falls');
  assert.ok(crunch.stopped > crunch.started, 'the noise crunch is scheduled');
});

test('context is created lazily, once, on init() or the first play()', () => {
  const explicit = withFakeContext();
  assert.equal(FakeAudioContext.instances, 0, 'nothing created at load');
  assert.equal(explicit.init(), true);
  assert.equal(FakeAudioContext.instances, 1);
  assert.equal(explicit.init(), true);
  assert.equal(explicit.play('ui'), true);
  assert.equal(FakeAudioContext.instances, 1, 'init and play share one context');

  const lazy = withFakeContext();
  assert.equal(FakeAudioContext.instances, 0);
  assert.equal(lazy.play('pickup'), true, 'play creates the context when needed');
  assert.equal(FakeAudioContext.instances, 1);
});

test('a suspended context is resumed when audio is initialised', () => {
  class Suspended extends FakeAudioContext {
    constructor() { super(); this.state = 'suspended'; }
  }
  const audio = withFakeContext({ AudioContext: Suspended });
  audio.init();
  assert.equal(FakeAudioContext.last.resumeCalls, 1);
  assert.equal(FakeAudioContext.last.state, 'running');
});

test('mute stops new sounds without creating nodes, and unmute restores them', () => {
  const audio = withFakeContext();
  audio.init();
  const ctx = FakeAudioContext.last;
  const masterBus = ctx.created[0];
  const created = ctx.created.length;

  audio.mute(true);
  assert.equal(masterBus.gain.value, 0);
  assert.equal(audio.play('hit'), false);
  assert.equal(ctx.created.length, created, 'muted play schedules nothing');
  assert.equal(audio.status().muted, true);

  audio.mute(false);
  assert.equal(masterBus.gain.value, 0.5);
  assert.equal(audio.play('hit'), true);
});

test('master and sfx volume set the bus gains and are clamped to 0..1', () => {
  const audio = withFakeContext();
  audio.init();
  const [masterBus, sfxBus] = FakeAudioContext.last.created;
  assert.equal(masterBus.gain.value, 0.5, 'default master');
  assert.equal(sfxBus.gain.value, 0.8, 'default sfx');

  audio.setVolume(0.25, 0.4);
  assert.equal(masterBus.gain.value, 0.25);
  assert.equal(sfxBus.gain.value, 0.4);

  audio.setVolume(undefined, 0.6);
  assert.equal(masterBus.gain.value, 0.25, 'omitted master keeps its value');
  assert.equal(sfxBus.gain.value, 0.6);

  audio.setVolume(3, -1);
  assert.equal(masterBus.gain.value, 1);
  assert.equal(sfxBus.gain.value, 0);
  assert.equal(audio.play('attack'), false, 'silent sfx volume schedules nothing');
  assert.deepEqual({ master: audio.status().master, sfx: audio.status().sfx }, { master: 1, sfx: 0 });
});

test('pitch scales frequencies and vol scales the envelope peak, both clamped', () => {
  const audio = withFakeContext();
  audio.init();
  const ctx = FakeAudioContext.last;

  assert.equal(audio.play('ui', { pitch: 2, vol: 0.5 }), true);
  const osc = ctx.ofKind('oscillator').pop();
  assert.equal(osc.frequency.log[0][1], 2800, 'pitch 2 doubles 1400 Hz');
  const env = osc.connections[0];
  const peakScheduled = env.gain.log.some(([kind, v]) => kind === 'exp' && Math.abs(v - 0.075) < 1e-9);
  assert.ok(peakScheduled, 'peak is 0.15 * 0.5');

  audio.play('ui', { pitch: 100 });
  assert.equal(ctx.ofKind('oscillator').pop().frequency.log[0][1], 1400 * 4, 'pitch clamps to 4');
});

test('an unknown name is ignored and a non-numeric vol falls back to full volume', () => {
  const audio = withFakeContext();
  audio.init();
  assert.equal(audio.play('nope'), false, 'not one of the nine');
  assert.equal(audio.play('ui', { vol: NaN }), true, 'non-numeric vol means 1');
  assert.equal(audio.play('ui', { vol: 0 }), false, 'zero vol schedules nothing');
});

test('a failing node creation during play is recorded and does not throw', () => {
  class Broken extends FakeAudioContext {
    createOscillator() { throw new Error('boom'); }
  }
  const audio = withFakeContext({ AudioContext: Broken });
  audio.init();
  assert.doesNotThrow(() => audio.play('attack'));
  assert.equal(audio.play('kill'), false);
  assert.equal(audio.status().error, 'boom');
});

test('the noise buffer is identical across contexts (fixed seed)', () => {
  const burstSamples = () => {
    const audio = withFakeContext();
    audio.play('hit');
    const burst = FakeAudioContext.last.ofKind('buffer-source')[0];
    return Array.from(burst.buffer.getChannelData(0));
  };
  const first = burstSamples();
  assert.deepEqual(burstSamples(), first);
  assert.ok(first.every((s) => s >= -1 && s <= 1), 'samples stay in range');
});

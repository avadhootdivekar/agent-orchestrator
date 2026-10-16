/**
 * Audio: procedural Web Audio sound effects. No audio files: every sound is oscillators and noise bursts shaped by
 * short envelopes. Without an AudioContext (no Web Audio, or construction failed) every call is a silent no-op that
 * never throws; the reason is kept in status().error. The context is created lazily: call init() from a user gesture
 * (autoplay policy), or let the first play() create it.
 */
(function (RPG, root) {
  'use strict';

  RPG.audio = RPG.audio || {};

  const DEFAULT_MASTER = 0.5;
  const DEFAULT_SFX = 0.8;
  // Exponential ramps cannot reach zero, so envelopes fall to this floor instead.
  const SILENCE = 0.0001;
  // Oscillators and noise sources are stopped this long after their envelope ends, so the tail is never cut.
  const STOP_PAD = 0.02;
  const NOISE_SECONDS = 1;
  // Fixed seed: the noise buffer is identical on every run.
  const NOISE_SEED = 0x2f6e2b1;
  const PITCH_MIN = 0.25;
  const PITCH_MAX = 4;

  // Each sound is a list of voices started at `at` seconds. A voice is either an oscillator tone (`type`, `f0`, optional
  // `f1` to sweep to) or a noise burst (`noise: true`, `cutoff`, optional `filter`). `peak` is the envelope peak gain.
  const SOUNDS = {
    attack: [
      { noise: true, at: 0, dur: 0.07, filter: 'highpass', cutoff: 2000, peak: 0.5 },
      { type: 'square', at: 0, dur: 0.06, f0: 520, f1: 360, peak: 0.2 }
    ],
    hit: [
      { noise: true, at: 0, dur: 0.08, filter: 'lowpass', cutoff: 900, peak: 0.6 },
      { type: 'triangle', at: 0, dur: 0.12, f0: 190, f1: 90, peak: 0.45 }
    ],
    kill: [
      { type: 'sawtooth', at: 0, dur: 0.4, f0: 280, f1: 55, peak: 0.3 },
      { noise: true, at: 0.05, dur: 0.25, filter: 'lowpass', cutoff: 600, peak: 0.35 }
    ],
    skill: [
      { type: 'sine', at: 0, dur: 0.07, f0: 440, peak: 0.3 },
      { type: 'sine', at: 0.05, dur: 0.07, f0: 660, peak: 0.3 },
      { type: 'sine', at: 0.1, dur: 0.12, f0: 880, peak: 0.3 }
    ],
    levelup: [
      { type: 'triangle', at: 0, dur: 0.12, f0: 523, peak: 0.3 },
      { type: 'triangle', at: 0.1, dur: 0.12, f0: 659, peak: 0.3 },
      { type: 'triangle', at: 0.2, dur: 0.12, f0: 784, peak: 0.3 },
      { type: 'triangle', at: 0.3, dur: 0.3, f0: 1046, peak: 0.3 }
    ],
    pickup: [
      { type: 'sine', at: 0, dur: 0.12, f0: 900, f1: 1500, peak: 0.3 }
    ],
    // Player damage: a dull crunch over a falling tone. Used for player-hit and player-death (lower pitch).
    hurt: [
      { noise: true, at: 0, dur: 0.1, filter: 'lowpass', cutoff: 700, peak: 0.5 },
      { type: 'sawtooth', at: 0, dur: 0.2, f0: 220, f1: 80, peak: 0.25 }
    ],
    // Two close square tones beat against each other, which reads as an ominous warning.
    bossWarn: [
      { type: 'square', at: 0, dur: 0.9, f0: 90, peak: 0.18 },
      { type: 'square', at: 0, dur: 0.9, f0: 96, peak: 0.18 }
    ],
    ui: [
      { type: 'square', at: 0, dur: 0.03, f0: 1400, peak: 0.15 }
    ]
  };

  const state = {
    ctx: null,
    masterBus: null,
    sfxBus: null,
    noise: null,
    failed: false,
    error: null,
    master: DEFAULT_MASTER,
    sfx: DEFAULT_SFX,
    muted: false
  };

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  function finite(v, fallback) { return typeof v === 'number' && isFinite(v) ? v : fallback; }

  function fail(err) {
    state.failed = true;
    state.error = err && err.message ? err.message : String(err);
    return null;
  }

  /** Creates the context and bus chain once. Returns null (and records why) when Web Audio is unavailable. */
  function ensureContext(ContextCtor) {
    if (state.ctx || state.failed) return state.ctx;
    const Ctor = ContextCtor || root.AudioContext || root.webkitAudioContext;
    if (typeof Ctor !== 'function') return fail(new Error('AudioContext is not available'));
    try {
      const ctx = new Ctor();
      const masterBus = ctx.createGain();
      masterBus.connect(ctx.destination);
      const sfxBus = ctx.createGain();
      sfxBus.connect(masterBus);
      state.ctx = ctx;
      state.masterBus = masterBus;
      state.sfxBus = sfxBus;
      applyVolume();
    } catch (err) {
      return fail(err);
    }
    // Browsers start contexts suspended until a gesture; resume() is a harmless no-op if none has happened yet.
    if (state.ctx.state === 'suspended' && typeof state.ctx.resume === 'function') {
      const pending = state.ctx.resume();
      if (pending && typeof pending.catch === 'function') pending.catch(function (err) { state.error = String(err); });
    }
    return state.ctx;
  }

  function applyVolume() {
    if (!state.ctx) return;
    state.masterBus.gain.value = state.muted ? 0 : state.master;
    state.sfxBus.gain.value = state.sfx;
  }

  /** Shared white-noise buffer, generated once per context from a fixed seed. */
  function noiseBuffer(ctx) {
    if (state.noise) return state.noise;
    const length = Math.floor(ctx.sampleRate * NOISE_SECONDS);
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    let seed = NOISE_SEED;
    for (let i = 0; i < length; i++) {
      seed = (Math.imul(seed, 1103515245) + 12345) & 0x7fffffff;
      data[i] = seed / 0x3fffffff - 1;
    }
    state.noise = buffer;
    return buffer;
  }

  function envelope(ctx, t, dur, peak) {
    const env = ctx.createGain();
    env.gain.setValueAtTime(SILENCE, t);
    env.gain.exponentialRampToValueAtTime(peak, t + Math.min(0.01, dur / 4));
    env.gain.exponentialRampToValueAtTime(SILENCE, t + dur);
    return env;
  }

  function startTone(ctx, dest, t, voice, pitch, vol) {
    const osc = ctx.createOscillator();
    osc.type = voice.type;
    osc.frequency.setValueAtTime(voice.f0 * pitch, t);
    if (voice.f1) osc.frequency.exponentialRampToValueAtTime(voice.f1 * pitch, t + voice.dur);
    const env = envelope(ctx, t, voice.dur, voice.peak * vol);
    osc.connect(env);
    env.connect(dest);
    osc.start(t);
    osc.stop(t + voice.dur + STOP_PAD);
  }

  function startNoise(ctx, dest, t, voice, pitch, vol) {
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer(ctx);
    const filter = ctx.createBiquadFilter();
    filter.type = voice.filter;
    filter.frequency.value = voice.cutoff * pitch;
    const env = envelope(ctx, t, voice.dur, voice.peak * vol);
    src.connect(filter);
    filter.connect(env);
    env.connect(dest);
    src.start(t);
    src.stop(t + voice.dur + STOP_PAD);
  }

  /** Initialises the audio context. Call from a user gesture. Returns true when audio is available. */
  function init(ContextCtor) {
    return ensureContext(ContextCtor) !== null;
  }

  /**
   * Plays one named sound. `opts.pitch` scales frequencies (clamped to 0.25..4); `opts.vol` scales this sound (0..1).
   * Returns true if sound was scheduled, false if it was skipped (unknown name, muted, silent volume or no audio).
   */
  function play(name, opts) {
    if (!Object.prototype.hasOwnProperty.call(SOUNDS, name)) return false;
    if (state.muted || state.master * state.sfx === 0) return false;
    const o = opts || {};
    const vol = clamp(finite(o.vol, 1), 0, 1);
    if (vol === 0) return false;
    const pitch = clamp(finite(o.pitch, 1), PITCH_MIN, PITCH_MAX);
    const ctx = ensureContext();
    if (!ctx) return false;
    try {
      const t = ctx.currentTime;
      for (const voice of SOUNDS[name]) {
        const start = t + voice.at;
        if (voice.noise) startNoise(ctx, state.sfxBus, start, voice, pitch, vol);
        else startTone(ctx, state.sfxBus, start, voice, pitch, vol);
      }
      return true;
    } catch (err) {
      fail(err);
      return false;
    }
  }

  /** Sets master and sfx volume (each 0..1). Omitted or non-numeric values keep their current setting. */
  function setVolume(master, sfx) {
    state.master = clamp(finite(master, state.master), 0, 1);
    state.sfx = clamp(finite(sfx, state.sfx), 0, 1);
    applyVolume();
  }

  function mute(on) {
    state.muted = !!on;
    applyVolume();
  }

  /** Read-only snapshot for the settings screen and for diagnostics. */
  function status() {
    return {
      available: state.ctx !== null,
      failed: state.failed,
      muted: state.muted,
      master: state.master,
      sfx: state.sfx,
      error: state.error
    };
  }

  RPG.audio = {
    SOUND_NAMES: Object.keys(SOUNDS),
    init: init,
    play: play,
    setVolume: setVolume,
    mute: mute,
    status: status
  };
})(globalThis.RPG, globalThis);

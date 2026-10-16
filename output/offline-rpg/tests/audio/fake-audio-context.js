'use strict';

// Minimal stand-ins for the Web Audio graph. They record what the module schedules and nothing else.
class Param {
  constructor(value) {
    this.value = value;
    this.log = [];
  }
  setValueAtTime(v, t) { this.log.push(['set', v, t]); return this; }
  exponentialRampToValueAtTime(v, t) { this.log.push(['exp', v, t]); return this; }
  linearRampToValueAtTime(v, t) { this.log.push(['lin', v, t]); return this; }
}

class Node {
  constructor(kind) {
    this.kind = kind;
    this.connections = [];
  }
  connect(dest) { this.connections.push(dest); return dest; }
}

class GainNode extends Node {
  constructor() { super('gain'); this.gain = new Param(1); }
}

class OscillatorNode extends Node {
  constructor() {
    super('oscillator');
    this.type = 'sine';
    this.frequency = new Param(0);
    this.started = null;
    this.stopped = null;
  }
  start(t) { this.started = t; }
  stop(t) { this.stopped = t; }
}

class BiquadFilterNode extends Node {
  constructor() { super('biquad'); this.type = 'lowpass'; this.frequency = new Param(0); }
}

class BufferSourceNode extends Node {
  constructor() { super('buffer-source'); this.buffer = null; this.started = null; this.stopped = null; }
  start(t) { this.started = t; }
  stop(t) { this.stopped = t; }
}

class FakeAudioContext {
  constructor() {
    FakeAudioContext.instances++;
    FakeAudioContext.last = this;
    this.created = [];
    this.currentTime = 2;
    this.sampleRate = 1000;
    this.state = 'running';
    this.destination = new Node('destination');
    this.resumeCalls = 0;
  }
  make(node) { this.created.push(node); return node; }
  createGain() { return this.make(new GainNode()); }
  createOscillator() { return this.make(new OscillatorNode()); }
  createBiquadFilter() { return this.make(new BiquadFilterNode()); }
  createBufferSource() { return this.make(new BufferSourceNode()); }
  createBuffer(channels, length, rate) {
    const data = new Float32Array(length);
    return { sampleRate: rate, getChannelData: () => data };
  }
  resume() { this.resumeCalls++; this.state = 'running'; return Promise.resolve(); }
  ofKind(kind) { return this.created.filter((n) => n.kind === kind); }
}

FakeAudioContext.instances = 0;
FakeAudioContext.last = null;

module.exports = { FakeAudioContext };

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadCore } = require('./helpers');

const RPG = loadCore(['namespace', 'util', 'loop']);
const { createLoop } = RPG.core;

const DT = 1 / 60;

/** Fake clock the test advances by hand, plus a scheduler that records the callback instead of using rAF. */
function harness(extra = {}) {
  const clock = { t: 0, now() { return this.t; } };
  const sched = {
    pending: null,
    request(cb) { this.pending = cb; return 1; },
    cancel() { this.pending = null; }
  };
  const log = { steps: [], renders: [] };
  const loop = createLoop({
    dt: DT,
    clock,
    scheduler: sched,
    step: (dt) => log.steps.push(dt),
    render: (alpha) => log.renders.push(alpha),
    ...extra
  });
  return { loop, clock, sched, log };
}

test('a frame shorter than one timestep runs no step and renders with a fractional alpha', () => {
  const { loop, clock, log } = harness();
  loop.start();
  loop.frame(); // t=0, primes `last`
  clock.t = DT / 2;
  loop.frame();
  assert.equal(log.steps.length, 0);
  assert.ok(Math.abs(log.renders.at(-1) - 0.5) < 1e-9);
});

test('steps always advance by the fixed dt, never by the real frame time', () => {
  const { loop, clock, log } = harness();
  loop.start();
  loop.frame();
  clock.t = 0.05; // 50 ms of real time -> whole steps of exactly DT
  loop.frame();
  assert.ok(log.steps.length >= 2);
  for (const dt of log.steps) assert.equal(dt, DT);
});

test('catch-up: a frame covering N timesteps runs N fixed steps and leaves the remainder as alpha', () => {
  const { loop, clock, log } = harness({ maxSteps: 10 });
  loop.start();
  loop.frame();
  clock.t = 3.5 * DT; // three full steps plus half a step left over
  loop.frame();
  assert.equal(log.steps.length, 3);
  assert.equal(loop.stepCount, 3);
  assert.ok(Math.abs(log.renders.at(-1) - 0.5) < 1e-6);
});

test('catch-up: maxSteps clamps the steps in one frame and the backlog is dropped, not replayed', () => {
  const { loop, clock, log } = harness({ maxSteps: 5 });
  loop.start();
  loop.frame();
  clock.t = 0.12; // ~7.2 steps owed, only 5 allowed; leftover is well over one step, so it is dropped
  loop.frame();
  assert.equal(log.steps.length, 5);
  // Backlog dropped: the next frame of one timestep yields exactly one step, not a burst.
  clock.t = 0.12 + DT;
  loop.frame();
  assert.equal(log.steps.length, 6);
});

test('max-frame clamp: a huge gap (e.g. tab restored) advances at most MAX_FRAME of simulated time', () => {
  const { loop, clock, log } = harness({ maxSteps: 1000 });
  loop.start();
  loop.frame();
  clock.t = 10; // ten seconds of wall time in one frame
  loop.frame();
  // MAX_FRAME is 0.25 s; 0.25 s at 1/60 is 15 steps.
  assert.equal(log.steps.length, 15);
});

test('a clock that goes backwards never produces negative time or steps', () => {
  const { loop, clock, log } = harness();
  loop.start();
  clock.t = 1;
  loop.frame();
  clock.t = 0.5;
  loop.frame();
  assert.equal(log.steps.length, 0);
  assert.ok(log.renders.at(-1) >= 0 && log.renders.at(-1) < 1);
});

test('shouldStep=false consumes time without counting or running a step', () => {
  let allow = false;
  const { loop, clock, log } = harness({ shouldStep: () => allow });
  loop.start();
  loop.frame();
  clock.t = 2 * DT;
  loop.frame();
  assert.equal(log.steps.length, 0);
  assert.equal(loop.stepCount, 0);
  allow = true;
  clock.t = 3 * DT;
  loop.frame();
  assert.equal(log.steps.length, 1); // the time banked during the frame just before, not the two consumed earlier
});

test('reset forgets accumulated time so no catch-up burst happens after a pause', () => {
  const { loop, clock, log } = harness();
  loop.start();
  loop.frame();
  clock.t = 0.9 * DT; // almost one step banked
  loop.frame();
  loop.reset();
  clock.t = 0.9 * DT + 0.9 * DT; // another sub-step gap, measured from the reset point
  loop.frame();
  assert.equal(log.steps.length, 0, 'without reset the banked time would have produced a step');
});

test('start() drives frames through the scheduler; stop() cancels and halts further steps', () => {
  const { loop, clock, sched, log } = harness();
  loop.start();
  assert.equal(loop.running, true);
  loop.frame(); // prime
  clock.t = DT;
  sched.pending(); // scheduler fires the tick
  assert.equal(log.steps.length, 1);
  loop.stop();
  assert.equal(loop.running, false);
  assert.equal(sched.pending, null);
  clock.t = 5 * DT;
  // With the scheduler cancelled nothing ticks on its own, so no further steps run.
  assert.equal(log.steps.length, 1);
});

test('start() twice does not double-schedule', () => {
  let requests = 0;
  const { loop } = harness({ scheduler: { request() { requests++; return 1; }, cancel() {} } });
  loop.start();
  loop.start();
  assert.equal(requests, 1);
});

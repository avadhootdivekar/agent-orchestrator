/* Fixed-timestep loop driver (ARCHITECTURE §3). Clock and scheduler are injected so it runs in node.
   clock:  { now(): seconds }  scheduler: { request(cb): handle, cancel(handle) }
   Defaults use performance.now / requestAnimationFrame lazily, only when start() is called without them. */
(function (RPG) {
  'use strict';

  var MAX_FRAME = 0.25;

  function defaults() {
    var g = globalThis;
    return {
      clock: { now: function () { return g.performance.now() / 1000; } },
      scheduler: {
        request: function (cb) { return g.requestAnimationFrame(function () { cb(); }); },
        cancel: function (h) { g.cancelAnimationFrame(h); }
      }
    };
  }

  /**
   * createLoop({ step(dt), render(alpha), dt=1/60, maxSteps=5, clock?, scheduler?, shouldStep? })
   * -> { start(), stop(), frame(), reset(), running, stepCount }
   * frame() runs one iteration using clock.now() (also called by the scheduler); tests call it directly.
   */
  function createLoop(cfg) {
    var dt = cfg.dt || 1 / 60;
    var maxSteps = cfg.maxSteps || 5;
    var clock = cfg.clock, scheduler = cfg.scheduler;
    var acc = 0, last = null, handle = null;
    var api = { running: false, stepCount: 0 };

    function frame() {
      var now = clock.now();
      if (last === null) last = now;
      acc += Math.min(Math.max(now - last, 0), MAX_FRAME);
      last = now;
      var n = 0;
      // epsilon guards float drift (e.g. 60 * (1/60) accumulating just below dt)
      while (acc >= dt - 1e-9 && n < maxSteps) {
        if (!cfg.shouldStep || cfg.shouldStep()) { cfg.step(dt); api.stepCount++; }
        acc -= dt; n++;
      }
      if (n >= maxSteps && acc >= dt) acc = 0; // spiral-of-death guard: drop the backlog
      if (acc < 0) acc = 0;
      cfg.render(Math.min(acc / dt, 0.999999));
    }

    function tick() {
      if (!api.running) return;
      frame();
      if (api.running) handle = scheduler.request(tick);
    }

    api.frame = frame;
    /** Forget accumulated time (call after tab hidden / unpause so no catch-up burst happens). */
    api.reset = function () { acc = 0; last = null; };
    api.start = function () {
      if (api.running) return;
      if (!clock || !scheduler) {
        var d = defaults();
        clock = clock || d.clock; scheduler = scheduler || d.scheduler;
      }
      api.running = true;
      api.reset();
      handle = scheduler.request(tick);
    };
    api.stop = function () {
      api.running = false;
      if (handle !== null && scheduler) scheduler.cancel(handle);
      handle = null;
    };
    return api;
  }

  RPG.core.createLoop = createLoop;
})(globalThis.RPG);

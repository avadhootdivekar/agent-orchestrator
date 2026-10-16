/* Entry point: builds the world, wires input -> sim step -> renderer into the fixed-timestep loop
   (docs/ARCHITECTURE.md §1, §3, §4). A fuller RPG.core.Game replaces this later. */
(function (RPG) {
  'use strict';

  var MAX_FRAME_DT = 0.1;

  function start() {
    var canvas = document.getElementById('game');
    var renderer = RPG.render.create(canvas);
    var input = RPG.input.create();
    var world = RPG.sim.createWorld({ seed: 1 });
    var lastFrame = null;

    function resize() {
      renderer.resize(canvas.clientWidth, canvas.clientHeight, window.devicePixelRatio || 1);
    }

    resize();
    renderer.snapTo(world);
    input.attach(window, canvas);
    window.addEventListener('resize', resize);

    var loop = RPG.core.createLoop({
      step: function (dt) {
        var intent = input.buildFrame(renderer.camera, renderer.viewW, renderer.viewH);
        RPG.sim.step(world, intent, dt);
      },
      render: function (alpha) {
        var now = performance.now() / 1000;
        var dt = lastFrame === null ? 0 : Math.min(now - lastFrame, MAX_FRAME_DT);
        lastFrame = now;
        renderer.draw(world, alpha, dt);
      }
    });
    // A hidden tab stops rAF; forget the gap so it does not become a catch-up burst on return.
    document.addEventListener('visibilitychange', function () { loop.reset(); lastFrame = null; });
    loop.start();

    RPG.main.world = world;
    RPG.main.loop = loop;
    RPG.main.renderer = renderer;
  }

  RPG.main = { start: start };
  // Classic script at the end of <body>: the canvas already exists here.
  start();
})(window.RPG = window.RPG || {});

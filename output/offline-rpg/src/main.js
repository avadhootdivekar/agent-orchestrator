/* Placeholder entry point: paints the background colour on a window-sized canvas.
 * Replaced by RPG.core.Game.start() in a later unit (docs/ARCHITECTURE.md §1, §4). */
(function (RPG) {
  'use strict';

  // Background colour token, matching the muted palette in docs/ARCHITECTURE.md §4 (render/palette).
  var BACKGROUND = '#14161c';

  function paint() {
    var canvas = document.getElementById('game');
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(canvas.clientWidth * dpr);
    canvas.height = Math.round(canvas.clientHeight * dpr);
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = BACKGROUND;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  window.addEventListener('resize', paint);
  // Classic script at the end of <body>: the canvas already exists here.
  paint();

  RPG.main = { paint: paint };
})(window.RPG = window.RPG || {});

/* Entry point (docs/INTEGRATION-PLAN.md §3, §5): the single place that touches the page. Game.start builds the
   renderer, input (keyboard, mouse, wheel), screen manager, menus and the fixed-timestep loop on the page canvas;
   the browser pieces it must not look up itself (settings storage) are passed in here. Runs once, as the last script
   of the single built page. */
(function (RPG) {
  'use strict';

  var CANVAS_ID = 'game';

  // localStorage can throw on access (blocked storage, file:// in some browsers); settings then stay in memory.
  function settingsStorage() {
    try { return globalThis.localStorage || undefined; } catch (e) { return undefined; }
  }

  function start() {
    var canvas = document.getElementById(CANVAS_ID);
    RPG.main.game = RPG.core.game.start(canvas, { settingsStorage: settingsStorage() });
    return RPG.main.game;
  }

  RPG.main = { start: start };
  // Classic script at the end of <body>: the canvas already exists here.
  start();
})(globalThis.RPG = globalThis.RPG || {});

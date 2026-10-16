/*
 * Raw input state: what the DOM layer recorded, with no interpretation. Pure in-memory data, no DOM.
 * `held` is level state per code (keys and 'MouseN'). `pressed` holds the edges seen since the last clearPressed().
 * Auto-repeat of an already-held code creates no press edge, so holding a skill key never retriggers it.
 */
(function (RPG) {
  'use strict';
  var input = (RPG.input = RPG.input || {});

  function createRawState(bindings) {
    return {
      bindings: bindings,
      held: Object.create(null),
      pressed: Object.create(null),
      // Canvas CSS px. null until the pointer has been seen over the canvas.
      mouse: { x: null, y: null }
    };
  }

  function setCode(raw, code, isDown) {
    if (isDown) {
      if (!raw.held[code]) {
        raw.pressed[code] = true;
      }
      raw.held[code] = true;
    } else {
      delete raw.held[code];
    }
  }

  function setPointer(raw, x, y) {
    raw.mouse.x = x;
    raw.mouse.y = y;
  }

  // Window blur: the browser will not deliver keyups for keys released while unfocused, so drop held state.
  function clearHeld(raw) {
    raw.held = Object.create(null);
  }

  function clearPressed(raw) {
    raw.pressed = Object.create(null);
  }

  input.raw = {
    createRawState: createRawState,
    setCode: setCode,
    setPointer: setPointer,
    clearHeld: clearHeld,
    clearPressed: clearPressed
  };
})(globalThis.RPG = globalThis.RPG || {});

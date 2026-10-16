/*
 * Pure mapping from raw input state to an intent for one frame. No DOM, no mutation of its arguments.
 * Camera is { x, y, zoom, viewW, viewH }: (x, y) is the world point at the viewport centre, in CSS px.
 * screenToWorld must agree with render/camera.js worldToScreen, which is not written yet.
 */
(function (RPG) {
  'use strict';
  var input = (RPG.input = RPG.input || {});

  // Index i of the frame's skills[] is slot i+1: keys 1-5, then Q (6) and E (7).
  var SKILL_SLOTS = Object.freeze(['skill1', 'skill2', 'skill3', 'skill4', 'skill5', 'skill6', 'skill7']);

  // Toggle name in the frame -> action name in the key map.
  var TOGGLES = Object.freeze({
    inventory: 'toggleInventory',
    character: 'toggleCharacter',
    skills: 'toggleSkills',
    settings: 'toggleSettings',
    pause: 'pause',
    saveMenu: 'saveMenu',
    debug: 'debug',
    quickSave: 'quickSave',
    quickLoad: 'quickLoad'
  });

  function anyHeld(raw, codes) {
    for (var i = 0; i < codes.length; i++) {
      if (raw.held[codes[i]]) return true;
    }
    return false;
  }

  function anyPressed(raw, codes) {
    for (var i = 0; i < codes.length; i++) {
      if (raw.pressed[codes[i]]) return true;
    }
    return false;
  }

  function screenToWorld(camera, sx, sy) {
    return {
      x: camera.x + (sx - camera.viewW / 2) / camera.zoom,
      y: camera.y + (sy - camera.viewH / 2) / camera.zoom
    };
  }

  // Edge-triggered: skills, dash and toggles. Level-triggered: move and attack (hold to repeat).
  function sampleIntent(raw, camera) {
    var b = raw.bindings;

    var dx = (anyHeld(raw, b.moveRight) ? 1 : 0) - (anyHeld(raw, b.moveLeft) ? 1 : 0);
    var dy = (anyHeld(raw, b.moveDown) ? 1 : 0) - (anyHeld(raw, b.moveUp) ? 1 : 0);
    // Scale diagonals to unit length so they are not faster than cardinal movement.
    var len = Math.sqrt(dx * dx + dy * dy);
    if (len > 0) {
      dx /= len;
      dy /= len;
    }

    var aim = raw.mouse.x === null
      ? { x: camera.x, y: camera.y }
      : screenToWorld(camera, raw.mouse.x, raw.mouse.y);

    var skills = SKILL_SLOTS.map(function (action) {
      return anyPressed(raw, b[action]);
    });

    var toggles = {};
    Object.keys(TOGGLES).forEach(function (name) {
      toggles[name] = anyPressed(raw, b[TOGGLES[name]]);
    });

    // Next (+1) and previous (-1) presses plus wheel notches (down = next), clamped to one step per frame.
    var weaponStep = (anyPressed(raw, b.switchWeapon) ? 1 : 0)
      - (anyPressed(raw, b.switchWeaponPrev) ? 1 : 0)
      + raw.wheelSteps;
    weaponStep = Math.max(-1, Math.min(1, weaponStep));

    return {
      move: { x: dx, y: dy },
      aim: aim,
      attack: anyHeld(raw, b.attack),
      skills: skills,
      dash: anyPressed(raw, b.dash),
      switchWeapon: weaponStep,
      interact: anyPressed(raw, b.interact),
      toggles: toggles
    };
  }

  input.intent = {
    sampleIntent: sampleIntent,
    screenToWorld: screenToWorld,
    SKILL_SLOTS: SKILL_SLOTS,
    TOGGLES: TOGGLES
  };
})(globalThis.RPG = globalThis.RPG || {});

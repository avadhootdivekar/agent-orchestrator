/*
 * Input actions -> physical codes. Pure data and pure helpers, no DOM.
 * Codes are KeyboardEvent.code values, 'Mouse0'..'Mouse2' for mouse buttons, or 'Shift+Tab' (Tab with Shift held,
 * synthesized by input.js since Shift is not a code of its own).
 * Defaults follow ARCHITECTURE.md §9: S is move-down, so the Skills screen is K.
 */
(function (RPG) {
  'use strict';
  var input = (RPG.input = RPG.input || {});

  var ACTIONS = Object.freeze([
    'moveUp', 'moveDown', 'moveLeft', 'moveRight',
    'attack',
    'skill1', 'skill2', 'skill3', 'skill4', 'skill5', 'skill6', 'skill7',
    'dash',
    'switchWeapon', 'switchWeaponPrev', 'interact',
    'toggleInventory', 'toggleCharacter', 'toggleSkills', 'toggleSettings',
    'pause', 'saveMenu', 'quickSave', 'quickLoad', 'debug'
  ]);

  var DEFAULTS = {
    moveUp: ['KeyW', 'ArrowUp'],
    moveDown: ['KeyS', 'ArrowDown'],
    moveLeft: ['KeyA', 'ArrowLeft'],
    moveRight: ['KeyD', 'ArrowRight'],
    attack: ['Mouse0', 'KeyJ'],
    skill1: ['Digit1'],
    skill2: ['Digit2'],
    skill3: ['Digit3'],
    skill4: ['Digit4'],
    skill5: ['Digit5'],
    skill6: ['KeyQ'],
    skill7: ['KeyE'],
    dash: ['Space'],
    switchWeapon: ['Tab'],
    switchWeaponPrev: ['Shift+Tab'],
    interact: ['KeyF'],
    toggleInventory: ['KeyI'],
    toggleCharacter: ['KeyC'],
    toggleSkills: ['KeyK'],
    toggleSettings: ['KeyO'],
    pause: ['Escape', 'KeyP'],
    saveMenu: ['KeyM'],
    quickSave: ['F5'],
    quickLoad: ['F9'],
    debug: ['F3']
  };

  function isAction(name) {
    return Object.prototype.hasOwnProperty.call(DEFAULTS, name);
  }

  // Bindings are plain { action: code[] } maps. Copies are deep enough that callers can't mutate ours.
  function copyBindings(bindings) {
    var out = {};
    ACTIONS.forEach(function (action) {
      out[action] = bindings[action].slice();
    });
    return out;
  }

  function defaultBindings() {
    return copyBindings(DEFAULTS);
  }

  // Returns new bindings where `action` is bound to exactly `code`. A key does one thing, so `code` is removed from
  // every other action. An action left with no keys is simply unbound; pressing nothing triggers it.
  function withBinding(bindings, action, code) {
    if (!isAction(action)) {
      throw new Error('unknown input action ' + action);
    }
    if (typeof code !== 'string' || code === '') {
      throw new Error('invalid key code for ' + action);
    }
    var next = copyBindings(bindings);
    ACTIONS.forEach(function (other) {
      if (other !== action) {
        next[other] = next[other].filter(function (c) { return c !== code; });
      }
    });
    next[action] = [code];
    return next;
  }

  input.keymap = {
    ACTIONS: ACTIONS,
    isAction: isAction,
    defaultBindings: defaultBindings,
    copyBindings: copyBindings,
    withBinding: withBinding
  };
})(globalThis.RPG = globalThis.RPG || {});

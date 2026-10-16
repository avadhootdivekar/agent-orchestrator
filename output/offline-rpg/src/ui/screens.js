/*
 * Screen manager (ARCHITECTURE §9 key bindings). A stack of screens drawn on the game canvas; no DOM.
 * Base screens ('mainmenu', 'playing') are not modal. Overlays ('pause', 'inventory', 'character', 'skills',
 * 'settings', 'saveload') are modal: while any is on the stack the simulation is paused.
 * Later units plug in by calling register({ id, update, draw, onKey }) for one of SCREEN_IDS. Headless: no window or
 * document access at load time.
 */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var SCREEN_IDS = Object.freeze(['mainmenu', 'playing', 'pause', 'inventory', 'character', 'skills', 'settings', 'saveload']);
  var BASE_IDS = Object.freeze(['mainmenu', 'playing']);

  // Which keymap action toggles which overlay. 'pause' is handled separately (Esc closes the top screen first).
  var TOGGLE_SCREEN = Object.freeze({
    toggleInventory: 'inventory',
    toggleCharacter: 'character',
    toggleSkills: 'skills',
    toggleSettings: 'settings',
    saveMenu: 'saveload'
  });

  function isKnown(id) {
    return SCREEN_IDS.indexOf(id) !== -1;
  }

  function isModal(id) {
    return BASE_IDS.indexOf(id) === -1;
  }

  function assertKnown(id) {
    if (!isKnown(id)) {
      throw new Error('unknown screen id: ' + String(id));
    }
  }

  // Default bindings come from the keymap when it is loaded; callers may inject their own map instead.
  function defaultBindings() {
    var keymap = RPG.input && RPG.input.keymap;
    return keymap ? keymap.defaultBindings() : {};
  }

  // Returns the keymap action bound to `code`, or null. Actions are scanned in keymap order, so the first match wins.
  function actionFor(bindings, code) {
    var actions = Object.keys(bindings);
    for (var i = 0; i < actions.length; i++) {
      if (bindings[actions[i]].indexOf(code) !== -1) return actions[i];
    }
    return null;
  }

  /** Creates a manager with the main menu on top. `bindings` defaults to the keymap defaults. */
  function create(options) {
    var opts = options || {};
    var bindings = opts.bindings || defaultBindings();
    var stack = ['mainmenu'];
    var handlers = {};

    SCREEN_IDS.forEach(function (id) {
      handlers[id] = { id: id, update: null, draw: null, onKey: null };
    });

    function top() {
      return stack.length ? stack[stack.length - 1] : null;
    }

    function has(id) {
      return stack.indexOf(id) !== -1;
    }

    /**
     * Shows `id`. A base screen replaces the whole stack. A modal screen is pushed on top, or does nothing when it is
     * already open (no duplicates). Throws for unknown ids.
     */
    function open(id) {
      assertKnown(id);
      if (!isModal(id)) {
        stack = [id];
      } else if (!has(id)) {
        stack.push(id);
      }
      return top();
    }

    /**
     * Hides a modal screen. With no id, pops the top screen when it is modal. Base screens are never removed, so the
     * stack cannot underflow. Returns true when something was closed.
     */
    function close(id) {
      if (id === undefined) {
        if (top() === null || !isModal(top())) return false;
        stack.pop();
        return true;
      }
      assertKnown(id);
      if (!isModal(id) || !has(id)) return false;
      stack.splice(stack.lastIndexOf(id), 1);
      return true;
    }

    /** Opens `id` when it is not on top, closes it when it is. Throws for unknown ids. */
    function toggle(id) {
      assertKnown(id);
      if (top() === id) {
        return close(id);
      }
      open(id);
      return true;
    }

    /** True while any modal screen is open. The simulation stops advancing while this is true. */
    function paused() {
      return stack.some(isModal);
    }

    /** Registers handlers for a screen. Replaces any earlier registration for the same id. */
    function register(def) {
      if (!def || typeof def !== 'object') {
        throw new Error('screen registration must be an object');
      }
      assertKnown(def.id);
      ['update', 'draw', 'onKey'].forEach(function (key) {
        if (def[key] !== undefined && typeof def[key] !== 'function') {
          throw new Error('screen ' + def.id + ': ' + key + ' must be a function');
        }
      });
      handlers[def.id] = {
        id: def.id,
        update: def.update || null,
        draw: def.draw || null,
        onKey: def.onKey || null
      };
    }

    /** Runs the top screen's update. Nothing runs while a modal overlay is on top of the base. */
    function update(dt) {
      var h = handlers[top()];
      if (h && h.update) h.update(dt);
    }

    /** Draws every screen on the stack, bottom to top, so overlays paint over the playfield. */
    function draw(ctx) {
      stack.forEach(function (id) {
        var h = handlers[id];
        if (h && h.draw) h.draw(ctx);
      });
    }

    /**
     * Routes one key code. The top screen's onKey gets first refusal: returning true consumes the key. Otherwise Esc
     * (the 'pause' action) closes the top modal, or opens pause from 'playing'; toggle keys open/close their overlay.
     * Returns true when the key was used.
     */
    function handleKey(code) {
      var action = actionFor(bindings, code);
      var h = handlers[top()];
      if (h && h.onKey && h.onKey(code, action) === true) return true;
      if (action === null) return false;

      if (action === 'pause') {
        if (top() !== null && isModal(top())) return close();
        if (top() === 'playing') return open('pause') === 'pause';
        return false;
      }
      if (top() === 'mainmenu' || top() === null) return false;
      if (Object.prototype.hasOwnProperty.call(TOGGLE_SCREEN, action)) {
        return toggle(TOGGLE_SCREEN[action]);
      }
      return false;
    }

    return {
      open: open,
      close: close,
      toggle: toggle,
      top: top,
      has: has,
      paused: paused,
      register: register,
      update: update,
      draw: draw,
      handleKey: handleKey,
      stack: function () { return stack.slice(); }
    };
  }

  RPG.ui.screens = {
    SCREEN_IDS: SCREEN_IDS,
    isKnown: isKnown,
    isModal: isModal,
    create: create
  };
})(globalThis.RPG = globalThis.RPG || {});

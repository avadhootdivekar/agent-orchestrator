/*
 * The only file that touches the DOM for input. Attaches listeners to a window-like target and an optional canvas,
 * records raw events into a raw state, and turns that state into a per-frame intent via the pure intent module.
 * Use RPG.input.create() per game instance, so tests and any second game do not share state.
 */
(function (RPG) {
  'use strict';
  var input = (RPG.input = RPG.input || {});
  var keymap = input.keymap;
  var raw = input.raw;
  var intent = input.intent;

  var MOUSE_CODE_PREFIX = 'Mouse';
  // Shift is a modifier, not a code, so Tab with Shift held is recorded under its own code.
  var SHIFT_TAB = 'Shift+Tab';
  // Keystrokes aimed at a form control must not move the character or fire skills.
  var TEXT_TAGS = /^(INPUT|TEXTAREA|SELECT)$/;

  function create() {
    var state = raw.createRawState(keymap.defaultBindings());
    var listeners = [];
    var canvasEl = null;

    function on(target, type, fn, options) {
      target.addEventListener(type, fn, options);
      listeners.push({ target: target, type: type, fn: fn, options: options });
    }

    function isTyping(e) {
      var t = e.target;
      return !!(t && ((t.tagName && TEXT_TAGS.test(t.tagName)) || t.isContentEditable));
    }

    function codeOf(e) {
      return e.code === 'Tab' && e.shiftKey ? SHIFT_TAB : e.code;
    }

    function isBound(code) {
      return keymap.ACTIONS.some(function (action) {
        return state.bindings[action].indexOf(code) !== -1;
      });
    }

    // Canvas CSS px, the same units as the camera viewport.
    function toCanvas(e) {
      var rect = canvasEl.getBoundingClientRect ? canvasEl.getBoundingClientRect() : { left: 0, top: 0 };
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    function attach(windowTarget, canvas) {
      detach();
      canvasEl = canvas || null;

      on(windowTarget, 'keydown', function (e) {
        if (isTyping(e)) return;
        var code = codeOf(e);
        if (isBound(code)) e.preventDefault();
        raw.setCode(state, code, true);
      });
      on(windowTarget, 'keyup', function (e) {
        raw.setCode(state, e.code, false);
        // Shift may be released before Tab, so the synthesized code must be cleared by the Tab keyup.
        if (e.code === 'Tab') raw.setCode(state, SHIFT_TAB, false);
      });
      on(windowTarget, 'blur', function () {
        raw.clearHeld(state);
      });
      on(windowTarget, 'mouseup', function (e) {
        raw.setCode(state, MOUSE_CODE_PREFIX + e.button, false);
      });

      if (canvasEl) {
        on(canvasEl, 'mousemove', function (e) {
          var p = toCanvas(e);
          raw.setPointer(state, p.x, p.y);
        });
        on(canvasEl, 'mousedown', function (e) {
          var p = toCanvas(e);
          raw.setPointer(state, p.x, p.y);
          raw.setCode(state, MOUSE_CODE_PREFIX + e.button, true);
        });
        // Right mouse can be bound to an action, so the browser menu must not swallow it.
        on(canvasEl, 'contextmenu', function (e) {
          e.preventDefault();
        });
        // Non-passive so the wheel does not scroll the page while it cycles weapons over the canvas.
        on(canvasEl, 'wheel', function (e) {
          e.preventDefault();
          if (e.deltaY === 0) return;
          raw.addWheel(state, e.deltaY > 0 ? 1 : -1);
        }, { passive: false });
      }
    }

    function detach() {
      listeners.forEach(function (l) {
        l.target.removeEventListener(l.type, l.fn, l.options);
      });
      listeners = [];
      canvasEl = null;
    }

    // Samples the intent for this frame, then clears edges so each press is delivered exactly once.
    function buildFrame(camera, viewW, viewH) {
      var frame = intent.sampleIntent(state, {
        x: camera.x,
        y: camera.y,
        zoom: camera.zoom,
        viewW: viewW,
        viewH: viewH
      });
      raw.clearPressed(state);
      return frame;
    }

    function getBindings() {
      return keymap.copyBindings(state.bindings);
    }

    // Throws on an unknown action or an invalid code. Unbound keys are ignored at sample time, not here.
    function setBinding(action, code) {
      state.bindings = keymap.withBinding(state.bindings, action, code);
    }

    function resetBindings() {
      state.bindings = keymap.defaultBindings();
    }

    return {
      attach: attach,
      detach: detach,
      buildFrame: buildFrame,
      getBindings: getBindings,
      setBinding: setBinding,
      resetBindings: resetBindings
    };
  }

  input.create = create;
})(globalThis.RPG = globalThis.RPG || {});

/* HUD overlays (docs/INTEGRATION-PLAN.md §4, W4-13): a bounded toast/banner queue fed by sim events, plus the death and
   victory screens and the injected text-entry prompt. Headless: no DOM, no timers. Time only advances through update(dt)
   with dt in seconds of game time, so tests drive expiry with fixed steps. */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var MAX_ENTRIES = 4;        // queue bound: the oldest entry is dropped when a new one would exceed it
  var TOAST_TTL = 3;          // seconds a toast stays on screen
  var BANNER_TTL = 4;         // seconds a banner stays on screen
  var TEXT_COLOR = '#d8d2c4';
  var BANNER_COLOR = '#e8c15a';
  var ERROR_COLOR = '#e06c6c';
  var SCRIM_COLOR = 'rgba(8, 6, 10, 0.72)';
  var TITLE_FONT = '32px system-ui, sans-serif';
  var BODY_FONT = '16px system-ui, sans-serif';
  var HINT_FONT = '13px system-ui, sans-serif';
  var DEFAULT_WIDTH = 800;    // used when the draw context has no canvas (headless callers)
  var DEFAULT_HEIGHT = 600;
  var ENTRY_GAP = 22;
  var DEAD_TITLE = 'You have fallen';
  var VICTORY_TITLE = 'The Warden of Ash is slain';
  var CONFIRM_HINT = 'Press Enter or click to continue';
  var BUTTON_SIZE = 32;       // on-canvas HUD buttons (bottom-right corner), CSS px
  var BUTTON_GAP = 6;
  var BUTTON_MARGIN = 10;
  var BUTTONS = Object.freeze([
    Object.freeze({ id: 'inventory', label: 'I', title: 'Inventory' }),
    Object.freeze({ id: 'skills', label: 'S', title: 'Skills' }),
    Object.freeze({ id: 'character', label: 'C', title: 'Character' }),
    Object.freeze({ id: 'pause', label: '||', title: 'Pause' })
  ]);
  var CONFIRM_KEYS = Object.freeze(['Enter', 'NumpadEnter']);

  /**
   * create(options) -> overlays. options (all optional): { events, screens, prompt, onRespawn, onContinue }.
   * `events` defaults to RPG.core.events. `screens` is the manager from ui/screens; when it is absent the overlays still
   * queue toasts but cannot open the death or victory screens. `prompt(label, cb)` is the injected text-entry dialog.
   * `onRespawn` / `onContinue` run after the player confirms on the death / victory screen.
   */
  function create(options) {
    var opts = options || {};
    var events = opts.events || (RPG.core && RPG.core.events) || null;
    var screens = opts.screens || null;
    var promptFn = opts.prompt || null;
    var onRespawn = opts.onRespawn || null;
    var onContinue = opts.onContinue || null;

    var entries = [];   // { kind: 'toast'|'banner', text, color, ttl }, oldest first
    var deathInfo = null;
    var victoryShown = false;
    var offs = [];

    function push(entry) {
      entries.push(entry);
      while (entries.length > MAX_ENTRIES) entries.shift();
    }

    /** Queues a short message at the bottom of the screen. */
    function toast(text, color) {
      push({ kind: 'toast', text: String(text), color: color || TEXT_COLOR, ttl: TOAST_TTL });
    }

    /** Queues a centred message, used for level-ups and other milestones. */
    function banner(text, color) {
      push({ kind: 'banner', text: String(text), color: color || BANNER_COLOR, ttl: BANNER_TTL });
    }

    /** Advances expiry by `dt` seconds and drops entries whose time is up. Non-positive steps are ignored. */
    function update(dt) {
      if (!(dt > 0)) return;
      entries.forEach(function (e) { e.ttl -= dt; });
      entries = entries.filter(function (e) { return e.ttl > 0; });
    }

    /** Copies of the live entries, oldest first. Tests and the debug overlay read this; callers cannot mutate the queue. */
    function list() {
      return entries.map(function (e) { return { kind: e.kind, text: e.text, color: e.color, ttl: e.ttl }; });
    }

    /** Opens the death screen. `payload` is the PLAYER_DIED payload ({ gold }). */
    function showDead(payload) {
      deathInfo = { gold: payload && typeof payload.gold === 'number' ? payload.gold : 0 };
      if (screens) screens.open('dead');
    }

    /** Opens the victory screen once per run; later calls are ignored so a repeat trigger cannot stack it. */
    function showVictory() {
      if (victoryShown) return;
      victoryShown = true;
      if (screens) screens.open('victory');
    }

    /** Forgets queued messages and the once-per-run latches; the game calls this when it adopts a new or loaded world. */
    function reset() {
      entries = [];
      deathInfo = null;
      victoryShown = false;
    }

    /** Closes a terminal screen and runs its callback. Keys other than the confirm keys fall through to the manager. */
    function confirm(id, callback) {
      function accept() {
        if (screens) screens.close(id);
        if (callback) callback();
        return true;
      }
      return {
        onKey: function (code) { return CONFIRM_KEYS.indexOf(code) === -1 ? false : accept(); },
        onPointer: accept // a tap or click is the same "continue" as Enter, so touch play can leave these screens
      };
    }

    function canvasSize(ctx) {
      var canvas = ctx && ctx.canvas;
      return {
        w: canvas && canvas.width ? canvas.width : DEFAULT_WIDTH,
        h: canvas && canvas.height ? canvas.height : DEFAULT_HEIGHT
      };
    }

    function drawCentered(ctx, lines) {
      var size = canvasSize(ctx);
      ctx.fillStyle = SCRIM_COLOR;
      ctx.fillRect(0, 0, size.w, size.h);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      var y = size.h / 2 - ENTRY_GAP * (lines.length - 1);
      lines.forEach(function (line) {
        ctx.font = line.font;
        ctx.fillStyle = line.color;
        ctx.fillText(line.text, size.w / 2, y);
        y += ENTRY_GAP * 2;
      });
    }

    /** Draws the queued toasts and banners. Banners sit in the upper third, toasts stack from the bottom. */
    function drawToasts(ctx) {
      var size = canvasSize(ctx);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      var toastY = size.h - ENTRY_GAP * 2;
      var bannerY = size.h / 3;
      entries.forEach(function (e) {
        ctx.font = e.kind === 'banner' ? TITLE_FONT : BODY_FONT;
        ctx.fillStyle = e.color;
        if (e.kind === 'banner') {
          ctx.fillText(e.text, size.w / 2, bannerY);
          bannerY += ENTRY_GAP * 2;
        } else {
          ctx.fillText(e.text, size.w / 2, toastY);
          toastY -= ENTRY_GAP;
        }
      });
    }

    /** Rectangles of the HUD buttons for a view of `size` { w, h } (CSS px): [{ id, x, y, w, h, label }], left to right. */
    function buttonRects(size) {
      var w = size && size.w > 0 ? size.w : DEFAULT_WIDTH, h = size && size.h > 0 ? size.h : DEFAULT_HEIGHT;
      var x0 = w - BUTTON_MARGIN - BUTTONS.length * BUTTON_SIZE - (BUTTONS.length - 1) * BUTTON_GAP;
      return BUTTONS.map(function (b, i) {
        return { id: b.id, label: b.label, x: x0 + i * (BUTTON_SIZE + BUTTON_GAP), y: h - BUTTON_MARGIN - BUTTON_SIZE, w: BUTTON_SIZE, h: BUTTON_SIZE };
      });
    }

    /** The id ('inventory' | 'skills' | 'character' | 'pause') of the button under (x, y), or null. */
    function hitButton(x, y, size) {
      var rects = buttonRects(size);
      for (var i = 0; i < rects.length; i++) {
        var r = rects[i];
        if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h) return r.id;
      }
      return null;
    }

    /** Draws the small translucent HUD buttons on the canvas (no DOM). `size` is the view in CSS px (the layout). */
    function drawButtons(ctx, size) {
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = HINT_FONT;
      buttonRects(size).forEach(function (r) {
        ctx.fillStyle = 'rgba(8, 6, 10, 0.45)';
        ctx.fillRect(r.x, r.y, r.w, r.h);
        ctx.fillStyle = TEXT_COLOR;
        ctx.fillText(r.label, r.x + r.w / 2, r.y + r.h / 2);
      });
    }

    function drawDead(ctx) {
      var gold = deathInfo ? deathInfo.gold : 0;
      drawCentered(ctx, [
        { text: DEAD_TITLE, font: TITLE_FONT, color: ERROR_COLOR },
        { text: 'Gold lost: ' + gold, font: BODY_FONT, color: TEXT_COLOR },
        { text: CONFIRM_HINT, font: HINT_FONT, color: TEXT_COLOR }
      ]);
    }

    function drawVictory(ctx) {
      drawCentered(ctx, [
        { text: VICTORY_TITLE, font: TITLE_FONT, color: BANNER_COLOR },
        { text: CONFIRM_HINT, font: HINT_FONT, color: TEXT_COLOR }
      ]);
    }

    /** Text entry (seed, import paste) goes through the injected dialog so tests stay headless. Without one, cb(null). */
    function prompt(label, cb) {
      if (promptFn) return promptFn(label, cb);
      cb(null);
      return undefined;
    }

    if (screens) {
      var dead = confirm('dead', onRespawn), won = confirm('victory', onContinue);
      screens.register({ id: 'dead', draw: drawDead, onKey: dead.onKey, onPointer: dead.onPointer });
      screens.register({ id: 'victory', draw: drawVictory, onKey: won.onKey, onPointer: won.onPointer });
    }
    if (events) {
      var NAMES = events.NAMES;
      offs.push(events.on(NAMES.SAVE, function (p) {
        if (p && p.ok === false) toast('Save failed: ' + p.error, ERROR_COLOR);
        else toast('Saved');
      }));
      offs.push(events.on(NAMES.LOAD, function () { toast('Loaded'); }));
      offs.push(events.on(NAMES.LEVEL_UP, function (p) { banner('Level ' + (p && p.level)); }));
      offs.push(events.on(NAMES.PLAYER_DIED, function (p) { showDead(p); }));
      offs.push(events.on(NAMES.VICTORY, function () { showVictory(); }));
    }

    /** Unsubscribes from the event bus. Call when the game is torn down. */
    function dispose() {
      offs.forEach(function (off) { off(); });
      offs = [];
    }

    return {
      toast: toast,
      banner: banner,
      update: update,
      reset: reset,
      list: list,
      showDead: showDead,
      showVictory: showVictory,
      drawToasts: drawToasts,
      buttonRects: buttonRects,
      hitButton: hitButton,
      drawButtons: drawButtons,
      prompt: prompt,
      dispose: dispose
    };
  }

  RPG.ui.overlays = {
    MAX_ENTRIES: MAX_ENTRIES,
    create: create
  };
})(globalThis.RPG = globalThis.RPG || {});

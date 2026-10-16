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
  var CONFIRM_HINT = 'Press Enter to continue';
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

    /** Closes a terminal screen and runs its callback. Keys other than the confirm keys fall through to the manager. */
    function confirm(id, callback) {
      return function (code) {
        if (CONFIRM_KEYS.indexOf(code) === -1) return false;
        if (screens) screens.close(id);
        if (callback) callback();
        return true;
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
      screens.register({ id: 'dead', draw: drawDead, onKey: confirm('dead', onRespawn) });
      screens.register({ id: 'victory', draw: drawVictory, onKey: confirm('victory', onContinue) });
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
      list: list,
      showDead: showDead,
      showVictory: showVictory,
      drawToasts: drawToasts,
      prompt: prompt,
      dispose: dispose
    };
  }

  RPG.ui.overlays = {
    MAX_ENTRIES: MAX_ENTRIES,
    create: create
  };
})(globalThis.RPG = globalThis.RPG || {});

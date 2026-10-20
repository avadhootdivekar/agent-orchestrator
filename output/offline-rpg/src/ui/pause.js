/* Pause menu (docs/INTEGRATION-PLAN.md §3.1, acceptance step 15-17). Canvas overlay for RPG.ui.screens, id 'pause'.
   Items: Resume | Save game | Load game | Settings | Quit to menu. Up/Down (or W/S) move, Enter activates, Esc/P resumes
   through the screen manager. Quit to menu loses progress since the last save, so it asks for a second Enter.
   Settings stays disabled until a settings screen is supplied (openSettings, W4-13c). */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var DEFAULT_LAYOUT = Object.freeze({ x: 0, y: 0, w: 640, h: 480 });
  var ITEMS = Object.freeze(['resume', 'save', 'load', 'settings', 'quit']);
  var LABEL = Object.freeze({ resume: 'Resume', save: 'Save game', load: 'Load game', settings: 'Settings', quit: 'Quit to menu' });
  var PANEL_W = 380; // wide enough for the quit confirmation line
  var PANEL_PAD = 20;
  var ROW_H = 36;
  var DIM_OVERLAY = 'rgba(0, 0, 0, 0.5)';
  var PANEL_COLOR = 'rgba(16, 14, 20, 0.95)';
  var TEXT_COLOR = '#d8d2c4';
  var DIM_COLOR = '#5f5a4f';
  var CURSOR_COLOR = '#e8c15a';
  var WARN_COLOR = '#e0a04c';
  var TITLE_FONT = '22px system-ui, sans-serif';
  var LABEL_FONT = '15px system-ui, sans-serif';

  function wrap(i, n) { return (i + n) % n; }

  /**
   * Creates the pause overlay. opts: { game, screens, saveload, openSettings?, layout? }.
   * `game` needs setPaused(false) and quitToMenu(). `saveload` is the screen from ui/saveload.js; Save and Load open it.
   * Returns the definition for screens.register().
   */
  function createScreen(opts) {
    var game = opts.game;
    var screens = opts.screens;
    var saveload = opts.saveload;
    var openSettings = opts.openSettings || null;
    var layout = opts.layout || DEFAULT_LAYOUT;
    var state = { cursor: 0, status: '', confirmQuit: false };

    function enabled(item) { return item !== 'settings' || openSettings !== null; }

    function openSaveLoad(mode) {
      saveload.start(mode);
      screens.open('saveload');
    }

    function resume() {
      game.setPaused(false);
      screens.close('pause');
    }

    function activate(item) {
      if (item === 'resume') resume();
      else if (item === 'save' || item === 'load') openSaveLoad(item);
      else if (item === 'settings') {
        if (enabled(item)) openSettings();
        else state.status = 'Settings arrive with the settings screen (W4-13c).';
      } else if (item === 'quit') {
        state.confirmQuit = true;
        state.status = 'Quit to menu without saving? Enter (or click Quit again) confirms, anything else cancels.';
      }
    }

    function onKey(code, action) {
      if (state.confirmQuit) {
        var confirmed = code === 'Enter';
        state.confirmQuit = false;
        state.status = '';
        if (confirmed) game.quitToMenu();
        return true;
      }
      if (action === 'moveUp' || action === 'moveDown') {
        state.cursor = wrap(state.cursor + (action === 'moveUp' ? -1 : 1), ITEMS.length);
        state.status = '';
        return true;
      }
      if (code === 'Enter') { activate(ITEMS[state.cursor]); return true; }
      return action !== 'pause'; // Esc/P falls through so the manager closes the overlay
    }

    function panel() {
      var h = PANEL_PAD * 2 + 40 + ITEMS.length * ROW_H + 40;
      return { x: layout.x + (layout.w - PANEL_W) / 2, y: layout.y + (layout.h - h) / 2, w: PANEL_W, h: h };
    }

    // Row i is drawn with its baseline at panel.y + PANEL_PAD + 40 + i * ROW_H and its cursor box starts 20 above that.
    function rowAt(x, y) {
      var p = panel();
      for (var i = 0; i < ITEMS.length; i++) {
        var top = p.y + PANEL_PAD + 40 + i * ROW_H - 20;
        if (x >= p.x + PANEL_PAD - 6 && x < p.x + p.w - PANEL_PAD + 6 && y >= top && y < top + ROW_H - 6) return i;
      }
      return -1;
    }

    // A press on a row selects and activates it. Outside the panel it is "back" (resume). While the quit question is
    // open a second press on the Quit row confirms; any other press cancels, like keys do.
    function onPointer(x, y) {
      var p = panel();
      if (x < p.x || x >= p.x + p.w || y < p.y || y >= p.y + p.h) {
        if (!state.confirmQuit) return false;
        state.confirmQuit = false;
        state.status = '';
        return true;
      }
      var i = rowAt(x, y);
      if (state.confirmQuit) {
        state.confirmQuit = false;
        state.status = '';
        if (i >= 0 && ITEMS[i] === 'quit') game.quitToMenu();
        return true;
      }
      if (i >= 0) { state.cursor = i; state.status = ''; activate(ITEMS[i]); }
      return true;
    }

    function draw(ctx) {
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
      var p = panel();
      var panelX = p.x, panelY = p.y, panelH = p.h;
      var x = panelX + PANEL_PAD;
      ctx.fillStyle = DIM_OVERLAY;
      ctx.fillRect(layout.x, layout.y, layout.w, layout.h);
      ctx.fillStyle = PANEL_COLOR;
      ctx.fillRect(panelX, panelY, PANEL_W, panelH);
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = TITLE_FONT;
      ctx.fillText('Paused', x, panelY + PANEL_PAD + 18);
      ctx.font = LABEL_FONT;
      ITEMS.forEach(function (item, i) {
        var y = panelY + PANEL_PAD + 40 + i * ROW_H;
        if (i === state.cursor) {
          ctx.strokeStyle = CURSOR_COLOR;
          ctx.lineWidth = 2;
          ctx.strokeRect(x - 6, y - 20, PANEL_W - PANEL_PAD * 2 + 12, ROW_H - 6);
        }
        ctx.fillStyle = enabled(item) ? TEXT_COLOR : DIM_COLOR;
        ctx.fillText(LABEL[item], x, y);
      });
      ctx.fillStyle = state.confirmQuit ? WARN_COLOR : DIM_COLOR;
      ctx.fillText(state.status, x, panelY + panelH - PANEL_PAD);
    }

    return {
      id: 'pause',
      draw: draw,
      onKey: onKey,
      onPointer: onPointer
    };
  }

  RPG.ui.pause = {
    createScreen: createScreen
  };
})(globalThis.RPG = globalThis.RPG || {});

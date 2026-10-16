/* Main menu (docs/INTEGRATION-PLAN.md §3.1, acceptance steps 2, 13, 17, 21). Canvas screen for RPG.ui.screens, id 'mainmenu'.
   Root list: New game | Continue | Import save. Continue loads the newest readable save and is disabled without one.
   New game form: difficulty (Left/Right on its row) and an optional seed (letters, digits and '-', up to 12 characters;
   blank means the game picks one from its clock). Up/Down (W/S on the root list) move; Enter activates; Esc goes back.
   Inside the seed field every letter is typed text, so navigation there uses the arrow keys only. */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var DEFAULT_LAYOUT = Object.freeze({ x: 0, y: 0, w: 640, h: 480 });
  var DEFAULT_DIFFICULTY = 'normal';
  var SEED_MAX = 12;
  var FIELD = Object.freeze({ difficulty: 0, seed: 1 });
  var FIELD_COUNT = 3; // difficulty, seed, and the "Start game" row (Enter starts from any row)
  var ROOT_ITEMS = Object.freeze(['new', 'continue', 'import']);
  var ROOT_LABEL = Object.freeze({ new: 'New game', continue: 'Continue', import: 'Import save' });
  var PANEL_PAD = 24;
  var ROW_H = 40;
  var TOP = 150;
  var BG_COLOR = '#14111a';
  var TEXT_COLOR = '#d8d2c4';
  var TITLE_COLOR = '#e8c15a';
  var DIM_COLOR = '#5f5a4f';
  var CURSOR_COLOR = '#e8c15a';
  var BAD_COLOR = '#e06c6c';
  var TITLE_FONT = '28px system-ui, sans-serif';
  var LABEL_FONT = '16px system-ui, sans-serif';

  function wrap(i, n) { return (i + n) % n; }

  // Printable seed characters from KeyboardEvent.code: letters come out lower-case, so the seed is case-insensitive.
  function seedCharFor(code) {
    var m = /^(?:Key([A-Z])|Digit(\d)|Numpad(\d))$/.exec(code);
    if (m) return (m[1] || m[2] || m[3]).toLowerCase();
    if (code === 'Minus' || code === 'NumpadSubtract') return '-';
    return '';
  }

  /** Newest save that is not corrupt, from storage.list(): { slot, savedAt, ... } or null. ISO timestamps sort as text. */
  function latestSave(storage) {
    var saves = storage.list().filter(function (entry) { return !entry.corrupt; });
    saves.sort(function (a, b) { return String(b.savedAt).localeCompare(String(a.savedAt)); });
    return saves[0] || null;
  }

  /**
   * Creates the main menu. opts: { game, screens, saveload, storage?, layout? }.
   * `game` needs newGame({seed, difficulty}) and loadFromSlot(slot). `saveload` is the screen from ui/saveload.js, used
   * for Import. `storage` defaults to RPG.persistence.storage and needs list(). Returns the definition for screens.register().
   */
  function createScreen(opts) {
    var game = opts.game;
    var screens = opts.screens;
    var saveload = opts.saveload;
    var storage = opts.storage || RPG.persistence.storage;
    var layout = opts.layout || DEFAULT_LAYOUT;
    // Custom has no preset to start from here: its sliders arrive with the settings screen (W4-13c).
    var difficultyIds = Object.keys(RPG.data.difficulties).filter(function (id) { return id !== 'custom'; });
    var state = {
      mode: 'root', cursor: 0, status: '', field: FIELD.difficulty, seed: '',
      difficulty: Math.max(0, difficultyIds.indexOf(DEFAULT_DIFFICULTY))
    };

    function setStatus(text) { state.status = text; }

    function canContinue() { return latestSave(storage) !== null; }

    function activate(item) {
      if (item === 'new') {
        state.mode = 'newgame';
        state.field = FIELD.difficulty;
        setStatus('');
      } else if (item === 'continue') {
        var latest = latestSave(storage);
        if (!latest) {
          setStatus('No save to continue.');
          return;
        }
        var r = game.loadFromSlot(latest.slot);
        if (!r.ok) setStatus(r.error || r.code || 'Load failed.');
      } else if (item === 'import') {
        saveload.start('import');
        screens.open('saveload');
      }
    }

    function startGame() {
      var seed = state.seed === '' ? undefined : state.seed;
      game.newGame({ seed: seed, difficulty: difficultyIds[state.difficulty] });
    }

    function onRootKey(code, action) {
      if (action === 'moveUp' || action === 'moveDown') {
        state.cursor = wrap(state.cursor + (action === 'moveUp' ? -1 : 1), ROOT_ITEMS.length);
        return true;
      }
      if (code === 'Enter') { activate(ROOT_ITEMS[state.cursor]); return true; }
      return false;
    }

    function onNewGameKey(code, action) {
      if (action === 'pause') { state.mode = 'root'; setStatus(''); return true; }
      if (code === 'Enter') { startGame(); return true; }
      if (code === 'ArrowDown') { state.field = Math.min(state.field + 1, FIELD_COUNT - 1); return true; }
      if (code === 'ArrowUp') { state.field = Math.max(state.field - 1, 0); return true; }
      if (state.field === FIELD.difficulty) {
        if (action === 'moveLeft') state.difficulty = wrap(state.difficulty - 1, difficultyIds.length);
        if (action === 'moveRight') state.difficulty = wrap(state.difficulty + 1, difficultyIds.length);
        return true;
      }
      if (state.field === FIELD.seed) {
        if (code === 'Backspace') state.seed = state.seed.slice(0, -1);
        var ch = seedCharFor(code);
        if (ch && state.seed.length < SEED_MAX) state.seed += ch;
      }
      return true;
    }

    function onKey(code, action) {
      return state.mode === 'newgame' ? onNewGameKey(code, action) : onRootKey(code, action);
    }

    function drawRoot(ctx) {
      var x = layout.x + PANEL_PAD;
      ROOT_ITEMS.forEach(function (item, i) {
        var y = layout.y + TOP + i * ROW_H;
        var enabled = item !== 'continue' || canContinue();
        if (i === state.cursor) {
          ctx.strokeStyle = CURSOR_COLOR;
          ctx.lineWidth = 2;
          ctx.strokeRect(x - 8, y - 22, layout.w - PANEL_PAD * 2 + 16, ROW_H - 6);
        }
        ctx.fillStyle = enabled ? TEXT_COLOR : DIM_COLOR;
        ctx.fillText(ROOT_LABEL[item], x, y);
      });
    }

    function drawNewGame(ctx) {
      var x = layout.x + PANEL_PAD;
      var seedShown = state.seed === '' ? '(random)' : state.seed;
      var rows = [
        'Difficulty: < ' + RPG.data.difficulties[difficultyIds[state.difficulty]].name + ' >',
        'Seed: ' + seedShown,
        'Start game'
      ];
      rows.forEach(function (text, i) {
        var y = layout.y + TOP + i * ROW_H;
        if (i === state.field) {
          ctx.strokeStyle = CURSOR_COLOR;
          ctx.lineWidth = 2;
          ctx.strokeRect(x - 8, y - 22, layout.w - PANEL_PAD * 2 + 16, ROW_H - 6);
        }
        ctx.fillStyle = TEXT_COLOR;
        ctx.fillText(text, x, y);
      });
    }

    function draw(ctx) {
      var x = layout.x + PANEL_PAD;
      ctx.fillStyle = BG_COLOR;
      ctx.fillRect(layout.x, layout.y, layout.w, layout.h);
      ctx.fillStyle = TITLE_COLOR;
      ctx.font = TITLE_FONT;
      ctx.fillText('Offline RPG', x, layout.y + 80);
      ctx.font = LABEL_FONT;
      if (state.mode === 'newgame') drawNewGame(ctx);
      else drawRoot(ctx);
      ctx.fillStyle = state.status ? BAD_COLOR : DIM_COLOR;
      ctx.fillText(state.status, x, layout.y + layout.h - 56);
      ctx.fillStyle = DIM_COLOR;
      ctx.fillText(state.mode === 'newgame'
        ? 'Up/Down field | Left/Right difficulty | type seed | Enter start | Esc back'
        : 'Up/Down choose | Enter select', x, layout.y + layout.h - 24);
    }

    return {
      id: 'mainmenu',
      draw: draw,
      onKey: onKey
    };
  }

  RPG.ui.mainmenu = {
    createScreen: createScreen,
    latestSave: latestSave,
    seedCharFor: seedCharFor
  };
})(globalThis.RPG = globalThis.RPG || {});

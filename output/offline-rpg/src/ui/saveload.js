/* Save/load screen (docs/INTEGRATION-PLAN.md §3.3, acceptance steps 14-22). Canvas screen for RPG.ui.screens, id 'saveload'.
   Slots come from storage.list(), so each row shows the metadata saved with it (level, area, time, playtime).
   Keys: Up/Down (or W/S) pick a slot; Enter runs the mode's action (load | save | import) on it; X exports it;
   G imports into it; D deletes it. Overwriting a slot and deleting one need a second Enter; any other key cancels.
   The reserved 'auto' slot is read-only here (the game writes it). Browser file I/O is injected: readImportText()
   resolves to the chosen file's text (or null when cancelled), download(filename, text) saves an export. */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var SLOTS = Object.freeze(['auto', '1', '2', '3']); // same order and ids as persistence/storage.js SLOTS
  var AUTO_SLOT = 'auto';
  var MODES = Object.freeze(['load', 'save', 'import']);
  var TITLES = Object.freeze({ load: 'Load game', save: 'Save game', import: 'Import save' });
  var PRIMARY_LABEL = Object.freeze({ load: 'Load', save: 'Save', import: 'Import' });
  var KEY = Object.freeze({ primary: 'Enter', export: 'KeyX', import: 'KeyG', remove: 'KeyD' });
  var DEFAULT_LAYOUT = Object.freeze({ x: 0, y: 0, w: 640, h: 480 });
  var PANEL_PAD = 16;
  var ROW_H = 36;
  var LIST_TOP = 80;
  var FOOTER_H = 24; // presses in this bottom strip fall through to the manager as "back"
  var BUTTONS = Object.freeze([ // pointer stand-ins for the X / G / D keys, drawn above the status line
    { op: 'export', label: 'Export' }, { op: 'import', label: 'Import' }, { op: 'delete', label: 'Delete' }]);
  var BUTTON_W = 90;
  var BUTTON_H = 22;
  var BUTTON_GAP = 8;
  var BUTTON_ABOVE_BOTTOM = 76;
  var PANEL_COLOR = 'rgba(16, 14, 20, 0.92)';
  var TEXT_COLOR = '#d8d2c4';
  var DIM_COLOR = '#7d7768';
  var CURSOR_COLOR = '#e8c15a';
  var WARN_COLOR = '#e0a04c';
  var BAD_COLOR = '#e06c6c';
  var TITLE_FONT = '20px system-ui, sans-serif';
  var LABEL_FONT = '14px system-ui, sans-serif';
  var MEMORY_WARNING = 'Saves are kept in memory only and are lost when this page closes.';

  function msg(e) { return e && e.message ? e.message : String(e); }
  function wrap(i, n) { return (i + n) % n; }

  function slotLabel(slot) { return slot === AUTO_SLOT ? 'Auto save' : 'Slot ' + slot; }

  // "2026-10-16 14:03 UTC" from an ISO timestamp; the raw value when it does not parse.
  function formatSaved(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return String(iso || 'unknown time');
    return new Date(t).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
  }

  // Seconds -> "1h 02m".
  function formatPlaytime(seconds) {
    var minutes = Math.floor((seconds || 0) / 60);
    return Math.floor(minutes / 60) + 'h ' + ('0' + (minutes % 60)).slice(-2) + 'm';
  }

  function areaName(id) {
    var areas = RPG.data && RPG.data.areas;
    return areas && areas[id] ? areas[id].name : String(id);
  }

  /** One row per slot: { slot, label, empty } | { slot, label, corrupt } | { slot, label, level, area, saved, playtime }. */
  function rowsFor(storage) {
    var listed = {};
    storage.list().forEach(function (entry) { listed[entry.slot] = entry; });
    return SLOTS.map(function (slot) {
      var entry = listed[slot];
      var row = { slot: slot, label: slotLabel(slot), empty: !entry, corrupt: !!(entry && entry.corrupt) };
      if (entry && !entry.corrupt) {
        row.level = entry.level;
        row.area = areaName(entry.area);
        row.saved = formatSaved(entry.savedAt);
        row.playtime = formatPlaytime(entry.playtime);
      }
      return row;
    });
  }

  function rowText(row) {
    if (row.corrupt) return row.label + ': unreadable save';
    if (row.empty) return row.label + ': empty';
    return row.label + ': Lv ' + row.level + ' | ' + row.area + ' | ' + row.saved + ' | ' + row.playtime;
  }

  /** Overwrite and delete are the destructive steps; every other operation runs on the first Enter. */
  function needsConfirm(op, row) {
    if (op === 'delete') return true;
    return (op === 'save' || op === 'import') && !row.empty;
  }

  function confirmText(c, row) {
    if (c.op === 'delete') return 'Delete ' + row.label + '? Enter confirms, any other key cancels.';
    return row.label + ' will be overwritten. Enter confirms, any other key cancels.';
  }

  /**
   * Creates the save/load screen. opts: { game, screens, storage?, readImportText?, download?, layout? }.
   * `game` needs loadFromSlot, saveToSlot, importSave(text, slot), exportSave(slot), deleteSave(slot).
   * `storage` defaults to RPG.persistence.storage and needs list() and available().
   * Returns the screen definition for screens.register(); start(mode) prepares it before screens.open('saveload').
   */
  function createScreen(opts) {
    var game = opts.game;
    var screens = opts.screens;
    var storage = opts.storage || RPG.persistence.storage;
    var readImportText = opts.readImportText || null;
    var download = opts.download || null;
    var layout = opts.layout || DEFAULT_LAYOUT;
    var state = { mode: 'load', cursor: 0, status: '', confirm: null, busy: false };

    function rows() { return rowsFor(storage); }
    function setStatus(text) { state.status = text; }
    function failText(r) { return r.error || r.code || 'failed'; }

    // Closes the screen after a successful load/save/import; the game may already have closed it (loadFromSlot does).
    function finish(r, okText) {
      if (!r.ok) return setStatus(failText(r));
      setStatus(okText);
      screens.close('saveload');
    }

    function run(op, row) {
      state.confirm = null;
      if (op === 'load') return finish(game.loadFromSlot(row.slot), 'Loaded ' + row.label);
      if (op === 'save') return finish(game.saveToSlot(row.slot), 'Saved to ' + row.label);
      if (op === 'delete') {
        var removed = game.deleteSave(row.slot);
        return setStatus(removed.ok ? row.label + ' deleted.' : failText(removed));
      }
      if (op === 'export') return runExport(row);
      if (op === 'import') return runImport(row);
    }

    function runExport(row) {
      if (row.empty) return setStatus('Nothing to export in ' + row.label + '.');
      var r = game.exportSave(row.slot);
      if (!r.ok) return setStatus(failText(r));
      if (!download) return setStatus('Export needs a file download hook (persistence/files.js, W4-08).');
      download(r.filename, r.text);
      setStatus('Exported ' + r.filename + '.');
    }

    // The file is read asynchronously; keys are ignored until it resolves so a second import cannot start.
    function runImport(row) {
      if (!readImportText) return setStatus('Import needs a file picker hook (persistence/files.js, W4-08).');
      state.busy = true;
      setStatus('Choose a save file...');
      Promise.resolve().then(readImportText).then(function (text) {
        state.busy = false;
        if (text === null || text === undefined) return setStatus('Import cancelled.');
        finish(game.importSave(text, row.slot), 'Imported into ' + row.label);
      }, function (e) {
        state.busy = false;
        setStatus('Import failed: ' + msg(e));
      });
    }

    // Entry point for every key operation: asks first when the step is destructive, otherwise runs it.
    function request(op) {
      var row = rows()[state.cursor];
      if ((op === 'save' || op === 'import') && row.slot === AUTO_SLOT) {
        return setStatus('The auto save is written by the game; pick slot 1-3.');
      }
      if (needsConfirm(op, row)) {
        state.confirm = { op: op };
        return setStatus(confirmText(state.confirm, row));
      }
      run(op, row);
    }

    /** Prepares the screen for `mode` ('load' | 'save' | 'import'). Call before screens.open('saveload'). */
    function start(mode) {
      if (MODES.indexOf(mode) < 0) throw new Error('saveload: unknown mode ' + String(mode));
      state.mode = mode;
      state.cursor = 0;
      state.confirm = null;
      state.status = '';
    }

    function onKey(code, action) {
      if (state.busy) return true;
      if (state.confirm) {
        var pending = state.confirm;
        state.confirm = null;
        if (code === KEY.primary) {
          run(pending.op, rows()[state.cursor]);
          return true;
        }
        setStatus('Cancelled.');
        return true;
      }
      if (action === 'moveUp' || action === 'moveDown') {
        state.cursor = wrap(state.cursor + (action === 'moveUp' ? -1 : 1), SLOTS.length);
        return true;
      }
      if (code === KEY.primary) { request(state.mode); return true; }
      if (code === KEY.export) { request('export'); return true; }
      if (code === KEY.import) { request('import'); return true; }
      if (code === KEY.remove) { request('delete'); return true; }
      return action !== 'pause'; // Esc/P falls through so the manager closes this screen
    }

    function buttonRect(i) {
      return { x: layout.x + PANEL_PAD + i * (BUTTON_W + BUTTON_GAP), y: layout.y + layout.h - PANEL_PAD - BUTTON_ABOVE_BOTTOM,
        w: BUTTON_W, h: BUTTON_H };
    }

    function inRect(r, x, y) { return x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h; }

    function rowAt(x, y) {
      for (var i = 0; i < SLOTS.length; i++) {
        var top = layout.y + LIST_TOP + i * ROW_H - 4;
        if (inRect({ x: layout.x + PANEL_PAD - 4, y: top, w: layout.w - PANEL_PAD * 2 + 8, h: ROW_H - 4 }, x, y)) return i;
      }
      return -1;
    }

    // A press on a slot selects it and runs the mode's action (Enter); Export/Import/Delete act on the selected slot.
    // While a question is open, a press on the selected slot answers yes (Enter) and anything else cancels.
    function onPointer(x, y) {
      if (y >= layout.y + layout.h - FOOTER_H) return false;
      var row = rowAt(x, y);
      if (state.busy || state.confirm) {
        onKey(row === state.cursor ? KEY.primary : '', null);
        return true;
      }
      if (row >= 0) { state.cursor = row; request(state.mode); return true; }
      for (var b = 0; b < BUTTONS.length; b++) {
        if (inRect(buttonRect(b), x, y)) { request(BUTTONS[b].op); break; }
      }
      return true;
    }

    function drawButtons(ctx) {
      ctx.font = LABEL_FONT;
      BUTTONS.forEach(function (btn, i) {
        var r = buttonRect(i);
        ctx.strokeStyle = DIM_COLOR;
        ctx.lineWidth = 1;
        ctx.strokeRect(r.x, r.y, r.w, r.h);
        ctx.fillStyle = TEXT_COLOR;
        ctx.fillText(btn.label, r.x + 10, r.y + 16);
      });
    }

    function drawRows(ctx, list) {
      var x = layout.x + PANEL_PAD;
      ctx.font = LABEL_FONT;
      list.forEach(function (row, i) {
        var y = layout.y + LIST_TOP + i * ROW_H;
        if (i === state.cursor) {
          ctx.strokeStyle = CURSOR_COLOR;
          ctx.lineWidth = 2;
          ctx.strokeRect(x - 4, y - 4, layout.w - PANEL_PAD * 2 + 8, ROW_H - 4);
        }
        ctx.fillStyle = row.corrupt ? BAD_COLOR : row.empty ? DIM_COLOR : TEXT_COLOR;
        ctx.fillText(rowText(row), x, y + 16);
      });
    }

    function draw(ctx) {
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
      var x = layout.x + PANEL_PAD, bottom = layout.y + layout.h - PANEL_PAD;
      ctx.fillStyle = PANEL_COLOR;
      ctx.fillRect(layout.x, layout.y, layout.w, layout.h);
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = TITLE_FONT;
      ctx.fillText(TITLES[state.mode], x, layout.y + PANEL_PAD + 18);
      if (!storage.available()) {
        ctx.fillStyle = WARN_COLOR;
        ctx.font = LABEL_FONT;
        ctx.fillText(MEMORY_WARNING, x, layout.y + PANEL_PAD + 42);
      }
      drawRows(ctx, rows());
      drawButtons(ctx);
      ctx.font = LABEL_FONT;
      ctx.fillStyle = state.confirm ? WARN_COLOR : TEXT_COLOR;
      ctx.fillText(state.status, x, bottom - 36);
      ctx.fillStyle = DIM_COLOR;
      ctx.fillText('Up/Down choose | Enter/click ' + PRIMARY_LABEL[state.mode] + ' | X export | G import | D delete | Esc/tap here back',
        x, bottom);
    }

    return {
      id: 'saveload',
      start: start,
      draw: draw,
      onKey: onKey,
      onPointer: onPointer,
      rows: rows
    };
  }

  RPG.ui.saveload = {
    SLOTS: SLOTS,
    MODES: MODES,
    createScreen: createScreen,
    rowsFor: rowsFor,
    formatSaved: formatSaved,
    formatPlaytime: formatPlaytime
  };
})(globalThis.RPG = globalThis.RPG || {});

/*
 * Settings model (INTEGRATION-PLAN §3.3, §4; W4-13c). Headless: no DOM, no canvas. It owns the values the Settings
 * screen edits and pushes the ones that matter at runtime into the audio and input layers.
 *
 * Settings live under their own storage key, so *Delete save* (which only removes a save slot) keeps them. The
 * persisted document is the plain object `snapshot()` returns: non-default key bindings only, as SAVE-SCHEMA asks.
 *
 * create({ storage?, input?, audio?, keymap?, difficulty?, data? }) -> settings
 *   storage     localStorage-shaped { getItem, setItem, removeItem }; omitted -> in-memory map
 *   input       RPG.input instance with setBinding/resetBindings; receives every key change
 *   audio       RPG.audio-shaped { setVolume(master, sfx), mute(on) }; receives volume/mute changes
 */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var STORAGE_KEY = 'offline-rpg:v1:settings';
  var FORMAT_VERSION = 1;
  var QUALITY_LEVELS = Object.freeze(['low', 'med', 'high']);
  var DEFAULT_QUALITY = 'high';
  var DEFAULT_VOLUME = 0.5;
  var DEFAULT_SFX = 0.8;
  var DEFAULT_DIFFICULTY = 'normal';
  var CUSTOM_ID = 'custom';

  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function isUnit(v) { return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1; }

  function memoryBackend() {
    var m = new Map();
    return {
      getItem: function (k) { return m.has(k) ? m.get(k) : null; },
      setItem: function (k, v) { m.set(k, String(v)); },
      removeItem: function (k) { m.delete(k); }
    };
  }

  function create(options) {
    var opts = options || {};
    var backend = opts.storage || memoryBackend();
    var input = opts.input || null;
    var audio = opts.audio || null;
    var keymap = opts.keymap || (RPG.input && RPG.input.keymap);
    var difficulty = opts.difficulty || RPG.world.difficulty;
    var presets = (opts.data && opts.data.difficulties) || RPG.data.difficulties;

    var state = {
      volume: DEFAULT_VOLUME,
      sfx: DEFAULT_SFX,
      muted: false,
      quality: DEFAULT_QUALITY,
      showDamageNumbers: true,
      screenShake: true,
      difficultyId: DEFAULT_DIFFICULTY,
      // The custom config is kept even while a preset is selected, so switching back restores the sliders.
      custom: null,
      bindings: keymap.defaultBindings()
    };

    function defaultCustom() {
      return difficulty.clampCustom({});
    }

    function pushAudio() {
      if (!audio) return;
      if (audio.setVolume) audio.setVolume(state.volume, state.sfx);
      if (audio.mute) audio.mute(state.muted);
    }

    function pushBinding(action) {
      if (input && input.setBinding) input.setBinding(action, state.bindings[action][0]);
    }

    // Keys that differ from the defaults; this is what a save or the storage key records.
    function keyOverrides() {
      var defaults = keymap.defaultBindings();
      var out = {};
      keymap.ACTIONS.forEach(function (action) {
        var now = state.bindings[action];
        if (JSON.stringify(now) !== JSON.stringify(defaults[action])) out[action] = now.slice();
      });
      return out;
    }

    // ---- audio and display --------------------------------------------------------------------------------------

    function setVolume(v) {
      assertUnit('volume', v);
      state.volume = v;
      pushAudio();
    }

    function setSfx(v) {
      assertUnit('sfx', v);
      state.sfx = v;
      pushAudio();
    }

    function setMuted(on) {
      state.muted = !!on;
      pushAudio();
    }

    function setQuality(level) {
      if (QUALITY_LEVELS.indexOf(level) === -1) {
        throw new Error('quality must be one of ' + QUALITY_LEVELS.join(', ') + ', got ' + level);
      }
      state.quality = level;
    }

    function setShowDamageNumbers(on) { state.showDamageNumbers = !!on; }
    function setScreenShake(on) { state.screenShake = !!on; }

    function assertUnit(name, v) {
      if (!isUnit(v)) throw new Error(name + ' must be a number in [0, 1], got ' + v);
    }

    // ---- key bindings -------------------------------------------------------------------------------------------

    /**
     * Binds `action` to `code`. A key does one thing, so a code already bound elsewhere is never taken silently:
     *   - default ('reject'): nothing changes and the result names the action that holds the key;
     *   - { swap: true }: the other action takes over the key this action had before (or is unbound if it had none).
     * Returns { ok: true, swapped?: action } or { ok: false, reason: 'conflict', conflictsWith: action }.
     */
    function rebind(action, code, rebindOpts) {
      if (!keymap.isAction(action)) throw new Error('unknown input action ' + action);
      var swap = !!(rebindOpts && rebindOpts.swap);
      var holder = null;
      keymap.ACTIONS.forEach(function (other) {
        if (other !== action && state.bindings[other].indexOf(code) !== -1) holder = other;
      });
      if (holder && !swap) return { ok: false, reason: 'conflict', conflictsWith: holder };

      // Every action keeps at least one key (defaults are non-empty and rebinding always sets one), so the key the
      // action gives up is always there to hand to the holder.
      var previous = state.bindings[action][0];
      state.bindings = keymap.withBinding(state.bindings, action, code);
      pushBinding(action);
      if (!holder) return { ok: true };

      var next = keymap.copyBindings(state.bindings);
      next[holder] = [previous];
      state.bindings = next;
      pushBinding(holder);
      return { ok: true, swapped: holder };
    }

    function resetKeys() {
      state.bindings = keymap.defaultBindings();
      if (input && input.resetBindings) input.resetBindings();
    }

    // ---- difficulty ----------------------------------------------------------------------------------------------

    /** Selects a preset by id (easy, normal, hard, nightmare, or custom). Unknown ids throw. */
    function setDifficultyPreset(id) {
      difficulty.resolve(id);
      state.difficultyId = id;
    }

    /**
     * Sets one custom slider. The value is clamped to the slider range by clampCustom, so the stored config is always
     * inside the ranges. Selecting custom happens here as well.
     */
    function setCustomField(field, value) {
      var ranges = presets.custom.ranges;
      if (!Object.prototype.hasOwnProperty.call(ranges, field)) {
        throw new Error('no custom difficulty slider named ' + field);
      }
      var base = state.custom || defaultCustom();
      var next = {};
      next[field] = value;
      state.custom = difficulty.clampCustom(Object.assign({}, base, next));
      state.difficultyId = CUSTOM_ID;
      return state.custom;
    }

    /** The resolved config for the current selection (a preset, or the clamped custom config). */
    function currentDifficulty() {
      if (state.difficultyId === CUSTOM_ID) return difficulty.resolve(state.custom || defaultCustom());
      return difficulty.resolve(state.difficultyId);
    }

    /** The clamped custom config, whether or not custom is the selected preset (what the custom sliders show). */
    function customConfig() {
      return Object.assign({}, state.custom || defaultCustom());
    }

    function customRanges() {
      return presets.custom.ranges;
    }

    // ---- persistence ---------------------------------------------------------------------------------------------

    /** The plain settings document (what goes into a save's `settings` field and the settings key). */
    function snapshot() {
      return {
        v: FORMAT_VERSION,
        volume: state.volume,
        sfx: state.sfx,
        muted: state.muted,
        quality: state.quality,
        showDamageNumbers: state.showDamageNumbers,
        screenShake: state.screenShake,
        difficulty: state.difficultyId,
        custom: state.custom ? Object.assign({}, state.custom) : null,
        keys: keyOverrides()
      };
    }

    /** Writes the snapshot under STORAGE_KEY. Returns { ok: true } or { ok: false, error }; never throws. */
    function save() {
      try {
        backend.setItem(STORAGE_KEY, JSON.stringify(snapshot()));
        return { ok: true };
      } catch (e) {
        return { ok: false, error: e && e.message ? e.message : String(e) };
      }
    }

    /**
     * Reads the stored document and applies every field that is valid. A missing, unparsable or wrong-shaped field
     * keeps its default, so a damaged settings key never blocks the game. Returns { ok, applied, warnings }.
     */
    function load() {
      var raw = null;
      try { raw = backend.getItem(STORAGE_KEY); } catch (e) { return { ok: false, applied: false, warnings: [String(e)] }; }
      if (raw === null || raw === undefined) return { ok: true, applied: false, warnings: [] };
      var doc;
      try { doc = JSON.parse(raw); } catch (e) { return { ok: false, applied: false, warnings: ['settings are not valid JSON'] }; }
      if (!isObj(doc)) return { ok: false, applied: false, warnings: ['settings must be an object'] };

      var warnings = [];
      function field(name, check, apply) {
        if (doc[name] === undefined) return;
        if (!check(doc[name])) { warnings.push(name + ' ignored'); return; }
        apply(doc[name]);
      }
      field('volume', isUnit, function (v) { state.volume = v; });
      field('sfx', isUnit, function (v) { state.sfx = v; });
      field('muted', function (v) { return typeof v === 'boolean'; }, function (v) { state.muted = v; });
      field('quality', function (v) { return QUALITY_LEVELS.indexOf(v) !== -1; }, function (v) { state.quality = v; });
      field('showDamageNumbers', function (v) { return typeof v === 'boolean'; }, function (v) { state.showDamageNumbers = v; });
      field('screenShake', function (v) { return typeof v === 'boolean'; }, function (v) { state.screenShake = v; });
      field('difficulty', function (v) { return typeof v === 'string' && !!presets[v]; }, function (v) { state.difficultyId = v; });
      field('custom', isObj, function (v) {
        try { state.custom = difficulty.clampCustom(v); } catch (e) { warnings.push('custom ignored'); }
      });
      field('keys', isObj, function (v) {
        var next = keymap.defaultBindings();
        Object.keys(v).forEach(function (action) {
          var codes = v[action];
          if (!keymap.isAction(action) || !Array.isArray(codes) || codes.length === 0) {
            warnings.push('key ' + action + ' ignored');
            return;
          }
          next = keymap.withBinding(next, action, codes[0]);
        });
        state.bindings = next;
      });

      pushAudio();
      if (input && input.setBinding) keymap.ACTIONS.forEach(pushBinding);
      return { ok: true, applied: true, warnings: warnings };
    }

    // Apply the initial state to the audio and input layers so they agree with what the screen shows.
    pushAudio();

    return {
      STORAGE_KEY: STORAGE_KEY,
      setVolume: setVolume,
      setSfx: setSfx,
      setMuted: setMuted,
      setQuality: setQuality,
      setShowDamageNumbers: setShowDamageNumbers,
      setScreenShake: setScreenShake,
      rebind: rebind,
      resetKeys: resetKeys,
      bindingsOf: function (action) { return state.bindings[action].slice(); },
      setDifficultyPreset: setDifficultyPreset,
      setCustomField: setCustomField,
      currentDifficulty: currentDifficulty,
      customConfig: customConfig,
      customRanges: customRanges,
      snapshot: snapshot,
      save: save,
      load: load
    };
  }

  // ---- settings screen (canvas, id 'settings') ----------------------------------------------------------------------
  // Draws and edits the model above; no DOM. Two columns: general options and custom sliders on the left, key
  // bindings on the right. Every change is saved at once, so closing the screen needs no extra step.

  var SCREEN_LAYOUT = Object.freeze({ x: 0, y: 0, w: 640, h: 480 });
  var PANEL_PAD = 16;
  var TITLE_BASELINE = 28;
  var ROWS_TOP = 52;
  var ROW_H = 15;
  var COL_W = 300;
  var VALUE_OFFSET = 150;
  var FOOTER_H = 24; // presses in this bottom strip fall through to the manager as "back"
  var ROW_BASELINE = 11; // a row's hit box starts this far above its text baseline
  var VOLUME_STEP = 0.05;
  var CUSTOM_STEPS = 20; // one Left/Right press moves a custom slider by 1/20 of its range
  var PANEL_COLOR = 'rgba(16, 14, 20, 0.95)';
  var TEXT_COLOR = '#d8d2c4';
  var DIM_COLOR = '#5f5a4f';
  var CURSOR_COLOR = '#e8c15a';
  var TITLE_FONT = '16px system-ui, sans-serif';
  var LABEL_FONT = '12px system-ui, sans-serif';

  var CUSTOM_LABEL = Object.freeze({
    enemyHp: 'Enemy HP', enemyDamage: 'Enemy damage', enemySpeed: 'Enemy speed', spawnDensity: 'Spawn density',
    eliteChance: 'Elite chance', bossHp: 'Boss HP', bossTelegraph: 'Boss telegraph', lootQuality: 'Loot quality',
    xpMultiplier: 'XP multiplier', goldMultiplier: 'Gold multiplier'
  });

  function wrap(i, n) { return (i + n) % n; }
  function onOff(v) { return v ? 'On' : 'Off'; }
  function percent(v) { return Math.round(v * 100) + '%'; }
  function round2(v) { return Math.round(v * 100) / 100; }
  function clamp01(v) { return Math.min(1, Math.max(0, v)); }
  function cycle(list, current, dir) { return list[wrap(list.indexOf(current) + dir, list.length)]; }

  // 'moveUp' -> 'Move up', 'skill6' -> 'Skill 6'.
  function actionLabel(action) {
    var spaced = action.replace(/([A-Z0-9])/g, ' $1').toLowerCase();
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
  }

  function buildRows(settings) {
    var general = [
      { kind: 'volume', label: 'Master volume' },
      { kind: 'sfx', label: 'Sound effects' },
      { kind: 'muted', label: 'Mute' },
      { kind: 'quality', label: 'Quality' },
      { kind: 'damageNumbers', label: 'Damage numbers' },
      { kind: 'screenShake', label: 'Screen shake' },
      { kind: 'difficulty', label: 'Difficulty' }
    ];
    Object.keys(settings.customRanges()).forEach(function (field) {
      general.push({ kind: 'custom', field: field, label: CUSTOM_LABEL[field] || field });
    });
    var keys = RPG.input.keymap.ACTIONS.map(function (action) {
      return { kind: 'key', action: action, label: actionLabel(action) };
    });
    keys.push({ kind: 'resetKeys', label: 'Reset keys to defaults' });
    return { general: general, all: general.concat(keys) };
  }

  var VALUE = Object.freeze({
    volume: function (s) { return percent(s.volume); },
    sfx: function (s) { return percent(s.sfx); },
    muted: function (s) { return onOff(s.muted); },
    quality: function (s) { return s.quality; },
    damageNumbers: function (s) { return onOff(s.showDamageNumbers); },
    screenShake: function (s) { return onOff(s.screenShake); },
    difficulty: function (s) { return RPG.data.difficulties[s.difficulty].name; }
  });

  /**
   * Creates the Settings screen. opts: { settings, layout? }; `settings` is a model from create(). Returns the
   * definition for screens.register(). Esc is not consumed, so the manager closes the screen.
   */
  function createScreen(opts) {
    var settings = opts.settings;
    var layout = opts.layout || SCREEN_LAYOUT;
    var rows = buildRows(settings);
    var presetIds = Object.keys(RPG.data.difficulties);
    var state = { cursor: 0, status: '', capture: null };

    function setStatus(text) { state.status = text; }

    // A failed write is reported in the status line and never blocks play.
    function persist() {
      var r = settings.save();
      if (!r.ok) setStatus('Could not save settings: ' + r.error);
    }

    function valueText(row) {
      if (row.kind === 'custom') return settings.customConfig()[row.field].toFixed(2);
      if (row.kind === 'key') {
        return state.capture === row.action ? 'press a key...' : settings.bindingsOf(row.action).join(' / ');
      }
      if (row.kind === 'resetKeys') return '';
      return VALUE[row.kind](settings.snapshot());
    }

    function toggle(row) {
      var s = settings.snapshot();
      if (row.kind === 'muted') settings.setMuted(!s.muted);
      else if (row.kind === 'damageNumbers') settings.setShowDamageNumbers(!s.showDamageNumbers);
      else settings.setScreenShake(!s.screenShake);
    }

    // Left/Right: step a slider, flip a toggle or cycle a choice. Enter does the same, but cycles choices forward.
    function change(row, dir) {
      var s = settings.snapshot();
      if (row.kind === 'volume') settings.setVolume(clamp01(round2(s.volume + dir * VOLUME_STEP)));
      else if (row.kind === 'sfx') settings.setSfx(clamp01(round2(s.sfx + dir * VOLUME_STEP)));
      else if (row.kind === 'quality') settings.setQuality(cycle(QUALITY_LEVELS, s.quality, dir));
      else if (row.kind === 'difficulty') settings.setDifficultyPreset(cycle(presetIds, s.difficulty, dir));
      else if (row.kind === 'custom') {
        var range = settings.customRanges()[row.field];
        var step = (range[1] - range[0]) / CUSTOM_STEPS;
        settings.setCustomField(row.field, round2(settings.customConfig()[row.field] + dir * step));
      } else if (row.kind === 'key' || row.kind === 'resetKeys') return;
      else toggle(row);
      setStatus('');
      persist();
    }

    function activate(row) {
      if (row.kind === 'key') {
        state.capture = row.action;
        setStatus('Press a key for ' + row.label + '. Esc cancels.');
      } else if (row.kind === 'resetKeys') {
        settings.resetKeys();
        setStatus('Keys reset to defaults.');
        persist();
      } else if (row.kind !== 'volume' && row.kind !== 'sfx' && row.kind !== 'custom') {
        change(row, 1); // sliders only respond to Left/Right
      }
    }

    // The next key is the new binding. A key another action holds is refused and capture stays open for another key.
    function finishCapture(code) {
      var action = state.capture;
      if (code === 'Escape') {
        state.capture = null;
        setStatus('Rebinding cancelled.');
        return;
      }
      var r = settings.rebind(action, code);
      if (!r.ok) {
        setStatus(code + ' is already used by ' + actionLabel(r.conflictsWith) + '. Press another key or Esc.');
        return;
      }
      state.capture = null;
      setStatus(actionLabel(action) + ' is now ' + code + '.');
      persist();
    }

    function onKey(code, action) {
      if (state.capture) {
        finishCapture(code);
        return true;
      }
      if (action === 'moveUp' || action === 'moveDown') {
        state.cursor = wrap(state.cursor + (action === 'moveUp' ? -1 : 1), rows.all.length);
        setStatus('');
        return true;
      }
      var row = rows.all[state.cursor];
      if (action === 'moveLeft' || action === 'moveRight') {
        change(row, action === 'moveLeft' ? -1 : 1);
        return true;
      }
      if (code === 'Enter') {
        activate(row);
        return true;
      }
      return action !== 'pause'; // Esc/P falls through so the manager closes the screen
    }

    function rowGeometry(i) {
      var right = i >= rows.general.length;
      var line = right ? i - rows.general.length : i;
      return { x: layout.x + PANEL_PAD + (right ? COL_W : 0) - 4, y: layout.y + ROWS_TOP + line * ROW_H - ROW_BASELINE };
    }

    function rowAt(x, y) {
      for (var i = 0; i < rows.all.length; i++) {
        var g = rowGeometry(i);
        if (x >= g.x && x < g.x + COL_W - 16 && y >= g.y && y < g.y + ROW_H) return i;
      }
      return -1;
    }

    // A press selects the row and does what Enter does. Sliders have no Enter action, so their left half steps down and
    // their right half up (like Left/Right). During key capture a press cancels it. The footer is "back".
    function onPointer(x, y) {
      if (y >= layout.y + layout.h - FOOTER_H) return false;
      if (state.capture) { finishCapture('Escape'); return true; }
      var i = rowAt(x, y);
      if (i < 0) return true;
      state.cursor = i;
      setStatus('');
      var row = rows.all[i];
      if (row.kind === 'volume' || row.kind === 'sfx' || row.kind === 'custom') {
        change(row, x < rowGeometry(i).x + (COL_W - 16) / 2 ? -1 : 1);
      } else {
        activate(row);
      }
      return true;
    }

    function draw(ctx) {
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
      var x = layout.x + PANEL_PAD;
      ctx.fillStyle = PANEL_COLOR;
      ctx.fillRect(layout.x, layout.y, layout.w, layout.h);
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = TITLE_FONT;
      ctx.fillText('Settings', x, layout.y + TITLE_BASELINE);
      ctx.font = LABEL_FONT;
      rows.all.forEach(function (row, i) {
        var right = i >= rows.general.length;
        var cx = x + (right ? COL_W : 0);
        var line = right ? i - rows.general.length : i;
        var y = layout.y + ROWS_TOP + line * ROW_H;
        if (i === state.cursor) {
          ctx.strokeStyle = CURSOR_COLOR;
          ctx.lineWidth = 1;
          ctx.strokeRect(cx - 4, y - 11, COL_W - 16, ROW_H);
        }
        ctx.fillStyle = TEXT_COLOR;
        ctx.fillText(row.label, cx, y);
        ctx.fillText(valueText(row), cx + VALUE_OFFSET, y);
      });
      ctx.fillStyle = TEXT_COLOR;
      ctx.fillText(state.status, x, layout.y + layout.h - 40);
      ctx.fillStyle = DIM_COLOR;
      ctx.fillText('Up/Down choose | Left/Right adjust | Enter select | click rows | Esc/tap here back', x, layout.y + layout.h - 16);
    }

    return { id: 'settings', draw: draw, onKey: onKey, onPointer: onPointer };
  }

  RPG.ui.settings = {
    create: create,
    createScreen: createScreen,
    STORAGE_KEY: STORAGE_KEY,
    QUALITY_LEVELS: QUALITY_LEVELS
  };
})(globalThis.RPG = globalThis.RPG || {});

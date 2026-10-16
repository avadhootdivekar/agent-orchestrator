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
      customRanges: customRanges,
      snapshot: snapshot,
      save: save,
      load: load
    };
  }

  RPG.ui.settings = { create: create, STORAGE_KEY: STORAGE_KEY, QUALITY_LEVELS: QUALITY_LEVELS };
})(globalThis.RPG = globalThis.RPG || {});

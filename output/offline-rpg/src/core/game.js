/* Game: state machine, save/load wiring, autosave and the per-tick glue between input, sim, ui and audio
   (docs/INTEGRATION-PLAN.md §3). Every collaborator is injectable so the whole thing runs headlessly in node with a
   fake storage, fake input and a manual tick(). Nothing touches browser globals at load time; start() is the only
   browser entry. States: mainmenu | playing | paused | dead | victory (§3.1). */
(function (RPG) {
  'use strict';

  var STATES = Object.freeze({ MAINMENU: 'mainmenu', PLAYING: 'playing', PAUSED: 'paused', DEAD: 'dead', VICTORY: 'victory' });
  var SLOT_AUTO = 'auto';
  var SLOT_QUICK = '1';
  var AUTOSAVE_INTERVAL = 60;   // seconds of world.time between timed autosaves
  var AUTOSAVE_CALM = 5;        // seconds with no monster in aggro range before a timed autosave is allowed
  var DEFAULT_AGGRO_RANGE = 300;
  var FINAL_BOSS_ID = 'warden-of-ash';
  var START_AREA = 'town';
  var DEFAULT_DIFFICULTY = 'normal';
  var DEFAULT_NAME = 'Wayfarer';
  var ERROR_EVENT = 'gameError';
  var OVERLAY_TOGGLES = { inventory: 'inventory', character: 'character', skills: 'skills', settings: 'settings', saveMenu: 'saveload' };
  var DEFAULT_SETTINGS = Object.freeze({ volume: 0.5, sfx: 0.8, muted: false, quality: 'high', showDamageNumbers: true, screenShake: 1, keys: {} });

  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function fail(code, error) { return { ok: false, code: code, error: error }; }
  function msg(e) { return e && e.message ? e.message : String(e); }

  function emptyIntent() {
    return { move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} };
  }

  /**
   * create(deps) -> game. deps (all optional but `canvas`-free): { events, persistence, sim, input, renderer, screens, audio,
   * storage (key-value backend for persistence.saveGame/loadGame), difficulty, areas, rng, clock, now }.
   * Missing collaborators default to the RPG.* globals where they exist; absent presentation layers are simply skipped.
   */
  function create(deps) {
    deps = deps || {};
    var events = deps.events || RPG.core.events;
    var persistence = deps.persistence || RPG.persistence;
    var sim = deps.sim || RPG.sim;
    var difficulty = deps.difficulty || (RPG.world && RPG.world.difficulty) || null;
    var areas = deps.areas || (RPG.world && RPG.world.areas) || null;
    var Rng = deps.rng || (RPG.core && RPG.core.Rng) || null;
    var clock = deps.clock || null;
    var input = deps.input || null, renderer = deps.renderer || null, screens = deps.screens || null, audio = deps.audio || null;
    var storageOpts = deps.storage ? { storage: deps.storage } : undefined;
    var NAMES = events.NAMES;

    var offs = [];
    var loop = null;
    var pendingDeath = false;
    var lastAutosaveAt = 0, calmFor = 0, bossCount = 0;

    var game = {
      state: STATES.MAINMENU, world: null, settings: cloneSettings(deps.settings), difficultyId: DEFAULT_DIFFICULTY,
      playerName: DEFAULT_NAME, lastError: null, lastWarnings: [], stepErrors: 0, victoryShown: false
    };

    function cloneSettings(s) {
      var out = JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
      if (isObj(s)) Object.keys(s).forEach(function (k) { out[k] = s[k]; });
      return out;
    }

    function setError(code, error) { game.lastError = { code: code, error: error }; return game.lastError; }

    // ---- state machine ------------------------------------------------------------------------------------------------

    function setState(next) {
      if (game.state === next) return;
      game.state = next;
      if (screens) {
        if (next === STATES.MAINMENU) screens.open('mainmenu');
        else if (next === STATES.PLAYING) { screens.open('playing'); }
        else if (next === STATES.PAUSED && !screens.paused()) screens.open('pause');
      }
      if (next === STATES.PLAYING && loop) loop.reset(); // no catch-up burst after a pause/load
    }

    /** True when the simulation may advance: `playing` with no modal overlay open. */
    function shouldStep() {
      return game.state === STATES.PLAYING && !!game.world && !(screens && screens.paused());
    }

    function setPaused(on) {
      if (on) {
        if (game.state !== STATES.PLAYING) return;
        setState(STATES.PAUSED);
      } else if (game.state === STATES.PAUSED) {
        if (screens) while (screens.paused() && screens.close()) { /* close every overlay */ }
        setState(STATES.PLAYING);
      }
    }

    function respawn() {
      if (game.state !== STATES.DEAD || !game.world) return fail('state', 'respawn is only valid while dead');
      var w = game.world, p = w.player;
      pendingDeath = false;
      p.dead = false;
      p.hp = p.maxHp || (p.stats && p.stats.maxHp) || p.hp;
      if (areas && typeof areas.enter === 'function') {
        try { areas.enter(w, START_AREA); } catch (e) { setError('area', msg(e)); }
      }
      if (renderer && renderer.snapTo) renderer.snapTo(w);
      setState(STATES.PLAYING);
      return { ok: true };
    }

    /** Leaves victory for free play (the screen's "Keep playing"). */
    function continuePlaying() {
      if (game.state !== STATES.VICTORY) return fail('state', 'continue is only valid on victory');
      setState(STATES.PLAYING);
      return { ok: true };
    }

    function quitToMenu() {
      game.world = null;
      pendingDeath = false;
      setState(STATES.MAINMENU);
    }

    // ---- world lifecycle ------------------------------------------------------------------------------------------------

    function resolveDifficulty(d) {
      if (!difficulty) return typeof d === 'string' ? { id: d } : d;
      return difficulty.resolve(d);
    }

    function seedOf(seed) {
      if (typeof seed === 'string') return Rng ? Rng.seedFromString(seed) : 1;
      if (typeof seed === 'number' && isFinite(seed)) return seed >>> 0;
      // Default only at the UI edge: the single place wall-clock time may pick a seed.
      return clock ? Math.floor(clock.now() * 1000) >>> 0 : 1;
    }

    function adopt(world) {
      game.world = world;
      pendingDeath = false;
      game.victoryShown = !!(world.progress && world.progress.bossesDefeated && world.progress.bossesDefeated[FINAL_BOSS_ID]);
      bossCount = countBosses(world);
      lastAutosaveAt = world.time || 0;
      calmFor = 0;
      if (renderer && renderer.snapTo) renderer.snapTo(world);
    }

    function countBosses(w) {
      return w && w.progress && w.progress.bossesDefeated ? Object.keys(w.progress.bossesDefeated).length : 0;
    }

    /** opts: { seed (uint32|string), difficulty (id|config), name }. */
    function newGame(opts) {
      opts = opts || {};
      var diff = resolveDifficulty(opts.difficulty === undefined ? DEFAULT_DIFFICULTY : opts.difficulty);
      var world = sim.createWorld({ seed: seedOf(opts.seed), difficulty: diff });
      world.difficulty = diff;
      if (areas && typeof areas.enter === 'function' && RPG.data && RPG.data.areas && RPG.data.areas[START_AREA]) {
        areas.enter(world, START_AREA);
      }
      if (opts.name && world.player) world.player.name = opts.name;
      game.difficultyId = diff.id;
      game.playerName = opts.name || DEFAULT_NAME;
      game.lastError = null;
      adopt(world);
      setState(STATES.PLAYING);
      return { ok: true };
    }

    function setDifficulty(idOrConfig) {
      try {
        var d = resolveDifficulty(idOrConfig);
        game.difficultyId = d.id;
        if (game.world) game.world.difficulty = d;
        return { ok: true };
      } catch (e) { return fail('difficulty', msg(e)); }
    }

    // ---- save / load ------------------------------------------------------------------------------------------------

    function buildSnapshot() {
      return persistence.snapshot(game.world, { settings: game.settings, name: game.playerName });
    }

    function saveToSlot(slot) {
      slot = String(slot);
      if (!game.world || game.state === STATES.MAINMENU) return fail('state', 'no game to save');
      if (game.state === STATES.DEAD) return fail('state', 'cannot save while dead');
      var r;
      try {
        r = persistence.saveGame(slot, buildSnapshot(), storageOpts);
      } catch (e) { r = fail(e && e.code || 'shape', msg(e)); }
      if (!r.ok) setError(r.code || 'storage', r.error);
      events.emit(NAMES.SAVE, { slot: slot, ok: !!r.ok, error: r.ok ? undefined : r.error });
      return r.ok ? { ok: true } : { ok: false, code: r.code, error: r.error };
    }

    function loadFromSlot(slot) {
      slot = String(slot);
      var r;
      try {
        r = persistence.loadGame(slot, storageOpts);
        if (!r.ok) { setError(r.code, r.error); return fail(r.code, r.error); }
        r = persistence.restore(r.state);
      } catch (e) { r = fail(e && e.code || 'shape', msg(e)); }
      if (!r.ok) { setError(r.code, r.error); return fail(r.code, r.error); }
      // Restored world replaces the current one only now that every check passed.
      adopt(r.world);
      game.lastWarnings = r.warnings || [];
      if (r.game) {
        if (r.game.difficulty) game.difficultyId = r.game.difficulty.id || game.difficultyId;
        if (r.game.name) game.playerName = r.game.name;
        if (r.game.settings && Object.keys(r.game.settings).length) applySettings(r.game.settings);
      }
      setState(STATES.PLAYING);
      if (screens) { while (screens.close()) { /* drop leftover overlays */ } }
      events.emit(NAMES.LOAD, { slot: slot, warnings: game.lastWarnings });
      return { ok: true, warnings: game.lastWarnings };
    }

    function quickSave() { return saveToSlot(SLOT_QUICK); }
    function quickLoad() { return loadFromSlot(SLOT_QUICK); }

    /** slot | undefined/'current' -> {ok, filename, text}. Download/Blob lives in persistence/files.js. */
    function exportSave(slot) {
      try {
        var state;
        if (slot === undefined || slot === null || slot === 'current') {
          if (!game.world) return fail('state', 'no game to export');
          state = buildSnapshot();
        } else {
          var r = persistence.loadGame(String(slot), storageOpts);
          if (!r.ok) return fail(r.code, r.error);
          state = r.state;
        }
        return { ok: true, filename: persistence.exportFileName(state), text: persistence.exportJson(state) };
      } catch (e) { return fail(e && e.code || 'shape', msg(e)); }
    }

    /** importJson -> saveGame(slot) -> loadFromSlot. The slot defaults to '1' (never 'auto'). */
    function importSave(text, slot) {
      slot = slot === undefined ? SLOT_QUICK : String(slot);
      var r;
      try {
        r = persistence.importJson(text);
        if (!r.ok) return fail(r.code, r.error);
        var w = persistence.saveGame(slot, r.state, storageOpts);
        if (!w.ok) return fail(w.code, w.error);
      } catch (e) { return fail(e && e.code || 'shape', msg(e)); }
      return loadFromSlot(slot);
    }

    function deleteSave(slot) {
      try {
        var r = persistence.deleteSave(String(slot), storageOpts);
        return r.ok ? { ok: true } : { ok: false, error: r.error };
      } catch (e) { return { ok: false, error: msg(e) }; }
    }

    // ---- autosave ------------------------------------------------------------------------------------------------

    /** Writes slot 'auto'. Skipped (returns null) while dead, paused, in a menu, or without a world. Never throws. */
    function autosave(reason) {
      if (game.state !== STATES.PLAYING && game.state !== STATES.VICTORY) return null;
      if (!game.world || pendingDeath) return null;
      lastAutosaveAt = game.world.time || 0;
      var r = saveToSlot(SLOT_AUTO);
      r.reason = reason;
      return r;
    }

    function monsterNear(w) {
      var p = w.player;
      return (w.monsters || []).some(function (m) {
        if (m.dead) return false;
        var def = m.def || {};
        var range = (def.aiParams && def.aiParams.aggroRange) || DEFAULT_AGGRO_RANGE;
        var dx = m.x - p.x, dy = m.y - p.y;
        return dx * dx + dy * dy <= range * range;
      });
    }

    // ---- settings ------------------------------------------------------------------------------------------------

    function applySettings(s) {
      if (isObj(s)) Object.keys(s).forEach(function (k) { game.settings[k] = s[k]; });
      var st = game.settings;
      if (audio) {
        if (audio.setVolume) audio.setVolume(st.volume, st.sfx);
        if (audio.mute) audio.mute(!!st.muted);
      }
      if (input && isObj(st.keys) && input.setBinding) {
        Object.keys(st.keys).forEach(function (action) {
          var codes = st.keys[action];
          try { if (Array.isArray(codes) && codes.length) input.setBinding(action, codes[0]); } catch (e) { setError('settings', msg(e)); }
        });
      }
      if (renderer) renderer.settings = st;
      return game.settings;
    }

    // ---- per-frame ------------------------------------------------------------------------------------------------

    // The pause overlay can be closed by the screens manager itself (Esc); follow it back to `playing`.
    function syncPause() {
      if (game.state === STATES.PAUSED && screens && !screens.paused()) setState(STATES.PLAYING);
    }

    /** Consumes the global toggles before the sim sees the intent. Returns the sim-facing intent. */
    function handleToggles(intent) {
      var t = intent.toggles || {};
      if (t.pause) {
        if (game.state === STATES.PAUSED) setPaused(false);
        else if (screens && screens.paused()) screens.close();
        else setPaused(true);
      }
      Object.keys(OVERLAY_TOGGLES).forEach(function (name) {
        if (t[name] && screens && game.state !== STATES.MAINMENU) screens.toggle(OVERLAY_TOGGLES[name]);
      });
      if (t.quickSave) quickSave();
      if (t.quickLoad) quickLoad();
      if (t.debug && renderer) renderer.debug = !renderer.debug;
      // Held movement/attack must not leak through while an overlay is open.
      if (screens && screens.paused()) {
        intent.move = { x: 0, y: 0 };
        intent.attack = false;
        intent.dash = false;
        intent.skills = [];
      }
      return intent;
    }

    function sampleIntent() {
      return input && renderer ? input.buildFrame(renderer.camera, renderer.viewW, renderer.viewH) : emptyIntent();
    }

    function afterStep(dt) {
      var w = game.world;
      if (!w) return;
      if (w.progress) w.progress.playtime = (w.progress.playtime || 0) + dt;
      if (pendingDeath || w.player.dead || w.player.hp <= 0) {
        pendingDeath = false;
        setState(STATES.DEAD);
        return;
      }
      var bosses = countBosses(w);
      if (bosses > bossCount) { bossCount = bosses; autosave('boss'); }
      if (!game.victoryShown && w.progress.bossesDefeated && w.progress.bossesDefeated[FINAL_BOSS_ID]) {
        game.victoryShown = true;
        autosave('victory'); // before the state change, while still `playing`
        setState(STATES.VICTORY);
        return;
      }
      calmFor = monsterNear(w) ? 0 : calmFor + dt;
      if (calmFor >= AUTOSAVE_CALM && w.time - lastAutosaveAt >= AUTOSAVE_INTERVAL) autosave('timer');
    }

    /** One fixed step: intent -> RPG.sim.step. A throwing sim pauses the game and surfaces the error; the loop survives. */
    function tick(dt) {
      if (!game.world) return;
      syncPause();
      var intent = handleToggles(sampleIntent());
      if (!shouldStep()) return;
      try {
        sim.step(game.world, intent, dt);
      } catch (e) {
        game.stepErrors++;
        setError('sim', msg(e));
        events.emit(ERROR_EVENT, { code: 'sim', error: msg(e) });
        setPaused(true);
        return;
      }
      afterStep(dt);
    }

    function render(alpha) {
      // While not stepping (pause/overlay) the loop never calls tick(), so keep reading toggles here or Esc could not resume.
      syncPause();
      if (game.world && !shouldStep() && input && renderer) handleToggles(sampleIntent());
      if (renderer && game.world) renderer.draw(game.world, alpha, 0, { state: game.state, error: game.lastError });
      if (screens && deps.ctx) screens.draw(deps.ctx);
    }

    function visibilityChanged(hidden) {
      if (hidden) { if (game.state === STATES.PLAYING) setPaused(true); }
      if (loop) loop.reset();
    }

    // ---- events ------------------------------------------------------------------------------------------------

    offs.push(events.on(NAMES.PLAYER_DIED, function () { pendingDeath = true; }));
    offs.push(events.on(NAMES.AREA_CHANGED, function () { autosave('area'); }));
    offs.push(events.on(NAMES.LEVEL_UP, function () { autosave('level'); }));

    function destroy() { offs.forEach(function (off) { off(); }); offs = []; }

    game.STATES = STATES;
    game.newGame = newGame;
    game.loadFromSlot = loadFromSlot;
    game.saveToSlot = saveToSlot;
    game.quickSave = quickSave;
    game.quickLoad = quickLoad;
    game.exportSave = exportSave;
    game.importSave = importSave;
    game.deleteSave = deleteSave;
    game.setDifficulty = setDifficulty;
    game.setPaused = setPaused;
    game.respawn = respawn;
    game.continuePlaying = continuePlaying;
    game.quitToMenu = quitToMenu;
    game.applySettings = applySettings;
    game.autosave = autosave;
    game.shouldStep = shouldStep;
    game.tick = tick;
    game.render = render;
    game.visibilityChanged = visibilityChanged;
    game.attachLoop = function (l) { loop = l; };
    game.on = function (type, fn) { return events.on(type, fn); };
    game.destroy = destroy;
    applySettings();
    if (screens) screens.open('mainmenu');
    return game;
  }

  /** Browser entry used by main.js: builds the real collaborators, wires the loop, returns the game. */
  function start(canvas) {
    var renderer = RPG.render.create(canvas);
    var input = RPG.input.create();
    var screens = RPG.ui.screens.create();
    var game = create({
      renderer: renderer, input: input, screens: screens, audio: RPG.audio, ctx: canvas.getContext('2d'),
      clock: { now: function () { return Date.now() / 1000; } }
    });
    function resize() { renderer.resize(canvas.clientWidth, canvas.clientHeight, globalThis.devicePixelRatio || 1); }
    resize();
    input.attach(globalThis, canvas);
    globalThis.addEventListener('resize', resize);
    // Autoplay policy: audio starts on the first user gesture, never at load (§3.4).
    function firstGesture() {
      globalThis.removeEventListener('keydown', firstGesture);
      globalThis.removeEventListener('pointerdown', firstGesture);
      if (RPG.audio && RPG.audio.init) RPG.audio.init();
    }
    globalThis.addEventListener('keydown', firstGesture);
    globalThis.addEventListener('pointerdown', firstGesture);
    var loop = RPG.core.createLoop({ step: game.tick, render: game.render, shouldStep: game.shouldStep });
    game.attachLoop(loop);
    var page = canvas.ownerDocument;
    page.addEventListener('visibilitychange', function () { game.visibilityChanged(page.hidden); });
    loop.start();
    return game;
  }

  RPG.core.game = { create: create, start: start, STATES: STATES };
})(globalThis.RPG = globalThis.RPG || {});

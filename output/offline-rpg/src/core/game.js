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
  var MAX_FRAME_DT = 0.1; // s; longest real frame the camera follow integrates over
  var DEFAULT_DIFFICULTY = 'normal';
  var DEFAULT_NAME = 'Wayfarer';
  var ERROR_EVENT = 'gameError';
  var MAX_QUEUED_KEYS = 32;
  var MAX_QUEUED_POINTERS = 8;
  var DEFAULT_LAYOUT = { x: 0, y: 0, w: 640, h: 480 };
  var OVERLAY_TOGGLES = { inventory: 'inventory', character: 'character', skills: 'skills', settings: 'settings', saveMenu: 'saveload' };
  var DEFAULT_SETTINGS = Object.freeze({ volume: 0.5, sfx: 0.8, muted: false, quality: 'high', showDamageNumbers: true, screenShake: true, keys: {} });

  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function fail(code, error) { return { ok: false, code: code, error: error }; }
  function msg(e) { return e && e.message ? e.message : String(e); }

  /** screenShake used to be a 0..1 number in older saves; settings.js keeps a boolean. Booleans are the contract. */
  function normalizeSettings(st) {
    if (typeof st.screenShake === 'number') st.screenShake = st.screenShake > 0;
    else if (st.screenShake !== undefined) st.screenShake = !!st.screenShake;
    return st;
  }

  function emptyIntent() {
    return { move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} };
  }

  /**
   * create(deps) -> game. deps (all optional but `canvas`-free): { events, persistence, sim, input, renderer, screens, audio,
   * storage (key-value backend for persistence.saveGame/loadGame), difficulty, areas, rng, clock, now,
   * settingsStore (ui/settings model: owns the standalone settings key, so deleting a save never touches settings),
   * overlays (ui/hud-overlays instance, built from `screens` when omitted), readImportText, download, ctx }.
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
    var settingsStore = deps.settingsStore || null;
    var overlays = deps.overlays || null;
    var layout = { x: 0, y: 0, w: DEFAULT_LAYOUT.w, h: DEFAULT_LAYOUT.h }; // live: screens read it at draw time
    var registered = {};
    var wired = false;
    var keyQueue = [];
    var pointerQueue = [];
    var defaultDifficulty = DEFAULT_DIFFICULTY;
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
      return normalizeSettings(out);
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
      snapCamera(w);
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

    /** Teleports the camera onto the player; the render interpolation start is moved too so it cannot drag the view back. */
    function snapCamera(world) {
      var p = world && world.player;
      if (p) { p.prevX = p.x; p.prevY = p.y; }
      if (renderer && renderer.snapTo && p) renderer.snapTo(world);
    }

    var loading = 0; // >0 while loadFromSlot runs: autosave is suppressed

    function adopt(world) {
      game.world = world;
      pendingDeath = false;
      if (overlays && overlays.reset) overlays.reset();
      registerPlayerScreens(world.player);
      game.victoryShown = !!(world.progress && world.progress.bossesDefeated && world.progress.bossesDefeated[FINAL_BOSS_ID]);
      bossCount = countBosses(world);
      lastAutosaveAt = world.time || 0;
      calmFor = 0;
      snapCamera(world);
    }

    function countBosses(w) {
      return w && w.progress && w.progress.bossesDefeated ? Object.keys(w.progress.bossesDefeated).length : 0;
    }

    /** opts: { seed (uint32|string), difficulty (id|config), name }. */
    function newGame(opts) {
      opts = opts || {};
      var diff = resolveDifficulty(opts.difficulty === undefined ? defaultDifficulty : opts.difficulty);
      var world = sim.createWorld({ seed: seedOf(opts.seed), difficulty: diff });
      world.difficulty = diff;
      if (areas && typeof areas.enter === 'function' && RPG.data && RPG.data.areas && RPG.data.areas[START_AREA]) {
        areas.enter(world, START_AREA);
      }
      if (opts.name && world.player) world.player.name = opts.name;
      game.playerName = opts.name || DEFAULT_NAME;
      game.lastError = null;
      adopt(world);
      setDifficulty(diff); // the one path every chosen difficulty takes (new-game form, settings)
      setState(STATES.PLAYING);
      return { ok: true };
    }

    function setDifficulty(idOrConfig) {
      try {
        var d = resolveDifficulty(idOrConfig);
        game.difficultyId = d.id;
        if (game.world) {
          game.world.difficulty = d;
          if (RPG.world.areas && RPG.world.areas.rescaleMonsters) RPG.world.areas.rescaleMonsters(game.world, d);
        }
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
      loading++; // restore re-enters the area (AREA_CHANGED); that must not autosave the pre-load state over slot 'auto'
      try { return loadFromSlotInner(slot); } finally { loading--; }
    }

    function loadFromSlotInner(slot) {
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
        // With a settings store the standalone key is the source of truth; a save's embedded copy must not override it.
        if (!settingsStore && r.game.settings && Object.keys(r.game.settings).length) applySettings(r.game.settings, { fromSave: true });
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
      if (!game.world || pendingDeath || loading) return null;
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

    function applySettings(s, o) {
      if (isObj(s)) Object.keys(s).forEach(function (k) { game.settings[k] = s[k]; });
      var st = normalizeSettings(game.settings);
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
      // A save restores its own difficulty; only a live settings change may retarget the running world.
      if (st.difficulty !== undefined && !(o && o.fromSave)) {
        var want = st.difficulty === 'custom' && settingsStore ? settingsStore.currentDifficulty() : st.difficulty;
        try {
          resolveDifficulty(want);
          defaultDifficulty = want;
          if (game.world) setDifficulty(want);
        } catch (e) { setError('settings', msg(e)); }
      }
      return game.settings;
    }

    /** Writes the standalone settings key. Returns { ok } (no store -> not ok); save slots are never involved. */
    function persistSettings() {
      return settingsStore ? settingsStore.save() : fail('state', 'no settings store');
    }

    // ---- screens ------------------------------------------------------------------------------------------------

    function canRegister() { return !!(screens && typeof screens.register === 'function' && RPG.ui); }

    function put(def) { screens.register(def); registered[def.id] = true; }

    // Screens that wrap a bare (action, arg) handler get keymap codes translated here; the manager passes (code, action).
    var NAV = { moveLeft: 'left', moveRight: 'right', moveUp: 'up', moveDown: 'down' };

    function reportResult(r) {
      if (overlays && r && r.ok === false) {
        var texts = RPG.ui.skillsScreen && RPG.ui.skillsScreen.REASON_TEXT;
        overlays.toast((texts && texts[r.reason]) || r.reason || 'Not possible');
      }
    }

    function adapt(def, keys) {
      var inner = def.onKey;
      def.onKey = function (code, action) {
        if (!inner) return false;
        var name = NAV[action] || keys[code] || null, arg;
        if (!name && /^Digit[1-7]$/.test(code) && keys.digits) { name = 'equip'; arg = Number(code.slice(5)) - 1; }
        if (!name) return false;
        reportResult(inner(name, arg));
        return true;
      };
      return def;
    }

    /** inventory / skills / character act on world.player, which is replaced on new game and load, so re-register then. */
    function registerPlayerScreens(player) {
      if (!canRegister() || !player) return;
      var ui = RPG.ui;
      if (ui.inventory) put(adapt(ui.inventory.createScreen(player, layout, reportResult), { Enter: 'equip', KeyX: 'drop' }));
      if (ui.skillsScreen) put(adapt(ui.skillsScreen.createScreen(player, layout, reportResult), { Enter: 'learn', Backspace: 'respec', digits: true }));
      if (ui.character) put(ui.character.createScreen(player, layout));
    }

    function registerScreens() {
      if (!canRegister()) return;
      var ui = RPG.ui;
      wired = true;
      if (!overlays && ui.overlays) {
        overlays = ui.overlays.create({ events: events, screens: screens, onRespawn: respawn, onContinue: continuePlaying });
      }
      if (overlays) {
        put({ id: 'playing', draw: function (ctx) {
          overlays.drawToasts(ctx);
          if (game.state === STATES.PLAYING && !screens.paused()) overlays.drawButtons(ctx, layout);
        } });
        registered.dead = registered.victory = true; // overlays registers these two itself
      }
      var shared = { game: game, screens: screens, storage: deps.storage, layout: layout };
      var saveload = ui.saveload && ui.saveload.createScreen(Object.assign({
        readImportText: deps.readImportText, download: deps.download }, shared));
      if (saveload) put(saveload);
      var hasSettings = !!(settingsStore && ui.settings && ui.settings.createScreen);
      if (hasSettings) put(ui.settings.createScreen({ settings: settingsStore, layout: layout }));
      if (ui.mainmenu && saveload) put(ui.mainmenu.createScreen(Object.assign({ saveload: saveload, settings: settingsStore }, shared)));
      if (ui.pause && saveload) {
        put(ui.pause.createScreen(Object.assign({
          saveload: saveload, openSettings: hasSettings ? function () { screens.open('settings'); } : null }, shared)));
      }
    }

    /** Queues a key press (KeyboardEvent.code) for the menus; the next frame routes it. Auto-repeat is the caller's to drop. */
    function queueKey(code) {
      if (keyQueue.length < MAX_QUEUED_KEYS) keyQueue.push(code);
    }

    /** Queues a primary-button press (canvas CSS px) for the menus; the next frame routes it. Ignored while playing. */
    function queuePointer(x, y, button) {
      if (pointerQueue.length < MAX_QUEUED_POINTERS) pointerQueue.push({ x: x, y: y, button: button || 0 });
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
        var id = OVERLAY_TOGGLES[name];
        // With real screens wired, a toggle for a screen nobody registered would open a blank modal.
        if (t[name] && screens && game.state !== STATES.MAINMENU && (!wired || registered[id])) screens.toggle(id);
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

    // The settings screen edits and saves the store itself; the game picks the result up when the screen closes.
    var settingsWasOpen = false;
    function syncSettings() {
      if (!settingsStore || !screens || typeof screens.has !== 'function') return;
      var open = screens.has('settings');
      if (settingsWasOpen && !open) applySettings(settingsStore.snapshot());
      settingsWasOpen = open;
    }

    /** Samples input and routes it: menus/overlays get the queued key codes, otherwise the intent's toggles apply. */
    function pumpInput() {
      var intent = sampleIntent();
      var keys = keyQueue, points = pointerQueue;
      keyQueue = [];
      pointerQueue = [];
      var inMenu = screens && (screens.paused() || game.state === STATES.MAINMENU);
      if (inMenu && keys.length && typeof screens.handleKey === 'function') {
        keys.forEach(function (code) { screens.handleKey(code); });
        intent.toggles = {}; // the same presses, already handled through the manager
      }
      // A press that lands on a menu acts on it; one made while playing is just the attack button (the sim's intent).
      if (inMenu && points.length && typeof screens.handlePointer === 'function') {
        points.forEach(function (p) { screens.handlePointer(p.x, p.y, p.button); });
      }
      if (!inMenu && points.length && overlays && overlays.hitButton && game.state === STATES.PLAYING && game.world) {
        // A press on a HUD button opens its screen and is not also an attack; presses elsewhere stay gameplay clicks.
        points.forEach(function (p) {
          if (p.button !== 0 || screens.paused()) return;
          var id = overlays.hitButton(p.x, p.y, layout);
          if (!id) return;
          intent.attack = false;
          if (id === 'pause') setPaused(true);
          else if (!wired || registered[id]) screens.open(id);
        });
      }
      handleToggles(intent);
      syncSettings();
      syncPause(); // a key may have closed the pause overlay this frame
      return intent;
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
        events.emit(NAMES.VICTORY, {});
        return;
      }
      calmFor = monsterNear(w) ? 0 : calmFor + dt;
      if (calmFor >= AUTOSAVE_CALM && w.time - lastAutosaveAt >= AUTOSAVE_INTERVAL) autosave('timer');
    }

    /** One fixed step: intent -> RPG.sim.step. A throwing sim pauses the game and surfaces the error; the loop survives. */
    function tick(dt) {
      if (!game.world) return;
      syncPause();
      var intent = pumpInput();
      if (!shouldStep()) return;
      try {
        sim.step(game.world, intent, dt);
        if (overlays) overlays.update(dt);
      } catch (e) {
        game.stepErrors++;
        setError('sim', msg(e));
        events.emit(ERROR_EVENT, { code: 'sim', error: msg(e) });
        setPaused(true);
        return;
      }
      afterStep(dt);
    }

    // Inputs for render/hud.buildHudModel. Toasts are drawn by the overlays' playing screen, so none are passed here.
    var skillCooldowns = null, fps = 0, lastRenderAt = null, lastFrameAt = null;
    function hudInputs() {
      if (!skillCooldowns) {
        skillCooldowns = {};
        var defs = (RPG.data && RPG.data.skills) || {};
        Object.keys(defs).forEach(function (id) { if (defs[id] && typeof defs[id].cooldown === 'number') skillCooldowns[id] = defs[id].cooldown; });
      }
      var prog = RPG.world && RPG.world.progression;
      if (clock) { // smoothed frames per second for the debug line
        var now = clock.now();
        if (lastRenderAt !== null && now > lastRenderAt) fps = fps ? fps * 0.9 + 0.1 / (now - lastRenderAt) : 1 / (now - lastRenderAt);
        lastRenderAt = now;
      }
      return {
        state: game.state, error: game.lastError, skillCooldowns: skillCooldowns,
        xpToNext: prog && prog.xpToNext ? prog.xpToNext : undefined,
        debug: !!(renderer && renderer.debug), fps: Math.round(fps)
      };
    }

    /** Real seconds since the previous render, for the camera follow. Capped so a stalled tab does not jerk the view. */
    function frameDt() {
      if (!clock) return 0;
      var now = clock.now(), dt = lastFrameAt !== null && now > lastFrameAt ? now - lastFrameAt : 0;
      lastFrameAt = now;
      return Math.min(dt, MAX_FRAME_DT);
    }

    function render(alpha) {
      // While not stepping (pause/overlay) the loop never calls tick(), so keep reading toggles here or Esc could not resume.
      syncPause();
      if ((game.world || game.state === STATES.MAINMENU) && !shouldStep() && input && renderer) pumpInput();
      if (renderer) { layout.w = renderer.viewW || layout.w; layout.h = renderer.viewH || layout.h; }
      if (renderer && game.world) {
        if (game.settings.screenShake === false && game.world.fx) game.world.fx.shake = 0;
        renderer.draw(game.world, alpha, frameDt(), hudInputs());
      }
      if (screens && deps.ctx) {
        // The renderer leaves an identity transform; the screens lay out in CSS px like the HUD.
        if (deps.ctx.setTransform) deps.ctx.setTransform((renderer && renderer.dpr) || 1, 0, 0, (renderer && renderer.dpr) || 1, 0, 0);
        screens.draw(deps.ctx);
        if (deps.ctx.setTransform) deps.ctx.setTransform(1, 0, 0, 1, 0, 0);
      }
    }

    function visibilityChanged(hidden) {
      if (hidden) { if (game.state === STATES.PLAYING) setPaused(true); }
      if (loop) loop.reset();
    }

    // ---- events ------------------------------------------------------------------------------------------------

    offs.push(events.on(NAMES.PLAYER_DIED, function () { pendingDeath = true; }));
    // Sound effects: the bridge subscribes to the event bus (not to a world), so one attach covers every new game and
    // load. Nothing plays until the first gesture creates the AudioContext; a missing or failed context is a no-op.
    attachAudioBridge();
    offs.push(events.on(NAMES.AREA_CHANGED, function () { snapCamera(game.world); autosave('area'); }));
    offs.push(events.on(NAMES.LEVEL_UP, function () { autosave('level'); }));

    function attachAudioBridge() {
      var bridge = RPG.audio && RPG.audio.bridge;
      if (!audio || !bridge || typeof bridge.attach !== 'function' || typeof audio.play !== 'function' || typeof audio.status !== 'function') return;
      try { offs.push(bridge.attach(events, audio)); } catch (e) { game.lastWarnings = (game.lastWarnings || []).concat('audio bridge: ' + msg(e)); }
    }

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
    game.queueKey = queueKey;
    game.persistSettings = persistSettings;
    game.overlays = function () { return overlays; };
    game.shouldStep = shouldStep;
    game.tick = tick;
    game.render = render;
    game.queuePointer = queuePointer;
    game.visibilityChanged = visibilityChanged;
    game.attachLoop = function (l) { loop = l; };
    game.on = function (type, fn) { return events.on(type, fn); };
    game.destroy = destroy;
    if (settingsStore) {
      var loaded = settingsStore.load();
      if (!loaded.ok) game.lastWarnings = loaded.warnings || [];
      applySettings(settingsStore.snapshot());
    } else {
      applySettings();
    }
    registerScreens();
    if (screens) screens.open('mainmenu');
    return game;
  }

  /** Browser entry used by main.js: builds the real collaborators, wires the loop, returns the game.
   *  opts.settingsStorage: browser key-value backend for the settings key (main.js supplies it; omitted -> in memory). */
  function start(canvas, opts) {
    var renderer = RPG.render.create(canvas);
    var input = RPG.input.create();
    var screens = RPG.ui.screens.create();
    var files = RPG.persistence && RPG.persistence.files;
    var settingsStore = RPG.ui.settings.create({ storage: opts && opts.settingsStorage, input: input, audio: RPG.audio });
    var game = create({
      renderer: renderer, input: input, screens: screens, audio: RPG.audio, ctx: canvas.getContext('2d'),
      clock: { now: function () { return Date.now() / 1000; } }, settingsStore: settingsStore,
      download: files && files.downloadText, readImportText: files && files.pickSaveText
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
    // Menus and overlays take raw key codes; the sim only ever sees the intent. Auto-repeat would double-activate Enter.
    globalThis.addEventListener('keydown', function (e) { if (!e.repeat) game.queueKey(e.code); });
    globalThis.addEventListener('pointerdown', firstGesture);
    // Mouse and touch both arrive as pointer events; the menus take them in canvas CSS px, like the screens' layout.
    canvas.addEventListener('pointerdown', function (e) {
      var rect = canvas.getBoundingClientRect();
      game.queuePointer(e.clientX - rect.left, e.clientY - rect.top, e.button);
    });
    var loop = RPG.core.createLoop({ step: game.tick, render: game.render, shouldStep: game.shouldStep });
    game.attachLoop(loop);
    game.screens = screens;
    game.loop = loop;
    var page = canvas.ownerDocument;
    page.addEventListener('visibilitychange', function () { game.visibilityChanged(page.hidden); });
    loop.start();
    return game;
  }

  RPG.core.game = { create: create, start: start, STATES: STATES };
})(globalThis.RPG = globalThis.RPG || {});

'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { loadGame } = require('./game-load.js');
const { sampleState, flakyBackend } = require('../persistence/helpers.js');

// Fake snapshot/restore over the REAL serialize/saveGame/loadGame, so a save really goes through JSON + checksum + storage.
function makeEnv(over) {
  over = over || {};
  const RPG = loadGame();
  const events = RPG.core.events.create();
  const backend = over.backend || flakyBackend();
  const steps = [];
  const sim = {
    createWorld(o) {
      return { seed: o.seed, time: 0, tick: 0, level: 1, monsters: [], player: { x: 0, y: 0, hp: 100, maxHp: 100 },
        progress: { bossesDefeated: {}, kills: 0, playtime: 0 }, difficulty: o.difficulty };
    },
    step(w, intent, dt) { if (over.stepThrows) throw new Error('boom'); steps.push(intent); w.time += dt; w.tick++; }
  };
  const base = RPG.persistence;
  const persistence = Object.assign({}, base, {
    snapshot(w, g) {
      const s = sampleState();
      s.seed = w.seed; s.meta.level = w.level; s.meta.name = g.name; s.player.level = w.level;
      s.settings = g.settings; s.progress.kills = w.progress.kills;
      return s;
    },
    restore(body) {
      const w = sim.createWorld({ seed: body.seed });
      w.level = body.meta.level; w.progress.kills = body.progress.kills;
      return { ok: true, world: w, warnings: [], game: { settings: body.settings, name: body.meta.name, difficulty: { id: body.difficulty.id } } };
    }
  });
  const stack = [];
  const screens = over.noScreens ? null : {
    paused: () => stack.length > 0, open: (id) => { if (id !== 'mainmenu' && id !== 'playing' && !stack.includes(id)) stack.push(id); },
    close: () => stack.pop() !== undefined, toggle: (id) => { const i = stack.indexOf(id); if (i >= 0) stack.splice(i, 1); else stack.push(id); return true; },
    draw() {}
  };
  const script = [];
  const input = { buildFrame: () => script.shift() || { move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} } };
  const renderer = { camera: {}, viewW: 1, viewH: 1, draw() {}, snapTo() {} };
  const game = RPG.core.game.create({
    events, persistence, sim, input, renderer, screens, areas: null, difficulty: null, storage: RPG.persistence.storage.createStorage({ backend }),
    clock: { now: () => 5 }
  });
  return { RPG, game, events, sim, steps, stack, script, backend, persistence };
}

test('starts in mainmenu and does not step', () => {
  const e = makeEnv();
  assert.strictEqual(e.game.state, 'mainmenu');
  assert.strictEqual(e.game.shouldStep(), false);
  e.game.tick(1 / 60);
  assert.strictEqual(e.steps.length, 0);
});

test('newGame -> playing; tick steps the sim', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 7, difficulty: 'normal', name: 'Ada' });
  assert.strictEqual(e.game.state, 'playing');
  assert.strictEqual(e.game.world.seed, 7);
  e.game.tick(1 / 60);
  assert.strictEqual(e.steps.length, 1);
});

test('string seeds are hashed deterministically', () => {
  const a = makeEnv(), b = makeEnv();
  a.game.newGame({ seed: 'abc' }); b.game.newGame({ seed: 'abc' });
  assert.strictEqual(a.game.world.seed, b.game.world.seed);
});

test('pause / resume transitions and shouldStep', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.game.setPaused(true);
  assert.strictEqual(e.game.state, 'paused');
  assert.strictEqual(e.game.shouldStep(), false);
  e.game.tick(1 / 60);
  assert.strictEqual(e.steps.length, 0);
  e.game.setPaused(false);
  assert.strictEqual(e.game.state, 'playing');
  assert.strictEqual(e.game.shouldStep(), true);
});

test('pause toggle from input pauses and resumes; closing the pause overlay resumes', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  const t = (name) => ({ move: { x: 1, y: 0 }, aim: {}, attack: true, skills: [], dash: false, toggles: { [name]: true } });
  e.script.push(t('pause'));
  e.game.tick(1 / 60);
  assert.strictEqual(e.game.state, 'paused');
  e.stack.length = 0; // Esc handled by the screens manager closed the overlay
  e.game.render(0);
  assert.strictEqual(e.game.state, 'playing');
});

test('overlay open stops stepping and zeroes held input', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.script.push({ move: { x: 1, y: 0 }, aim: {}, attack: true, skills: [], dash: false, toggles: { inventory: true } });
  e.game.tick(1 / 60);
  assert.strictEqual(e.steps.length, 0);
  assert.strictEqual(e.game.shouldStep(), false);
});

test('player death -> dead; respawn -> playing; autosave is skipped while dead', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.events.emit(e.events.NAMES.PLAYER_DIED, {});
  e.game.tick(1 / 60);
  assert.strictEqual(e.game.state, 'dead');
  assert.strictEqual(e.game.autosave('x'), null);
  assert.strictEqual(e.game.saveToSlot('2').ok, false);
  assert.strictEqual(e.game.respawn().ok, true);
  assert.strictEqual(e.game.state, 'playing');
  assert.strictEqual(e.game.world.player.hp, 100);
});

test('final boss -> victory with autosave first; continue -> playing; quit -> mainmenu', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.sim.step = (w, i, dt) => { w.time += dt; w.progress.bossesDefeated['warden-of-ash'] = true; };
  e.game.tick(1 / 60);
  assert.strictEqual(e.game.state, 'victory');
  assert.strictEqual(e.game.loadFromSlot('auto').ok, true, 'victory autosave exists');
  e.game.state = 'victory';
  assert.strictEqual(e.game.continuePlaying().ok, true);
  assert.strictEqual(e.game.state, 'playing');
  e.game.quitToMenu();
  assert.strictEqual(e.game.state, 'mainmenu');
  assert.strictEqual(e.game.world, null);
});

test('autosave: area change and level up write slot auto; not while paused', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.events.emit(e.events.NAMES.AREA_CHANGED, {});
  assert.strictEqual(e.backend.getItem('offline-rpg:v1:slot:auto') !== null, true);
  e.backend.map.clear();
  e.game.setPaused(true);
  e.events.emit(e.events.NAMES.LEVEL_UP, {});
  assert.strictEqual(e.backend.getItem('offline-rpg:v1:slot:auto'), null);
  e.game.setPaused(false);
  e.events.emit(e.events.NAMES.LEVEL_UP, {});
  assert.notStrictEqual(e.backend.getItem('offline-rpg:v1:slot:auto'), null);
});

test('timed autosave after 60s of calm, not while a monster is near', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.game.world.monsters.push({ x: 10, y: 0, def: { aiParams: { aggroRange: 300 } } });
  for (let i = 0; i < 61 * 60; i++) e.game.tick(1 / 60);
  assert.strictEqual(e.backend.getItem('offline-rpg:v1:slot:auto'), null);
  e.game.world.monsters.length = 0;
  for (let i = 0; i < 6 * 60; i++) e.game.tick(1 / 60);
  assert.notStrictEqual(e.backend.getItem('offline-rpg:v1:slot:auto'), null);
});

test('save then load through fake storage restores the same snapshot', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 4242 });
  e.game.world.level = 9; e.game.world.progress.kills = 77;
  const before = JSON.stringify(e.persistence.snapshot(e.game.world, { settings: e.game.settings, name: e.game.playerName }));
  assert.strictEqual(e.game.saveToSlot('2').ok, true);
  e.game.quitToMenu();
  const r = e.game.loadFromSlot('2');
  assert.strictEqual(r.ok, true);
  assert.strictEqual(e.game.state, 'playing');
  const after = JSON.stringify(e.persistence.snapshot(e.game.world, { settings: e.game.settings, name: e.game.playerName }));
  assert.strictEqual(after, before);
  assert.strictEqual(e.game.world.level, 9);
});

test('quickSave/quickLoad use slot 1, never auto', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  assert.strictEqual(e.game.quickSave().ok, true);
  assert.notStrictEqual(e.backend.getItem('offline-rpg:v1:slot:1'), null);
  assert.strictEqual(e.backend.getItem('offline-rpg:v1:slot:auto'), null);
  assert.strictEqual(e.game.quickLoad().ok, true);
});

test('loading a missing/corrupt slot fails cleanly and keeps the current world', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 5 });
  const w = e.game.world;
  assert.strictEqual(e.game.loadFromSlot('3').ok, false);
  e.backend.setItem('offline-rpg:v1:slot:3', '{not json');
  const r = e.game.loadFromSlot('3');
  assert.strictEqual(r.ok, false);
  assert.strictEqual(e.game.world, w);
  assert.ok(e.game.lastError);
});

test('failed write sets lastError, returns not-ok and never throws', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.backend.failAll = true;
  let r;
  assert.doesNotThrow(() => { r = e.game.saveToSlot('1'); });
  assert.strictEqual(r.ok, false);
  assert.ok(e.game.lastError);
  assert.doesNotThrow(() => e.events.emit(e.events.NAMES.AREA_CHANGED, {}));
});

test('export / import / delete wrappers', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 11 });
  e.game.world.level = 3;
  const x = e.game.exportSave('current');
  assert.strictEqual(x.ok, true);
  assert.match(x.filename, /\.json$/);
  const doc = JSON.parse(x.text);
  assert.strictEqual(doc.state === undefined && doc.meta === undefined, false);
  e.game.quitToMenu();
  const imp = e.game.importSave(x.text);
  assert.strictEqual(imp.ok, true);
  assert.strictEqual(e.game.world.level, 3);
  assert.strictEqual(e.game.exportSave('1').ok, true);
  assert.strictEqual(e.game.deleteSave('1').ok, true);
  assert.strictEqual(e.game.loadFromSlot('1').ok, false);
  assert.strictEqual(e.game.importSave('garbage').ok, false);
});

test('setDifficulty updates id and live world', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  assert.strictEqual(e.game.setDifficulty('hard').ok, true);
  assert.strictEqual(e.game.difficultyId, 'hard');
});

test('exception in sim.step does not kill the loop and is surfaced', () => {
  const e = makeEnv({ stepThrows: true });
  e.game.newGame({ seed: 1 });
  const errors = [];
  e.game.on('gameError', (p) => errors.push(p));
  assert.doesNotThrow(() => e.game.tick(1 / 60));
  assert.strictEqual(e.game.state, 'paused');
  assert.strictEqual(e.game.stepErrors, 1);
  assert.strictEqual(e.game.lastError.code, 'sim');
  assert.match(e.game.lastError.error, /boom/);
  assert.strictEqual(errors.length, 1);
  // the real loop keeps calling tick/render and survives
  const loop = e.RPG.core.createLoop({ step: e.game.tick, render: e.game.render, shouldStep: e.game.shouldStep, clock: { now: () => 0 }, scheduler: { request() {}, cancel() {} } });
  assert.doesNotThrow(() => loop.frame());
  e.sim.step = () => {};
  e.game.setPaused(false);
  assert.strictEqual(e.game.state, 'playing');
});

test('applySettings reaches audio and input; settings come back from a load', () => {
  const calls = [];
  const e = makeEnv();
  const game2 = e.RPG.core.game.create({
    events: e.events, persistence: e.persistence, sim: e.sim,
    audio: { setVolume: (v, s) => calls.push(['vol', v, s]), mute: (m) => calls.push(['mute', m]) },
    input: { setBinding: (a, c) => calls.push(['bind', a, c]), buildFrame: () => ({ toggles: {} }) }
  });
  game2.applySettings({ volume: 0.2, muted: true, keys: { skills: ['KeyK'] } });
  assert.deepStrictEqual(calls.filter((c) => c[0] === 'vol').pop(), ['vol', 0.2, 0.8]);
  assert.deepStrictEqual(calls.filter((c) => c[0] === 'mute').pop(), ['mute', true]);
  assert.deepStrictEqual(calls.pop(), ['bind', 'skills', 'KeyK']);
});

test('loading a slot never overwrites slot auto with pre-load state (restore re-enters the area)', () => {
  const e = makeEnv();
  e.game.newGame({ seed: 1 });
  e.game.world.level = 7;
  assert.strictEqual(e.game.saveToSlot('2').ok, true);
  e.game.world.level = 3;
  e.game.autosave('test');
  const autoBefore = e.backend.getItem('offline-rpg:v1:slot:auto');
  assert.notStrictEqual(autoBefore, null);
  e.game.world.level = 5; // pre-load state now differs from what auto holds
  // Real restore re-enters the area, which emits AREA_CHANGED while the old world is still current.
  const real = e.persistence.restore;
  e.persistence.restore = function (b) { const r = real(b); e.events.emit(e.events.NAMES.AREA_CHANGED, {}); return r; };
  assert.strictEqual(e.game.loadFromSlot('2').ok, true);
  assert.strictEqual(e.game.world.level, 7);
  assert.strictEqual(e.backend.getItem('offline-rpg:v1:slot:auto'), autoBefore, 'auto unchanged by load');
  // a real autosave trigger afterwards still writes auto, from the loaded state
  e.events.emit(e.events.NAMES.AREA_CHANGED, {});
  assert.notStrictEqual(e.backend.getItem('offline-rpg:v1:slot:auto'), autoBefore);
  assert.strictEqual(e.game.loadFromSlot('auto').ok, true);
  assert.strictEqual(e.game.world.level, 7);
});

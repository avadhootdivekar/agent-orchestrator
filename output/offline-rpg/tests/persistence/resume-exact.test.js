'use strict';
// W06-03: save/resume exactness through the real Game (core/game.js) with a localStorage-shaped stub.
// "Discard the Game" = build a second engine + Game on the same backend, then load. Covers mid-fight resume, Delete Save
// keeping settings, export/import round trip, sample-save migration, and corrupt / wrong-version import rejection.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { makeGame, makeBot, memoryBackend, DT } = require('../integration/helpers.js');

const SAMPLE = path.join(__dirname, '..', '..', 'docs', 'sample-game-save.json');

function canonical(v) {
  if (Array.isArray(v)) return v.map(canonical);
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v).sort()) out[k] = canonical(v[k]);
    return out;
  }
  return v;
}
function snap(g) {
  const s = g.RPG.persistence.snapshot(g.game.world, { settings: g.game.settings, name: g.game.playerName });
  delete s.savedAt;
  return JSON.stringify(canonical(s));
}
function monsterPrint(w) {
  return JSON.stringify(w.monsters.filter((m) => !m.dead).map((m) => [m.defId, m.x, m.y, m.hp]));
}
function raw(g, slot) { return g.backend.getItem('offline-rpg:v1:slot:' + slot); }

// Hard difficulty, forest, a minute of botting so the player is hurt, leveled, with cooldowns and live monsters.
async function midFight(seed, backend) {
  const g = await makeGame({ backend });
  g.newGame(seed, 'hard');
  g.RPG.world.areas.enter(g.game.world, 'forest');
  g.drive(60 * 40, makeBot(seed));
  const p = g.game.world.player;
  g.RPG.sim.rewards.grantXp(p, 600); // real level-up path: level growth modifiers, skill points, full heal
  p.hp = p.stats.maxHp * 0.6; p.resource = p.stats.maxResource * 0.5;
  return g;
}

test('mid-fight save, discard the Game, load: player, inventory, skills, area, rng, difficulty and playtime resume exactly', async () => {
  const g1 = await midFight('resume-exact');
  const w1 = g1.game.world, p1 = w1.player;
  assert.ok(w1.progress.kills > 0, 'fixture must have fought');
  assert.ok(w1.monsters.some((m) => !m.dead), 'fixture must be mid-fight (live monsters)');
  assert.equal(w1.area.id, 'forest');
  assert.equal(g1.game.saveToSlot('1').ok, true);
  const saved = snap(g1);
  const rngW = w1.rng.getState(), rngL = w1.lootRng.getState();

  const g2 = await makeGame({ backend: g1.backend }); // fresh engine, nothing shared but the storage
  assert.notEqual(g2.game, g1.game);
  const r = g2.game.loadFromSlot('1');
  assert.equal(r.ok, true, JSON.stringify(r));
  const w2 = g2.game.world, p2 = w2.player;

  assert.equal(snap(g2), saved, 'restored snapshot differs from the saved one');
  assert.ok(w1.time > 0, 'fixture must have advanced the world clock');
  assert.equal(w2.time, w1.time, 'world.time is restored on load');
  assert.equal(w2.area.id, 'forest');
  assert.equal(JSON.parse(saved).seed, JSON.parse(snap(g2)).seed);
  assert.equal(w2.rng.getState(), rngW);
  assert.equal(w2.lootRng.getState(), rngL);
  assert.equal(w2.difficulty.id, 'hard');
  assert.equal(g2.game.difficultyId, 'hard');
  assert.equal(w2.progress.playtime, w1.progress.playtime);
  assert.equal(w2.progress.kills, w1.progress.kills);
  assert.equal(p2.level, p1.level);
  assert.equal(p2.xp, p1.xp);
  assert.equal(p2.gold, p1.gold);
  assert.equal(p2.hp, p1.hp);
  assert.equal(p2.stats.maxHp, p1.stats.maxHp, 'derived max hp (level growth) lost on load');
  assert.equal(p2.stats.damage, p1.stats.damage);
  assert.ok(p1.level > 1, 'fixture must have leveled');
  assert.equal(p2.x, p1.x);
  assert.equal(p2.y, p1.y);
  assert.equal(JSON.stringify(canonical(p2.inventory)), JSON.stringify(canonical(p1.inventory)));
  assert.equal(JSON.stringify(canonical(p2.skills.bar)), JSON.stringify(canonical(p1.skills.bar)));
  assert.equal(JSON.stringify(canonical(p2.skills.cooldowns || {})), JSON.stringify(canonical(p1.skills.cooldowns || {})));
  assert.equal(g2.game.state, 'playing');
  // Monsters are transient by design: the area is rebuilt from the seed, so two loads of one save agree.
  assert.ok(w2.monsters.some((m) => !m.dead), 'uncleared fight respawns');
  const g3 = await makeGame({ backend: g1.backend });
  assert.equal(g3.game.loadFromSlot('1').ok, true);
  assert.equal(monsterPrint(g3.game.world), monsterPrint(w2), 'respawned monsters are not deterministic');
  // The restored rng streams continue identically to the original's.
  for (let i = 0; i < 10; i++) {
    assert.equal(w2.rng.next(), w1.rng.next());
    assert.equal(w2.lootRng.next(), w1.lootRng.next());
  }
  assert.equal(g2.game.stepErrors, 0);
  g2.drive(120, makeBot('resume-exact'));
  assert.equal(g2.game.stepErrors, 0);
});

test('autosave slot resumes the same way as a manual slot', async () => {
  const g1 = await midFight('resume-auto');
  assert.equal(g1.game.autosave('test').ok, true);
  const saved = snap(g1);
  const g2 = await makeGame({ backend: g1.backend });
  assert.equal(g2.game.loadFromSlot('auto').ok, true);
  assert.equal(snap(g2), saved);
});

test('Delete Save removes the slot but keeps the standalone settings', async () => {
  const RPG0 = (await makeGame()).RPG; // engine only, to reach ui.settings in a game with a settings store
  const settingsBackend = memoryBackend();
  const saves = memoryBackend();
  const storage = RPG0.persistence.storage.createStorage({ backend: saves });
  const settings = RPG0.ui.settings.create({ storage: settingsBackend, input: null, audio: null });
  settings.setVolume(0.25); settings.setMuted(true);
  assert.equal(settings.save().ok, true);
  const game = RPG0.core.game.create({
    input: { buildFrame: () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} }) },
    renderer: { camera: {}, viewW: 800, viewH: 600, draw() {}, snapTo() {}, resize() {} },
    storage, clock: { now: () => 0 }, screens: null, settingsStore: settings
  });
  assert.equal(game.newGame({ seed: 'del', difficulty: 'normal' }).ok, true);
  assert.equal(game.saveToSlot('1').ok, true);
  assert.ok(saves.getItem('offline-rpg:v1:slot:1'));
  const settingsBefore = settingsBackend.getItem('offline-rpg:v1:settings');
  assert.ok(settingsBefore);

  assert.equal(game.deleteSave('1').ok, true);
  assert.equal(saves.getItem('offline-rpg:v1:slot:1'), null, 'slot not deleted');
  assert.equal(game.loadFromSlot('1').ok, false);
  assert.equal(settingsBackend.getItem('offline-rpg:v1:settings'), settingsBefore, 'settings changed by Delete Save');
  const again = RPG0.ui.settings.create({ storage: settingsBackend, input: null, audio: null });
  again.load();
  assert.equal(again.snapshot().volume, 0.25);
  assert.equal(again.snapshot().muted, true);
});

test('export -> import round trip on a fresh machine resumes the same state', async () => {
  const g1 = await midFight('rt-export');
  const saved = snap(g1);
  const ex = g1.game.exportSave('current');
  assert.equal(ex.ok, true);
  assert.equal(JSON.parse(ex.text).version, 1);
  const g2 = await makeGame({ backend: memoryBackend() });
  const r = g2.game.importSave(ex.text, '2');
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(snap(g2), saved);
  assert.ok(raw(g2, '2'), 'import did not persist to the slot');
  assert.equal(JSON.parse(ex.text).meta.level, g1.game.world.player.level);
});

test('docs/sample-game-save.json (v1) loads through the Game; a v0 variant migrates to v1', async () => {
  const text = fs.readFileSync(SAMPLE, 'utf8');
  const doc = JSON.parse(text);
  assert.equal(doc.version, 1);
  const g = await makeGame({ backend: memoryBackend() });
  const r = g.game.importSave(text, '1');
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(g.game.world.area.id, doc.area.id);
  assert.equal(g.game.world.seed, doc.seed);
  assert.equal(g.game.world.rng.getState(), doc.rngState.world);
  assert.equal(g.game.world.lootRng.getState(), doc.rngState.loot);
  assert.equal(g.game.world.player.level, doc.player.level);
  assert.equal(g.game.world.progress.playtime, doc.meta.playtime);

  const v0 = JSON.parse(text);
  v0.version = 0; delete v0.rngState; delete v0.checksum;
  const g0 = await makeGame({ backend: memoryBackend() });
  const r0 = g0.game.importSave(JSON.stringify(v0), '1');
  assert.equal(r0.ok, true, JSON.stringify(r0));
  assert.equal(JSON.parse(raw(g0, '1')).version, 1, 'stored slot not rewritten at the current version');
  assert.equal(g0.game.world.player.level, doc.player.level);
});

test('corrupt, truncated and wrong-version imports are rejected and the current save and running game survive', async () => {
  const g = await midFight('reject');
  assert.equal(g.game.saveToSlot('1').ok, true);
  const slotBefore = raw(g, '1'), snapBefore = snap(g);
  const good = JSON.parse(g.game.exportSave('current').text);

  const future = Object.assign({}, good, { version: 99 }); delete future.checksum;
  const negative = Object.assign({}, good, { version: -1 }); delete negative.checksum;
  const fractional = Object.assign({}, good, { version: 1.5 }); delete fractional.checksum;
  const noPlayer = Object.assign({}, good); delete noPlayer.player; delete noPlayer.checksum;
  const badHp = JSON.parse(JSON.stringify(good)); badHp.player.hp = 'lots'; delete badHp.checksum;
  const wrongFormat = Object.assign({}, good, { format: 'something-else' });
  const cases = {
    notJson: '{not json', truncated: JSON.stringify(good).slice(0, 200), empty: '', array: '[]', nul: 'null',
    future: JSON.stringify(future), negative: JSON.stringify(negative), fractional: JSON.stringify(fractional),
    noPlayer: JSON.stringify(noPlayer), badHp: JSON.stringify(badHp), wrongFormat: JSON.stringify(wrongFormat),
    oversize: ' '.repeat(2 * 1024 * 1024 + 10)
  };
  for (const [name, text] of Object.entries(cases)) {
    const r = g.game.importSave(text, '1');
    assert.equal(r.ok, false, `accepted ${name}`);
    assert.equal(raw(g, '1'), slotBefore, `slot 1 changed after rejecting ${name}`);
    assert.equal(snap(g), snapBefore, `running game changed after rejecting ${name}`);
  }
  assert.equal(JSON.parse(raw(g, '1')).version, 1);
  // Future version is a specific, non-destructive refusal.
  assert.equal(g.game.importSave(cases.future, '1').code, 'version');
  // A new Game still loads the preserved save.
  const g2 = await makeGame({ backend: g.backend });
  assert.equal(g2.game.loadFromSlot('1').ok, true);
  assert.equal(snap(g2), snapBefore);
});

test('a damaged slot fails to load without disturbing the running game; a future-version slot is left in place', async () => {
  const g = await midFight('slot-damage');
  assert.equal(g.game.saveToSlot('1').ok, true);
  const snapBefore = snap(g);
  const good = JSON.parse(raw(g, '1'));

  g.backend.setItem('offline-rpg:v1:slot:2', '{"format":"offline-rpg-save","version":1,"trunc');
  const bad = g.game.loadFromSlot('2');
  assert.equal(bad.ok, false);
  assert.equal(snap(g), snapBefore);

  const fut = JSON.stringify(Object.assign({}, good, { version: 7 }));
  g.backend.setItem('offline-rpg:v1:slot:3', fut);
  const r = g.game.loadFromSlot('3');
  assert.equal(r.ok, false);
  assert.equal(r.code, 'version');
  assert.equal(raw(g, '3'), fut, 'newer-version save must not be moved or rewritten');
  assert.equal(snap(g), snapBefore);
});

// W07-02: a restored game must continue exactly like an uninterrupted one (velocity + cleared slots persisted).
test('save -> reload -> continue is identical to an uninterrupted run on the same inputs', async () => {
  // Town has no monsters, so the player (position, velocity, facing) is the whole dynamic state. Save mid-motion.
  const intent = (dx, dy) => () => ({ move: { x: dx, y: dy }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });
  const g1 = await makeGame({ backend: memoryBackend() });
  g1.newGame('resume-det', 'normal');
  g1.drive(20, intent(1, 0));
  const p1 = g1.game.world.player;
  assert.ok(p1.vx !== 0, 'fixture must be saved while moving');
  assert.equal(g1.game.saveToSlot('1').ok, true);
  const g2 = await makeGame({ backend: g1.backend });
  assert.equal(g2.game.loadFromSlot('1').ok, true);
  const state = (g) => { const p = g.game.world.player; return JSON.stringify([p.x, p.y, p.vx, p.vy, g.game.world.rng.getState()]); };
  assert.equal(state(g2), state(g1));
  for (const [n, dx, dy] of [[30, 1, 0], [45, 0.6, -0.8], [60, 0, 0], [40, -1, 0.2]]) {
    g1.drive(n, intent(dx, dy));
    g2.drive(n, intent(dx, dy));
    assert.equal(state(g2), state(g1), `restored run diverged after ${n} ticks of (${dx}, ${dy})`);
  }
  assert.equal(g2.game.stepErrors, 0);
});

test('a player in motion keeps their velocity across save/load and then moves identically', async () => {
  const g1 = await makeGame({ backend: memoryBackend() });
  g1.newGame('vel-det', 'normal');
  const w1 = g1.game.world, p1 = w1.player;
  p1.vx = 37.5; p1.vy = -12.25;
  const snapBody = g1.RPG.persistence.snapshot(w1, { settings: g1.game.settings, name: 'x' });
  assert.equal(JSON.stringify(snapBody.player.vel), JSON.stringify({ x: 37.5, y: -12.25 }));
  const r = g1.RPG.persistence.restore(JSON.parse(JSON.stringify(snapBody)));
  assert.equal(r.ok, true, JSON.stringify(r));
  const p2 = r.world.player;
  assert.equal(p2.vx, 37.5);
  assert.equal(p2.vy, -12.25);
  // Older saves without `vel` load at rest.
  delete snapBody.player.vel;
  const r0 = g1.RPG.persistence.restore(snapBody);
  assert.equal(r0.ok, true, JSON.stringify(r0));
  assert.equal(r0.world.player.vx, 0);
  assert.equal(r0.world.player.vy, 0);
});

test('cleared encounter slots stay cleared after load', async () => {
  const g1 = await makeGame({ backend: memoryBackend() });
  g1.newGame('clear-det', 'normal');
  const w1 = g1.game.world;
  g1.RPG.world.areas.enter(w1, 'forest');
  const living = (w) => w.monsters.filter((m) => !m.dead).length;
  const before = living(w1);
  assert.ok(before > 0);
  // Kill every monster of the first occupied slot through the real death hook.
  const slot = w1.area.slotOf[w1.monsters.find((m) => !m.dead).id];
  const inSlot = w1.monsters.filter((m) => !m.dead && w1.area.slotOf[m.id] === slot);
  inSlot.forEach((m) => { m.dead = true; m.hp = 0; g1.RPG.world.areas.onMonsterDead(w1, m); });
  assert.equal(JSON.stringify(w1.cleared.forest), JSON.stringify([slot]));
  const after = living(w1);
  assert.equal(after, before - inSlot.length);
  const body = g1.RPG.persistence.snapshot(w1, { settings: g1.game.settings, name: 'x' });
  const r = g1.RPG.persistence.restore(JSON.parse(JSON.stringify(body)));
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(living(r.world), after, 'cleared slot respawned on load');
  assert.equal(JSON.stringify(r.world.cleared.forest), JSON.stringify([slot]));
});

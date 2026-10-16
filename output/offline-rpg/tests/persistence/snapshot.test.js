'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = path.resolve(__dirname, '../../src');
const FILES = [
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/entities.js', 'core/state.js',
  'data/maps/test-map.js', 'data/difficulties.js', 'data/weapons.js', 'data/skills.js', 'data/monsters.js',
  'data/items.js', 'data/areas.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/mapgen.js', 'world/loot.js',
  'world/equipment.js', 'world/progression.js', 'world/difficulty.js', 'world/skills.js',
  'sim/player.js', 'sim/sim.js',
  'persistence/migrate.js', 'persistence/save.js', 'persistence/snapshot.js',
];

function loadRPG() {
  const ctx = vm.createContext({});
  for (const f of FILES) vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), ctx, { filename: f });
  return ctx.RPG;
}

const RPG = loadRPG();
const P = RPG.persistence;
const plain = (x) => JSON.parse(JSON.stringify(x)); // vm objects live in another realm
const NOW = '2026-10-16T12:00:00.000Z';
const GAME = { settings: { volume: 0.6, muted: false, keys: { skills: ['KeyK'] } }, name: 'Tester', savedAt: NOW };

// A world exercising every schema field, built through the real game APIs.
function buildWorld() {
  const w = RPG.sim.createWorld({ seed: 123456789 });
  const p = w.player;
  w.difficulty = RPG.world.difficulty.resolve('hard');
  p.level = 7; p.xp = 340; p.gold = 512; p.attrs = { might: 8, agility: 6, spirit: 5 };
  const rng = new RPG.core.Rng(42);
  const chest = RPG.world.loot.generateItem(rng, { ilvl: 12, rarity: 'rare', baseId: 'chain-mail', uid: 101 });
  const vest = RPG.world.loot.generateItem(rng, { ilvl: 3, rarity: 'uncommon', baseId: 'leather-vest', uid: 102 });
  const sword = RPG.world.loot.generateItem(rng, { ilvl: 4, rarity: 'uncommon', baseId: 'sword', uid: 103 });
  const bow = RPG.world.loot.generateItem(rng, { ilvl: 4, rarity: 'rare', baseId: 'hunting-bow', uid: 104 });
  RPG.world.equipment.equip({ slots: p.equipment }, p.statBlock, chest);
  p.inventory[0] = vest; p.inventory[5] = Object.assign({}, vest, { uid: 105, qty: 3 });
  p.weaponSlots = [sword, bow, null]; p.activeWeapon = 1;
  p.skills.points = 12;
  p.stats = p.statBlock; // skills.js reads/writes modifiers on player.stats; the live game points it at the StatBlock
  RPG.world.skills.learnSkill(w, p, 'fireball');
  RPG.world.skills.learnSkill(w, p, 'vitality');
  RPG.world.skills.learnSkill(w, p, 'vitality');
  RPG.world.skills.learnSkill(w, p, 'focus');
  RPG.world.skills.upgradeSkill(w, p, 'fireball', 'bigger-explosion');
  RPG.world.skills.equipSkill(p, 'fireball', 2);
  p.skills.points += 3;
  p.cooldowns = { 'skill:fireball': 1.5 };
  RPG.sim.player.refreshStats(p);
  p.hp = 50.5; p.resource = 20;
  p.x += 16; p.y += 8;
  w.area.visited.forest = true;
  w.cleared = { forest: ['slot-3', 'slot-5'] };
  w.progress = { bossesDefeated: { 'warden-of-ash': true }, areasUnlocked: { town: true, forest: true }, kills: 211, deaths: 2, playtime: 1834.5 };
  w.rng.setState(987654321);
  w.lootRng = RPG.core.Rng.create(1122334455);
  return w;
}

function roundTrip(world, game) {
  const body = P.snapshot(world, game || GAME);
  const r = P.restore(P.deserialize(P.serialize(body)).state);
  assert.equal(r.ok, true, r.error);
  return { body, r };
}

test('snapshot produces a body that serialize accepts and validate passes', () => {
  const w = buildWorld();
  const body = P.snapshot(w, GAME);
  assert.deepEqual(plain(P.validate(body, { body: true })), []);
  assert.doesNotThrow(() => P.serialize(body));
  assert.equal(body.meta.difficulty, 'hard');
  assert.equal(body.meta.name, 'Tester');
});

test('round trip preserves every schema field', () => {
  const w = buildWorld();
  const { body, r } = roundTrip(w);
  const again = P.snapshot(r.world, Object.assign({}, r.game, { savedAt: NOW }));
  assert.deepEqual(plain(again), plain(body));
  // And spot-check each field group against the original world, not just snapshot==snapshot.
  const p = r.world.player, o = w.player;
  assert.equal(r.world.seed, 123456789);
  assert.equal(p.level, 7); assert.equal(p.xp, 340); assert.equal(p.gold, 512);
  assert.deepEqual(plain(p.attrs), { might: 8, agility: 6, spirit: 5 });
  assert.equal(p.hp, 50.5); assert.equal(p.resource, 20);
  assert.equal(p.x, o.x); assert.equal(p.y, o.y);
  assert.deepEqual(plain(p.weaponSlots), plain(o.weaponSlots)); assert.equal(p.activeWeapon, 1);
  assert.deepEqual(plain(p.equipment), plain(o.equipment));
  assert.deepEqual(plain(p.inventory), plain(o.inventory));
  assert.deepEqual(plain(RPG.world.skills.serialize(p)), plain(RPG.world.skills.serialize(o)));
  assert.equal(p.skills.points, o.skills.points); assert.equal(p.skills.spentPoints, o.skills.spentPoints);
  assert.deepEqual(plain(p.skills.bar), plain(o.skills.bar));
  assert.deepEqual(plain(p.cooldowns), { 'skill:fireball': 1.5 });
  assert.equal(r.world.difficulty.id, 'hard');
  assert.equal(r.world.difficulty.enemyHp, 1.5);
  assert.equal(r.world.area.id, w.area.id);
  assert.deepEqual(plain(r.world.area.visited), plain(w.area.visited));
  assert.deepEqual(plain(r.world.cleared), { forest: ['slot-3', 'slot-5'] });
  assert.deepEqual(plain(r.world.progress), plain(w.progress));
  assert.equal(r.world.rng.getState(), 987654321);
  assert.equal(r.world.lootRng.getState(), 1122334455);
  assert.deepEqual(plain(r.game.settings), plain(GAME.settings));
  assert.equal(r.game.name, 'Tester');
});

test('rng continues identically after restore', () => {
  const w = buildWorld();
  const { r } = roundTrip(w);
  for (let i = 0; i < 20; i++) {
    assert.equal(r.world.rng.next(), w.rng.next());
    assert.equal(r.world.lootRng.next(), w.lootRng.next());
  }
});

test('derived stats are identical after restore (items and skill passives reapplied)', () => {
  const w = buildWorld();
  const before = plain(w.player.stats.snapshot ? w.player.stats.snapshot() : w.player.stats);
  assert.ok(before.armor > RPG.sim.player.BASE_STATS.armor, 'fixture must actually carry item modifiers');
  assert.ok(before.maxHp > RPG.sim.player.BASE_STATS.maxHp, 'fixture must actually carry passive modifiers');
  const { r } = roundTrip(w);
  const p = r.world.player;
  assert.deepEqual(plain(p.statBlock.snapshot()), before);
  assert.deepEqual(plain(p.stats), before);
  assert.deepEqual(plain(p.statBlock.mods.map((m) => m.source).sort()), plain(w.player.statBlock.mods.map((m) => m.source).sort()));
  assert.equal(p.maxHp, before.maxHp);
});

test('restored world is playable: steps without error', () => {
  const { r } = roundTrip(buildWorld());
  const intent = { move: { x: 1, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} };
  const x0 = r.world.player.x;
  for (let i = 0; i < 30; i++) RPG.sim.step(r.world, intent, 1 / 60);
  assert.ok(r.world.player.x >= x0);
});

test('snapshot of an area from mapgen restores the same layout from the seed', () => {
  const w = buildWorld();
  const forest = RPG.data.areas.forest;
  const gen = RPG.world.mapgen.generate(forest, RPG.core.Rng.create(RPG.core.Rng.hashString(w.seed + ':forest')));
  w.area = { id: 'forest', map: gen.map, spawn: gen.spawn, exits: gen.exits, visited: { town: true, forest: true } };
  w.player.x = gen.spawn.x; w.player.y = gen.spawn.y;
  const { r } = roundTrip(w);
  assert.equal(r.world.area.id, 'forest');
  assert.deepEqual(plain(r.world.area.map.tiles), plain(gen.map.tiles));
  assert.equal(r.world.player.x, gen.spawn.x);
  assert.deepEqual(plain(r.warnings), []);
});

test('unknown area falls back to town; unwalkable position falls back to spawn; both warn', () => {
  const body = P.snapshot(buildWorld(), GAME);
  body.area.id = 'removed-area'; body.meta.area = 'removed-area';
  const r = P.restore(body);
  assert.equal(r.ok, true);
  assert.equal(r.world.area.id, 'town');
  assert.ok(r.warnings.some((m) => /unknown area/.test(m)));
  const b2 = P.snapshot(buildWorld(), GAME);
  b2.player.pos = { x: 0, y: 0 };
  const r2 = P.restore(b2);
  assert.equal(r2.ok, true);
  assert.ok(r2.warnings.some((m) => /not walkable/.test(m)));
  assert.deepEqual([r2.world.player.x, r2.world.player.y], [r2.world.area.spawn.x, r2.world.area.spawn.y]);
});

test('unknown items are dropped with a warning; unknown skills are refunded as points', () => {
  const body = P.snapshot(buildWorld(), GAME);
  body.player.inventory[0].baseId = 'removed-item';
  body.player.skills.learned['removed-skill'] = 2;
  body.player.skills.spentPoints += 2;
  const r = P.restore(body);
  assert.equal(r.ok, true);
  assert.equal(r.world.player.inventory[0], null);
  assert.ok(r.warnings.some((m) => /unknown item "removed-item"/.test(m)));
  assert.equal(r.world.player.skills.points, body.player.skills.points + 2);
  assert.ok(r.warnings.some((m) => /skills adjusted/.test(m)));
});

test('hp above max is clamped with a warning', () => {
  const body = P.snapshot(buildWorld(), GAME);
  body.player.hp = 99999;
  const r = P.restore(body);
  assert.equal(r.ok, true);
  assert.equal(r.world.player.hp, r.world.player.maxHp);
  assert.ok(r.warnings.some((m) => /hp clamped/.test(m)));
});

// ---- corrupt documents ----------------------------------------------------------------------------------------------

const CORRUPTIONS = {
  'not an object': () => 42,
  'garbage json text': () => '{nope',
  'wrong format magic': (d) => { d.format = 'other'; return d; },
  'newer version': (d) => { d.version = 99; return d; },
  'missing player': (d) => { delete d.player; return d; },
  'level out of range': (d) => { d.player.level = 31; return d; },
  'NaN-like hp (string)': (d) => { d.player.hp = 'lots'; return d; },
  'short inventory': (d) => { d.player.inventory.length = 5; return d; },
  'bad skill bar': (d) => { d.player.skills.bar = ['fireball']; return d; },
  'negative gold': (d) => { d.player.gold = -1; return d; },
  'missing rngState': (d) => { delete d.rngState; return d; },
  'seed not uint32': (d) => { d.seed = -5; return d; },
  'xp out of range for level': (d) => { d.player.xp = 999999; return d; },
  'unknown modifier kind': (d) => { d.player.equipment.chest.mods[0].kind = 'weird'; return d; },
  'unknown difficulty': (d) => { d.difficulty.id = 'impossible'; return d; },
  'item not an object': (d) => { d.player.inventory[1] = 'sword'; return d; },
};

function fullDoc() { return JSON.parse(P.serialize(P.snapshot(buildWorld(), GAME))); }

test('every corrupt document is rejected with ok:false and a code', () => {
  for (const [name, corrupt] of Object.entries(CORRUPTIONS)) {
    let input = corrupt(fullDoc());
    if (name === 'garbage json text') input = '{nope';
    const r = P.restore(input);
    assert.equal(r.ok, false, name);
    assert.ok(['parse', 'shape', 'version'].includes(r.code), name + ': ' + r.code);
    assert.equal(typeof r.error, 'string', name);
    assert.equal(r.world, undefined, name);
  }
});

test('corrupt document leaves the live world, the global data and the input untouched', () => {
  const live = buildWorld();
  const liveBefore = JSON.stringify(live), statsBefore = JSON.stringify(live.player.statBlock.snapshot());
  const dataBefore = JSON.stringify([RPG.data.items, RPG.data.skills, RPG.data.areas, RPG.data.difficulties]);
  for (const [name, corrupt] of Object.entries(CORRUPTIONS)) {
    const input = corrupt(fullDoc());
    const inputBefore = typeof input === 'string' ? input : JSON.stringify(input);
    assert.equal(P.restore(input).ok, false, name);
    assert.equal(typeof input === 'string' ? input : JSON.stringify(input), inputBefore, name + ': input mutated');
  }
  assert.equal(JSON.stringify(live), liveBefore);
  assert.equal(JSON.stringify(live.player.statBlock.snapshot()), statsBefore);
  assert.equal(JSON.stringify([RPG.data.items, RPG.data.skills, RPG.data.areas, RPG.data.difficulties]), dataBefore);
});

test('a failure part-way through building (late throw) still returns only an error', () => {
  const live = buildWorld(), before = JSON.stringify(live);
  const body = P.snapshot(live, GAME);
  const orig = RPG.sim.player.refreshStats;
  RPG.sim.player.refreshStats = () => { throw new Error('boom'); };
  try {
    const r = P.restore(body);
    assert.equal(r.ok, false);
    assert.equal(r.world, undefined);
    assert.match(r.error, /boom/);
  } finally { RPG.sim.player.refreshStats = orig; }
  assert.equal(JSON.stringify(live), before);
});

test('snapshot refuses to produce an invalid body', () => {
  const w = buildWorld();
  w.player.hp = NaN;
  assert.throws(() => P.snapshot(w, GAME), (e) => e.code === 'shape');
  assert.throws(() => P.snapshot(null, GAME), (e) => e.code === 'shape');
  const w2 = buildWorld(); w2.difficulty = null;
  assert.throws(() => P.snapshot(w2, GAME), (e) => e.code === 'shape');
});

test('checksum mismatch is a warning, not a rejection (hand-edited saves are legitimate)', () => {
  const doc = fullDoc();
  doc.player.gold = 600;
  const r = P.restore(doc);
  assert.equal(r.ok, true);
  assert.equal(r.world.player.gold, 600);
  assert.ok(r.warnings.some((m) => /checksum/.test(m)));
});

// ---- migration ------------------------------------------------------------------------------------------------------

test('a version-0 document (no rngState, stale checksum) migrates and restores', () => {
  const doc = fullDoc();
  doc.version = 0;
  delete doc.rngState;
  const r = P.restore(JSON.stringify(doc));
  assert.equal(r.ok, true, r.error);
  assert.equal(r.version, 0);
  assert.equal(r.world.rng.getState(), doc.seed);
  assert.equal(r.world.player.level, 7);
  assert.deepEqual(plain(r.world.player.equipment), plain(doc.player.equipment));
  assert.deepEqual(plain(r.world.player.statBlock.snapshot()), plain(buildWorld().player.statBlock.snapshot()));
  // The input object was not migrated in place.
  assert.equal(doc.version, 0);
});

test('migrated document that is still corrupt is rejected', () => {
  const doc = fullDoc();
  doc.version = 0; delete doc.rngState; doc.player.inventory.length = 3;
  assert.equal(P.restore(doc).ok, false);
});

test('unknown top-level keys are carried through game.extra and back into the next snapshot', () => {
  const doc = fullDoc(); doc.toolData = { a: 1 };
  const r = P.restore(doc);
  assert.equal(r.ok, true);
  assert.deepEqual(plain(r.game.extra), { toolData: { a: 1 } });
  const body = P.snapshot(r.world, r.game);
  assert.deepEqual(plain(JSON.parse(P.serialize(body)).toolData), { a: 1 });
});

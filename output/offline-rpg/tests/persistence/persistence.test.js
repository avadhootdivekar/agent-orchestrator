'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { load, sampleState, flakyBackend } = require('./helpers');

const plain = (x) => JSON.parse(JSON.stringify(x)); // vm objects live in another realm; compare as plain data
const P = load();
const slotKey = (s) => 'offline-rpg:v1:slot:' + s;

function mutated(fn) { const s = sampleState(); fn(s); return s; }
function docOf(state) { return JSON.parse(P.serialize(state)); }
function withChecksum(doc) { delete doc.checksum; doc.checksum = P.checksum(doc); return JSON.stringify(doc); }

test('headless load: no localStorage touched at load time', () => {
  const ctx = load({ localStorage: { getItem() { throw new Error('touched'); }, setItem() { throw new Error('touched'); } } });
  assert.equal(ctx.SAVE_VERSION, 1);
});

test('round-trip is exact, including seed', () => {
  const s = sampleState();
  const r = P.deserialize(P.serialize(s));
  assert.equal(r.ok, true);
  assert.deepEqual(plain(r.state), s);
  assert.equal(r.state.seed, 123456789);
  assert.deepEqual(plain(r.warnings), []);
});

test('round-trip: seed edge values, indent 2 export, and idempotent re-serialize', () => {
  for (const seed of [0, 1, 0xffffffff]) {
    const r = P.deserialize(P.serialize(mutated((s) => { s.seed = seed; })));
    assert.equal(r.state.seed, seed);
  }
  const s = sampleState();
  const text = P.exportJson(s);
  assert.match(text, /\n  "format"/);
  const r = P.importJson(text);
  assert.equal(r.ok, true);
  assert.equal(P.serialize(r.state), P.serialize(s));
});

test('serialize does not mutate its input and output carries format/version/checksum', () => {
  const s = sampleState(), before = JSON.stringify(s);
  const doc = docOf(s);
  assert.equal(JSON.stringify(s), before);
  assert.equal(doc.format, 'offline-rpg-save');
  assert.equal(doc.version, 1);
  assert.match(doc.checksum, /^fnv1a:[0-9a-f]{8}$/);
});

test('serialize refuses invalid state (NaN, wrong shape) instead of writing it', () => {
  assert.throws(() => P.serialize(mutated((s) => { s.player.hp = NaN; })), (e) => e.code === 'shape');
  assert.throws(() => P.serialize(mutated((s) => { s.player.inventory.length = 5; })), (e) => e.code === 'shape');
  assert.throws(() => P.serialize(null), (e) => e.code === 'shape');
});

test('unknown top-level keys survive a round trip via extra', () => {
  const doc = docOf(sampleState());
  doc.toolData = { a: 1 };
  const r = P.deserialize(withChecksum(doc));
  assert.deepEqual(plain(r.state.extra), { toolData: { a: 1 } });
  assert.equal(JSON.parse(P.serialize(r.state)).toolData.a, 1);
});

test('rejects non-JSON, truncated, empty and non-string input with specific codes', () => {
  const good = P.serialize(sampleState());
  assert.equal(P.deserialize(good.slice(0, good.length >> 1)).code, 'parse');
  assert.equal(P.deserialize('').code, 'parse');
  assert.equal(P.deserialize('not json').code, 'parse');
  assert.equal(P.deserialize(undefined).code, 'shape');
  assert.equal(P.deserialize(42).code, 'shape');
  assert.equal(P.deserialize(null).code, 'shape');
});

test('rejects wrong-type documents with code shape', () => {
  for (const t of ['[]', '42', 'null', '"x"', '{}', '{"format":"other","version":1}', '{"format":"offline-rpg-save"}',
    '{"format":"offline-rpg-save","version":"1"}']) {
    const r = P.deserialize(t);
    assert.equal(r.ok, false, t);
    assert.equal(r.code, 'shape', t);
  }
  const bad = [
    (d) => { d.seed = '123'; }, (d) => { d.seed = -1; }, (d) => { d.seed = 2 ** 32; }, (d) => { d.seed = 1.5; },
    (d) => { d.player = []; }, (d) => { d.player.level = 31; }, (d) => { d.player.level = '7'; },
    (d) => { d.player.inventory = d.player.inventory.slice(0, 23); }, (d) => { d.player.inventory[1] = 'sword'; },
    (d) => { d.player.inventory[0].mods = {}; }, (d) => { d.player.weaponSlots.pop(); }, (d) => { d.player.activeWeapon = 3; },
    (d) => { d.player.pos.x = null; }, (d) => { d.player.skills.bar = []; }, (d) => { delete d.area; }, (d) => { d.savedAt = 'yesterday'; },
    (d) => { d.progress.kills = -1; }, (d) => { d.rngState = 5; }, (d) => { d.settings = null; }
  ];
  bad.forEach((fn, i) => {
    const doc = docOf(sampleState()); fn(doc);
    const r = P.deserialize(withChecksum(doc));
    assert.equal(r.ok, false, 'case ' + i);
    assert.equal(r.code, 'shape', 'case ' + i);
    assert.ok(r.error.length > 0 && r.details.length > 0);
  });
});

test('rejects future-version saves with code version and never loads them', () => {
  const doc = docOf(sampleState()); doc.version = 2;
  const r = P.deserialize(withChecksum(doc));
  assert.equal(r.ok, false);
  assert.equal(r.code, 'version');
  doc.version = 0.5;
  assert.equal(P.deserialize(withChecksum(doc)).code, 'shape');
  doc.version = -1;
  assert.equal(P.deserialize(withChecksum(doc)).code, 'shape');
});

test('rejects oversized input with code size', () => {
  assert.equal(P.deserialize('x'.repeat(P.MAX_BYTES + 1)).code, 'size');
});

test('checksum mismatch is a warning only (hand-edited saves load)', () => {
  const doc = docOf(sampleState()); doc.player.gold = 999;
  const r = P.deserialize(JSON.stringify(doc));
  assert.equal(r.ok, true);
  assert.equal(r.state.player.gold, 999);
  assert.equal(r.warnings.length, 1);
  delete doc.checksum;
  assert.deepEqual(plain(P.deserialize(JSON.stringify(doc)).warnings), []);
});

test('migration: v0 save migrates to v1 and validates; original input untouched', () => {
  const doc = docOf(sampleState());
  doc.version = 0; delete doc.rngState; delete doc.checksum;
  const text = JSON.stringify(doc);
  const r = P.deserialize(text);
  assert.equal(r.ok, true);
  assert.equal(r.version, 0);
  assert.deepEqual(plain(r.state.rngState), { world: 123456789, loot: 123456789 });
  assert.equal(JSON.parse(P.serialize(r.state)).version, 1);
  assert.equal(JSON.stringify(doc), text);
  const raw = { version: 0, seed: 5 };
  const m = P.migrate(raw);
  assert.equal(m.version, 1);
  assert.equal(raw.version, 0);
});

test('migration registry: step chain runs in order; missing step and bad step are errors', () => {
  const calls = [];
  const ctx = load();
  // Simulate a future SAVE_VERSION=3 by exercising migrate with an extended registry in a fresh context.
  const src = fs.readFileSync(path.join(__dirname, '..', '..', 'src', 'persistence', 'migrate.js'), 'utf8').replace('var SAVE_VERSION = 1;', 'var SAVE_VERSION = 3;');
  const vm = require('node:vm');
  const c = vm.createContext({ RPG: {} });
  vm.runInContext(src, c);
  const M = c.RPG.persistence;
  M.MIGRATIONS[1] = (s) => { calls.push(1); s.version = 2; return s; };
  assert.throws(() => M.migrate({ version: 1 }), (e) => e.code === 'version'); // no 2 -> 3 step
  M.MIGRATIONS[2] = (s) => { calls.push(2); s.version = 3; return s; };
  assert.equal(M.migrate({ version: 0 }).version, 3);
  assert.deepEqual(calls, [1, 1, 2].slice(0, 1).concat([1, 2]));
  M.MIGRATIONS[2] = (s) => s;
  assert.throws(() => M.migrate({ version: 2 }), (e) => e.code === 'version');
  assert.ok(ctx.MIGRATIONS[0]);
});

test('saveGame/loadGame round trip through memory fallback storage', () => {
  const st = P.storage.createStorage();
  assert.equal(typeof st.available(), 'boolean');
  const s = sampleState();
  assert.equal(P.saveGame('1', s, { storage: st }).ok, true);
  const r = P.loadGame('1', { storage: st });
  assert.equal(r.ok, true);
  assert.deepEqual(plain(r.state), s);
  assert.deepEqual(plain(st.list().map((x) => [x.slot, x.level, x.area])), [['1', 7, 'forest']]);
  assert.equal(P.loadGame('2', { storage: st }).code, 'missing');
  assert.equal(P.deleteSave('1', { storage: st }).ok, true);
  assert.equal(P.loadGame('1', { storage: st }).code, 'missing');
});

test('storage falls back to memory (available() false) when localStorage is absent or throws', () => {
  assert.equal(P.storage.createStorage().available(), false);
  const throwing = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() {} };
  const P2 = load({ localStorage: throwing });
  const st = P2.storage.createStorage();
  assert.equal(st.available(), false);
  assert.equal(P2.saveGame('auto', sampleState(), { storage: st }).ok, true);
  assert.equal(P2.loadGame('auto', { storage: st }).ok, true);
});

test('storage uses a working localStorage and the documented key', () => {
  const b = flakyBackend();
  const P2 = load({ localStorage: b });
  const st = P2.storage.createStorage();
  assert.equal(st.available(), true);
  P2.saveGame('2', sampleState(), { storage: st });
  assert.ok(b.map.has(slotKey('2')));
  assert.equal(b.map.has(slotKey('2') + ':tmp'), false);
  assert.throws(() => st.read('9'), /unknown slot/);
});

test('failed write (quota) keeps the old save intact, at every failure point', () => {
  const old = sampleState();
  const next = mutated((s) => { s.player.gold = 9999; });
  for (const mode of ['all', 'tmp', 'main', 'after1']) {
    const b = flakyBackend(), st = P.storage.createStorage({ backend: b });
    assert.equal(P.saveGame('1', old, { storage: st }).ok, true);
    const before = b.map.get(slotKey('1'));
    if (mode === 'all') b.failAll = true;
    if (mode === 'tmp') b.failKey = ':tmp';
    if (mode === 'after1') b.failAfter = b.writes + 1; // tmp write succeeds, main write fails
    if (mode === 'main') { const orig = b.setItem; b.setItem = (k, v) => { if (!k.endsWith(':tmp')) throw new Error('Quota'); orig(k, v); }; }
    const r = P.saveGame('1', next, { storage: st });
    assert.equal(r.ok, false, mode);
    assert.equal(r.code, 'storage', mode);
    assert.equal(b.map.get(slotKey('1')), before, mode);
    assert.equal(b.map.has(slotKey('1') + ':tmp'), false, mode);
    const l = P.loadGame('1', { storage: st });
    assert.equal(l.ok, true, mode);
    assert.equal(l.state.player.gold, 512, mode);
  }
});

test('write that stores damaged data is detected and the old save is restored', () => {
  const b = flakyBackend(), st = P.storage.createStorage({ backend: b });
  P.saveGame('1', sampleState(), { storage: st });
  const before = b.map.get(slotKey('1'));
  const orig = b.setItem;
  b.setItem = (k, v) => orig(k, v.slice(0, 20)); // silently truncating storage
  const r = P.saveGame('1', mutated((s) => { s.player.gold = 1; }), { storage: st });
  b.setItem = orig;
  assert.equal(r.ok, false);
  assert.equal(b.map.get(slotKey('1')), before);
});

test('saving an invalid state never touches the stored save', () => {
  const b = flakyBackend(), st = P.storage.createStorage({ backend: b });
  P.saveGame('1', sampleState(), { storage: st });
  const before = b.map.get(slotKey('1')), writes = b.writes;
  const r = P.saveGame('1', mutated((s) => { s.seed = 'x'; }), { storage: st });
  assert.equal(r.ok, false);
  assert.equal(r.code, 'shape');
  assert.equal(b.writes, writes);
  assert.equal(b.map.get(slotKey('1')), before);
});

test('failed import leaves the stored save and import never writes', () => {
  const b = flakyBackend(), st = P.storage.createStorage({ backend: b });
  P.saveGame('1', sampleState(), { storage: st });
  const snapshot = JSON.stringify([...b.map]), writes = b.writes;
  const good = P.exportJson(sampleState());
  for (const text of [good.slice(0, 200), '{"format":"offline-rpg-save","version":99}', '[]', '']) {
    assert.equal(P.importJson(text).ok, false);
  }
  assert.equal(P.importJson(good).ok, true);
  assert.equal(b.writes, writes);
  assert.equal(JSON.stringify([...b.map]), snapshot);
  assert.equal(P.importJson('﻿' + good).ok, true);
});

test('corrupt slot is rejected with a code and moved aside; future-version slot is left alone', () => {
  const b = flakyBackend(), st = P.storage.createStorage({ backend: b });
  b.map.set(slotKey('1'), '{"format":"offline-rpg-save","version":1,"seed":');
  const r = P.loadGame('1', { storage: st });
  assert.equal(r.ok, false);
  assert.equal(r.code, 'parse');
  assert.equal(b.map.has(slotKey('1')), false);
  assert.ok(b.map.get(slotKey('1') + ':corrupt').startsWith('{"format"'));
  const doc = docOf(sampleState()); doc.version = 7;
  b.map.set(slotKey('2'), withChecksum(doc));
  const f = P.loadGame('2', { storage: st });
  assert.equal(f.code, 'version');
  assert.ok(b.map.has(slotKey('2')));
});

test('interrupted write: leftover :tmp is recovered when the main key is missing; ignored when main is fine', () => {
  const b = flakyBackend(), st = P.storage.createStorage({ backend: b });
  b.map.set(slotKey('3') + ':tmp', P.serialize(sampleState()));
  const r = P.loadGame('3', { storage: st });
  assert.equal(r.ok, true);
  assert.equal(r.warnings.length, 1);
  P.saveGame('auto', sampleState(), { storage: st });
  b.map.set(slotKey('auto') + ':tmp', 'garbage');
  assert.equal(P.loadGame('auto', { storage: st }).ok, true);
  P.deleteSave('auto', { storage: st });
  assert.equal(b.map.has(slotKey('auto') + ':tmp'), false);
});

test('docs/sample-game-save.json validates and loads', () => {
  const text = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'sample-game-save.json'), 'utf8');
  const r = P.deserialize(text);
  assert.equal(r.ok, true, r.error);
  assert.deepEqual(plain(r.warnings), []);
  assert.equal(r.state.player.level, 5);
  assert.equal(r.state.area.id, 'forest');
  assert.equal(r.state.player.weaponSlots.filter(Boolean).length, 3);
  assert.equal(r.state.player.inventory.filter(Boolean).length, 6);
  assert.equal(Object.keys(r.state.player.skills.learned).length, 3);
  assert.equal(P.serialize(r.state, { indent: 2 }) + '\n', text);
  const st = P.storage.createStorage();
  assert.equal(P.saveGame('1', r.state, { storage: st }).ok, true);
});

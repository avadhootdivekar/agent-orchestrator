'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'world/stats.js']);
const { StatBlock } = RPG.world;

function plain(v) { return JSON.parse(JSON.stringify(v)); }

test('defaults match DATA-DEFINITIONS base values', () => {
  const s = new StatBlock();
  assert.equal(s.get('maxHp'), 100);
  assert.equal(s.get('moveSpeed'), 170);
  assert.equal(s.get('critChance'), 0.05);
  assert.equal(s.get('unknownStat'), 0);
});

test('flat applies before inc, then more multiplies', () => {
  const s = new StatBlock();
  s.addModifier({ source: 'item:a', stat: 'damage', kind: 'flat', value: 6 });   // 4+6 = 10
  s.addModifier({ source: 'item:b', stat: 'damage', kind: 'inc', value: 0.5 });  // *1.5 = 15
  s.addModifier({ source: 'buff:x', stat: 'damage', kind: 'inc', value: 0.5 });  // inc sums: *2 = 20
  s.addModifier({ source: 'skill:y', stat: 'damage', kind: 'more', value: 0.1 });
  s.addModifier({ source: 'skill:z', stat: 'damage', kind: 'more', value: 0.1 }); // *1.21
  assert.ok(Math.abs(s.get('damage') - 20 * 1.21) < 1e-9);
});

test('percent is an alias of inc and order of adding does not matter', () => {
  const a = new StatBlock(), b = new StatBlock();
  a.addModifier({ source: 's', stat: 'armor', kind: 'percent', value: 1 });
  a.addModifier({ source: 's', stat: 'armor', kind: 'flat', value: 5 });
  b.addModifier({ source: 's', stat: 'armor', kind: 'flat', value: 5 });
  b.addModifier({ source: 's', stat: 'armor', kind: 'inc', value: 1 });
  assert.equal(a.get('armor'), 20);
  assert.equal(b.get('armor'), 20);
});

test('caps are enforced; floors at zero', () => {
  const s = new StatBlock();
  s.addModifier({ source: 'a', stat: 'moveSpeed', kind: 'flat', value: 500 });
  s.addModifier({ source: 'a', stat: 'critChance', kind: 'flat', value: 5 });
  s.addModifier({ source: 'a', stat: 'cooldownReduction', kind: 'flat', value: 0.9 });
  s.addModifier({ source: 'a', stat: 'lifeSteal', kind: 'flat', value: 1 });
  s.addModifier({ source: 'a', stat: 'resFire', kind: 'flat', value: 2 });
  s.addModifier({ source: 'a', stat: 'armor', kind: 'flat', value: -100 });
  assert.equal(s.get('moveSpeed'), 320);
  assert.equal(s.get('critChance'), 0.75);
  assert.equal(s.get('cooldownReduction'), 0.5);
  assert.equal(s.get('lifeSteal'), 0.25);
  assert.equal(s.get('resFire'), 0.75);
  assert.equal(s.get('armor'), 0);
});

test('removeModifiersBySource removes only that source', () => {
  const s = new StatBlock();
  s.addModifier({ source: 'item:weapon', stat: 'damage', kind: 'flat', value: 10 });
  s.addModifier({ source: 'item:weapon', stat: 'critChance', kind: 'flat', value: 0.1 });
  s.addModifier({ source: 'item:weapon2', stat: 'damage', kind: 'flat', value: 3 });
  s.addModifier({ source: 'buff:rage', stat: 'damage', kind: 'inc', value: 1 });
  assert.equal(s.removeModifiersBySource('item:weapon'), 2);
  assert.equal(s.get('damage'), (4 + 3) * 2);
  assert.equal(s.get('critChance'), 0.05);
  assert.equal(s.removeModifiersBySource('item:weapon'), 0);
});

test('invalid input is rejected', () => {
  const s = new StatBlock();
  assert.throws(() => s.addModifier({ source: 'a', stat: 'damage', kind: 'bogus', value: 1 }), { name: 'TypeError' });
  assert.throws(() => s.addModifier({ source: 'a', stat: 'damage', kind: 'flat', value: NaN }), { name: 'TypeError' });
  assert.throws(() => s.addModifier({ stat: 'damage', kind: 'flat', value: 1 }), { name: 'TypeError' });
});

test('serialize/restore round-trips exactly, including through JSON', () => {
  const s = new StatBlock({ maxHp: 250 });
  s.addModifier({ source: 'level', stat: 'maxHp', kind: 'flat', value: 24 });
  s.addModifier({ source: 'item:body', stat: 'armor', kind: 'inc', value: 0.25 });
  s.addModifier({ source: 'buff:x', stat: 'damage', kind: 'more', value: 0.2 });
  const data = plain(s.serialize());
  const r = StatBlock.restore(data);
  assert.deepEqual(plain(r.serialize()), data);
  assert.deepEqual(plain(r.snapshot()), plain(s.snapshot()));
  assert.equal(r.get('maxHp'), 274);
  r.removeModifiersBySource('buff:x');
  assert.equal(s.get('damage') > r.get('damage'), true, 'restored block is independent of the original');
});

test('snapshot contains every base stat and derived values', () => {
  const s = new StatBlock();
  s.addModifier({ source: 'a', stat: 'customStat', kind: 'flat', value: 3 });
  const snap = s.snapshot();
  assert.equal(snap.maxHp, 100);
  assert.equal(snap.customStat, 3);
});

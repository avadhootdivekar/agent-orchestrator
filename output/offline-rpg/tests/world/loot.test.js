'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'core/rng.js', 'data/items.js', 'data/difficulties.js',
  'data/monsters.js', 'world/loot.js']);
const { Rng } = RPG.core;
const { loot } = RPG.world;
const plain = (v) => JSON.parse(JSON.stringify(v));

test('same seed gives the same items and drops', () => {
  const run = (seed) => {
    const rng = new Rng(seed);
    const items = [];
    for (let i = 0; i < 50; i++) items.push(loot.generateItem(rng, { areaLevel: 12 }));
    const drops = loot.rollDrops(rng, RPG.data.monsters['stone-brute'] ? RPG.data.monsters['stone-brute'].lootTable : { dropChance: 1, rolls: 3 }, { areaLevel: 5 });
    return plain({ items, drops });
  };
  assert.deepEqual(run(1234), run(1234));
  assert.notDeepEqual(run(1234), run(4321));
});

test('rarity distribution over 2000 rolls is ordered common most to legendary least', () => {
  const rng = new Rng(99);
  const counts = { common: 0, uncommon: 0, rare: 0, epic: 0, legendary: 0 };
  for (let i = 0; i < 2000; i++) counts[loot.rollRarity(rng, { areaLevel: 1, lootQuality: 1 })]++;
  assert.ok(counts.common > counts.uncommon, JSON.stringify(counts));
  assert.ok(counts.uncommon > counts.rare, JSON.stringify(counts));
  assert.ok(counts.rare > counts.epic, JSON.stringify(counts));
  assert.ok(counts.epic > counts.legendary, JSON.stringify(counts));
});

test('higher loot quality shifts weight toward rarer items', () => {
  const w = (q) => loot.rarityWeights({ lootQuality: q, areaLevel: 1 });
  const share = (t) => t.find((e) => e.id === 'legendary').weight / t.reduce((a, e) => a + e.weight, 0);
  assert.ok(share(w(1.6)) > share(w(1)));
  assert.ok(share(w(1)) > share(w(0.8)));
});

test('modifiers stay in range, are distinct, allowed for the slot, and match the rarity count', () => {
  const rng = new Rng(7);
  for (let i = 0; i < 300; i++) {
    const it = loot.generateItem(rng, { ilvl: 15, rarity: 'epic', slot: 'ring' });
    const def = RPG.data.items[it.baseId];
    assert.equal(def.slot, 'ring');
    assert.ok(it.mods.length >= 3 && it.mods.length <= 4);
    assert.equal(new Set(it.mods.map((m) => m.id)).size, it.mods.length);
    for (const m of it.mods) {
      const p = RPG.data.modifierPool[m.id];
      assert.ok(p.allowedSlots.includes('ring'));
      const lo = Math.min(...p.ranges.map((r) => r.min)), hi = Math.max(...p.ranges.map((r) => r.max));
      assert.ok(m.value >= lo && m.value <= hi);
    }
  }
});

test('common has no mods, legendary has a unique property and a name', () => {
  const rng = new Rng(3);
  const c = loot.generateItem(rng, { ilvl: 1, rarity: 'common', baseId: 'sword' });
  assert.deepEqual(plain(c.mods), []);
  assert.equal(c.name, 'Sword');
  const l = loot.generateItem(rng, { ilvl: 1, rarity: 'legendary', baseId: 'sword' });
  assert.ok(l.mods.some((m) => m.id === 'unique'));
  assert.ok(l.name.endsWith('Sword') && l.name !== 'Sword');
  assert.equal(typeof l.uid, 'number');
  assert.match(l.seedTag, /^loot:/);
});

test('ilvl restricts which bases can drop; consumables are never rolled as gear', () => {
  const rng = new Rng(5);
  for (let i = 0; i < 200; i++) {
    const it = loot.generateItem(rng, { ilvl: 1 });
    const d = RPG.data.items[it.baseId];
    assert.ok(d.ilvlMin <= 1 && d.slot !== 'consumable');
  }
});

test('drop tables honour dropChance, guaranteed ids and difficulty loot quality', () => {
  const never = loot.rollDrops(new Rng(1), { dropChance: 0, rolls: 3 }, {});
  assert.equal(never.length, 0);
  const g = loot.rollDrops(new Rng(1), { dropChance: 1, rolls: 0, guaranteed: ['health-potion'] }, {});
  assert.deepEqual(plain(g.map((i) => i.baseId)), ['health-potion']);
  const count = (diff) => {
    const rng = new Rng(11);
    let n = 0;
    for (let i = 0; i < 500; i++) n += loot.rollDrops(rng, { dropChance: 0.5, rolls: 1 }, { difficulty: diff }).length;
    return n;
  };
  assert.ok(count(RPG.data.difficulties.nightmare) > count(RPG.data.difficulties.easy));
  assert.ok(Array.isArray(loot.rollMonsterDrops(new Rng(1), RPG.data.monsters['stone-brute'], {})));
});

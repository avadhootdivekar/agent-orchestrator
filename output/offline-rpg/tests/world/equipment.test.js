'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'core/rng.js', 'data/items.js', 'world/stats.js',
  'world/loot.js', 'world/equipment.js']);
const { Rng } = RPG.core;
const { StatBlock, loot, equipment } = RPG.world;
const plain = (v) => JSON.parse(JSON.stringify(v));

const make = (seed, opts) => loot.generateItem(new Rng(seed), opts);

test('equip changes stats and unequip restores them exactly', () => {
  const sb = new StatBlock();
  const eq = equipment.createEquipment();
  const before = plain(sb.snapshot());
  const chest = make(1, { ilvl: 12, rarity: 'rare', baseId: 'chain-mail' });
  assert.equal(equipment.equip(eq, sb, chest), null);
  assert.ok(sb.get('armor') > before.armor);
  assert.ok(sb.mods.every((m) => m.source === 'item:' + chest.uid));
  assert.equal(equipment.unequip(eq, sb, 'chest'), chest);
  assert.deepEqual(plain(sb.snapshot()), before);
  assert.equal(sb.mods.length, 0);
  assert.equal(equipment.unequip(eq, sb, 'chest'), null);
});

test('swap replaces the slot item and removes the old modifiers', () => {
  const sb = new StatBlock();
  const eq = equipment.createEquipment();
  const a = make(2, { ilvl: 1, rarity: 'uncommon', baseId: 'leather-vest' });
  const b = make(3, { ilvl: 1, rarity: 'uncommon', baseId: 'leather-vest' });
  b.uid = a.uid + 1;
  equipment.equip(eq, sb, a);
  const armorA = sb.get('armor');
  assert.equal(equipment.swap(eq, sb, b), a);
  assert.equal(eq.slots.chest, b);
  assert.ok(!sb.mods.some((m) => m.source === 'item:' + a.uid));
  equipment.unequip(eq, sb, 'chest');
  assert.equal(sb.get('armor'), new StatBlock().get('armor'));
  assert.ok(armorA > new StatBlock().get('armor'));
});

test('several slots stack and unequipping all returns the base snapshot', () => {
  const sb = new StatBlock();
  const eq = equipment.createEquipment();
  const base = plain(sb.snapshot());
  const rng = new Rng(21);
  let uid = 100;
  for (const slot of ['weapon', 'head', 'boots', 'ring', 'amulet']) {
    equipment.equip(eq, sb, loot.generateItem(rng, { ilvl: 12, rarity: 'legendary', slot, uid: uid++ }));
  }
  assert.notDeepEqual(plain(sb.snapshot()), base);
  for (const slot of Object.keys(eq.slots)) equipment.unequip(eq, sb, slot);
  assert.deepEqual(plain(sb.snapshot()), base);
});

test('consumables cannot be equipped', () => {
  const potion = make(4, { baseId: 'health-potion' });
  assert.equal(equipment.isEquippable(potion), false);
  assert.throws(() => equipment.equip(equipment.createEquipment(), new StatBlock(), potion));
});

test('compare diffs derived stats against the current item without mutating anything', () => {
  const sb = new StatBlock();
  const eq = equipment.createEquipment();
  const low = make(5, { ilvl: 1, rarity: 'common', baseId: 'leather-vest' });
  const high = make(6, { ilvl: 12, rarity: 'common', baseId: 'chain-mail' });
  equipment.equip(eq, sb, low);
  const snap = plain(sb.snapshot());
  const cmp = equipment.compare(eq, sb, high);
  assert.equal(cmp.slot, 'chest');
  assert.equal(cmp.current, low);
  assert.equal(cmp.deltas.armor.diff, 8);
  assert.deepEqual(plain(sb.snapshot()), snap);
  assert.equal(eq.slots.chest, low);
});

test('applyAll rebuilds modifiers on a fresh stat block', () => {
  const sb = new StatBlock();
  const eq = equipment.createEquipment();
  equipment.equip(eq, sb, make(8, { ilvl: 12, rarity: 'epic', baseId: 'iron-helm' }));
  const fresh = new StatBlock();
  equipment.applyAll(eq, fresh);
  assert.deepEqual(plain(fresh.snapshot()), plain(sb.snapshot()));
});

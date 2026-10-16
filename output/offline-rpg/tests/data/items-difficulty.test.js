'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');

// The data files need the namespace and util they depend on, loaded first, as in the built page.
const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'data/items.js', 'data/difficulties.js']);

// Objects and arrays built inside the vm realm fail deepStrictEqual against host literals; round-trip to plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));
const { items, rarities, modifierPool, itemSlots, difficulties, difficultyMultiplierFields } = {
  items: RPG.data.items,
  rarities: RPG.data.rarities,
  modifierPool: RPG.data.modifierPool,
  itemSlots: RPG.data.itemSlots,
  difficulties: RPG.data.difficulties,
  difficultyMultiplierFields: RPG.data.difficultyMultiplierFields,
};

// Stat keys a modifier may target (DATA-DEFINITIONS §1 StatMap).
const STAT_KEYS = new Set(['damage', 'maxHp', 'maxResource', 'armor', 'critChance', 'attackSpeed', 'moveSpeed',
  'skillDamage', 'resourceRegen', 'lifeSteal']);
const RARITY_ORDER = ['common', 'uncommon', 'rare', 'epic', 'legendary'];
const PRESET_ORDER = ['easy', 'normal', 'hard', 'nightmare'];
// Direction each multiplier moves from easy to nightmare. bossTelegraph shrinks: the telegraph gets shorter.
const EXPECTED_DIRECTION = {
  enemyHp: 'up', enemyDamage: 'up', enemySpeed: 'up', spawnDensity: 'up', eliteChance: 'up',
  bossHp: 'up', bossTelegraph: 'down', lootQuality: 'up', xpMultiplier: 'up', goldMultiplier: 'up',
};

test('five rarities, ordered Common to Legendary', () => {
  assert.deepEqual(plain(Object.keys(rarities)), RARITY_ORDER);
  const ranks = RARITY_ORDER.map((id) => rarities[id].rank);
  assert.deepEqual(ranks, [0, 1, 2, 3, 4]);
});

test('rarity tiers never fall as rarity rises', () => {
  const list = RARITY_ORDER.map((id) => rarities[id]);
  for (let i = 1; i < list.length; i++) {
    const lo = list[i - 1];
    const hi = list[i];
    assert.ok(hi.modCount[0] >= lo.modCount[0], `${hi.id} min modCount below ${lo.id}`);
    assert.ok(hi.modCount[1] >= lo.modCount[1], `${hi.id} max modCount below ${lo.id}`);
    assert.ok(hi.modTierBoost >= lo.modTierBoost, `${hi.id} modTierBoost below ${lo.id}`);
    assert.ok(hi.dropWeight < lo.dropWeight, `${hi.id} should drop less often than ${lo.id}`);
  }
  for (const r of list) assert.ok(r.modCount[0] <= r.modCount[1], `${r.id} modCount min > max`);
  assert.equal(rarities.legendary.uniqueProperty, true);
  assert.equal(rarities.common.modCount[1], 0, 'common items roll no modifiers');
});

test('nine modifier kinds with well-formed ranges', () => {
  assert.deepEqual(plain(Object.keys(modifierPool).sort()), ['armor', 'attackSpeed', 'crit', 'dmg', 'hp', 'lifeSteal',
    'moveSpeed', 'resRegen', 'skillDmg']);
  for (const mod of Object.values(modifierPool)) {
    assert.ok(STAT_KEYS.has(mod.stat), `${mod.id} targets unknown stat ${mod.stat}`);
    assert.ok(mod.kind === 'flat' || mod.kind === 'inc', `${mod.id} kind ${mod.kind}`);
    assert.ok(mod.weight > 0, `${mod.id} weight must be positive`);
    assert.ok(mod.allowedSlots.length > 0, `${mod.id} has no allowed slots`);
    for (const slot of mod.allowedSlots) assert.ok(itemSlots.includes(slot), `${mod.id} unknown slot ${slot}`);
    // One roll range per ilvl bracket, starting ilvl ascending, each min <= max.
    assert.equal(mod.ranges.length, 3, `${mod.id} needs a range for each of the 3 ilvl brackets`);
    assert.deepEqual(plain(mod.ranges.map((r) => r.ilvlMin)), [1, 11, 21]);
    for (const r of mod.ranges) assert.ok(r.min <= r.max, `${mod.id} bracket ${r.ilvlMin} min > max`);
    // Higher brackets never roll weaker than lower ones.
    for (let i = 1; i < mod.ranges.length; i++) {
      assert.ok(mod.ranges[i].max >= mod.ranges[i - 1].max, `${mod.id} weakens at bracket ${mod.ranges[i].ilvlMin}`);
    }
  }
});

test('base items are well-formed and cover every equipment slot', () => {
  const coveredSlots = new Set(Object.values(items).map((i) => i.slot));
  for (const slot of itemSlots) assert.ok(coveredSlots.has(slot), `no base item for slot ${slot}`);
  for (const [key, def] of Object.entries(items)) {
    assert.equal(def.id, key, `${key} id mismatch`);
    assert.ok(def.name, `${key} has no name`);
    assert.ok(itemSlots.includes(def.slot) || def.slot === 'consumable', `${key} unknown slot ${def.slot}`);
    assert.ok(def.ilvlMin >= 1 && def.ilvlMin <= def.ilvlMax, `${key} bad ilvl range`);
    assert.ok(def.weight > 0, `${key} weight must be positive`);
    if (def.slot === 'weapon') assert.ok(def.weaponId, `${key} weapon has no weaponId`);
    for (const stat of Object.keys(def.base.stats)) assert.ok(STAT_KEYS.has(stat), `${key} base unknown stat ${stat}`);
    if (def.slot === 'consumable') assert.ok(def.consumable && def.stackable, `${key} consumable needs effect and stacking`);
  }
});

test('every preset defines all multipliers and the death penalty', () => {
  const required = [...difficultyMultiplierFields, 'playerDeathPenalty', 'id', 'name'].sort();
  assert.equal(difficultyMultiplierFields.length, 10, 'multiplier field set changed');
  for (const id of [...PRESET_ORDER, 'custom']) {
    // Only custom carries slider ranges; presets are the fixed values.
    const expected = id === 'custom' ? [...required, 'ranges'].sort() : required;
    assert.deepEqual(plain(Object.keys(difficulties[id]).sort()), expected, `${id} field set`);
    for (const field of difficultyMultiplierFields) {
      assert.equal(typeof difficulties[id][field], 'number', `${id}.${field} not a number`);
      assert.ok(Number.isFinite(difficulties[id][field]), `${id}.${field} not finite`);
    }
  }
});

test('difficulty multipliers are strictly monotonic from Easy to Nightmare', () => {
  for (const field of difficultyMultiplierFields) {
    const values = PRESET_ORDER.map((id) => difficulties[id][field]);
    for (let i = 1; i < values.length; i++) {
      const ok = EXPECTED_DIRECTION[field] === 'up' ? values[i] > values[i - 1] : values[i] < values[i - 1];
      assert.ok(ok, `${field} not strictly ${EXPECTED_DIRECTION[field]} at ${PRESET_ORDER[i]}: ${values.join(', ')}`);
    }
  }
});

test('custom slider ranges bracket the baseline and every preset', () => {
  const { ranges } = difficulties.custom;
  for (const field of difficultyMultiplierFields) {
    const [lo, hi] = ranges[field];
    assert.ok(lo < hi, `${field} range is empty`);
    assert.ok(difficulties.custom[field] >= lo && difficulties.custom[field] <= hi, `${field} baseline outside range`);
    for (const id of PRESET_ORDER) {
      const v = difficulties[id][field];
      assert.ok(v >= lo && v <= hi, `${id}.${field}=${v} outside custom range [${lo}, ${hi}]`);
    }
  }
});

test('data is frozen, so presets cannot drift at runtime', () => {
  assert.ok(Object.isFrozen(items.sword));
  assert.ok(Object.isFrozen(rarities.epic));
  assert.ok(Object.isFrozen(modifierPool.dmg.ranges[0]));
  assert.ok(Object.isFrozen(difficulties.hard));
});

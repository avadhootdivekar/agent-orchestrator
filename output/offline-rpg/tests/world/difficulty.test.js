'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'data/difficulties.js', 'data/monsters.js', 'world/difficulty.js']);
const diff = RPG.world.difficulty;
const presets = RPG.data.difficulties;
const monsters = Object.values(RPG.data.monsters);

const PRESET_IDS = ['easy', 'normal', 'hard', 'nightmare'];
// Every multiplier the data defines, in the order the presets should rise through.
const RISING = ['enemyHp', 'enemyDamage', 'enemySpeed', 'spawnDensity', 'eliteChance', 'bossHp', 'lootQuality', 'xpMultiplier', 'goldMultiplier'];
// Telegraph shrinks as difficulty rises (DATA-DEFINITIONS §2), so it falls rather than rises.
const FALLING = ['bossTelegraph'];

// Objects built inside the vm context have another realm's prototypes, so compare them as plain JSON.
const plain = (v) => JSON.parse(JSON.stringify(v));

test('all four presets and custom resolve to a full config with the matching id', () => {
  for (const id of [...PRESET_IDS, 'custom']) {
    const cfg = diff.resolve(id);
    assert.equal(cfg.id, id);
    for (const field of [...RISING, ...FALLING]) {
      assert.ok(Number.isFinite(cfg[field]), `${id}.${field} must be a finite number`);
    }
    assert.ok(['none', 'gold10', 'gold25'].includes(cfg.playerDeathPenalty), `${id} death penalty`);
  }
});

test('every preset sits inside the custom slider ranges, so presets are reachable through custom', () => {
  for (const id of PRESET_IDS) {
    for (const [field, [lo, hi]] of Object.entries(presets.custom.ranges)) {
      assert.ok(presets[id][field] >= lo && presets[id][field] <= hi, `${id}.${field}=${presets[id][field]} outside [${lo}, ${hi}]`);
    }
  }
});

test('custom is clamped to the slider ranges, and missing fields take the custom baseline', () => {
  const ranges = presets.custom.ranges;
  const cfg = diff.clampCustom({ enemyHp: 99, enemySpeed: 0.01, eliteChance: -1, bossTelegraph: 9 });
  assert.equal(cfg.enemyHp, ranges.enemyHp[1]);
  assert.equal(cfg.enemySpeed, ranges.enemySpeed[0]);
  assert.equal(cfg.eliteChance, ranges.eliteChance[0]);
  assert.equal(cfg.bossTelegraph, ranges.bossTelegraph[1]);
  assert.equal(cfg.lootQuality, presets.custom.lootQuality, 'untouched fields keep the baseline');
  assert.equal(cfg.playerDeathPenalty, presets.custom.playerDeathPenalty);
});

test('resolve clamps a custom config object the same way, and keeps a saved config\'s own numbers', () => {
  const clamped = diff.resolve({ id: 'custom', enemyDamage: 50 });
  assert.equal(clamped.enemyDamage, presets.custom.ranges.enemyDamage[1]);

  // A save stores the resolved config; loading it must not be rewritten by later preset edits.
  const saved = plain(diff.resolve('hard'));
  saved.enemyHp = 1.9;
  assert.equal(diff.resolve(saved).enemyHp, 1.9);
  assert.equal(diff.resolve(saved).id, 'hard');
});

test('custom rejects non-numeric sliders and unknown death penalties instead of guessing', () => {
  assert.throws(() => diff.clampCustom({ enemyHp: 'lots' }), /enemyHp must be a finite number/);
  assert.throws(() => diff.clampCustom({ enemyHp: NaN }), /enemyHp must be a finite number/);
  assert.throws(() => diff.clampCustom({ playerDeathPenalty: 'gold99' }), /playerDeathPenalty must be one of/);
});

test('unknown ids and non-id inputs throw', () => {
  assert.throws(() => diff.resolve('impossible'), /unknown difficulty "impossible"/);
  assert.throws(() => diff.resolve('constructor'), /unknown difficulty "constructor"/);
  assert.throws(() => diff.resolve({ id: 'nope' }), /unknown difficulty "nope"/);
  assert.throws(() => diff.resolve(42), /difficulty must be an id or a config object/);
});

test('presets rise strictly from easy to nightmare on every data-defined dimension (telegraph falls)', () => {
  for (const field of RISING) {
    const values = PRESET_IDS.map((id) => presets[id][field]);
    for (let i = 1; i < values.length; i++) {
      assert.ok(values[i] > values[i - 1], `${field}: ${PRESET_IDS[i]} (${values[i]}) must exceed ${PRESET_IDS[i - 1]} (${values[i - 1]})`);
    }
  }
  for (const field of FALLING) {
    const values = PRESET_IDS.map((id) => presets[id][field]);
    for (let i = 1; i < values.length; i++) {
      assert.ok(values[i] < values[i - 1], `${field}: ${PRESET_IDS[i]} must be shorter than ${PRESET_IDS[i - 1]}`);
    }
  }
});

test('scaled monster stats rise Easy -> Normal -> Hard -> Nightmare on every multiplier, not HP alone', () => {
  const pick = (def) => PRESET_IDS.map((id) => diff.scaleMonster(def, 5, id, 2));
  const grunt = RPG.data.monsters['husk-grunt'];
  const rows = pick(grunt);
  for (const field of ['hp', 'damage', 'speed', 'xp', 'lootQuality', 'gold', 'eliteChance']) {
    for (let i = 1; i < rows.length; i++) {
      assert.ok(rows[i][field] > rows[i - 1][field], `${field}: ${PRESET_IDS[i]} must exceed ${PRESET_IDS[i - 1]}`);
    }
  }
  // Telegraph is 1 for non-bosses, so the boss-only fields are checked on the boss def below.
  const boss = RPG.data.monsters[Object.keys(RPG.data.monsters).find((id) => RPG.data.monsters[id].archetype === 'boss')];
  const bossRows = PRESET_IDS.map((id) => diff.scaleMonster(boss, 5, id, 2));
  for (let i = 1; i < bossRows.length; i++) {
    assert.ok(bossRows[i].hp > bossRows[i - 1].hp, 'boss hp rises with difficulty');
    assert.ok(bossRows[i].telegraph < bossRows[i - 1].telegraph, 'boss telegraph shrinks with difficulty');
  }
});

test('spawn budget rises with density across presets and never drops below 1', () => {
  const area = { baseBudget: 12 };
  const budgets = PRESET_IDS.map((id) => diff.spawnBudget(area, id));
  for (let i = 1; i < budgets.length; i++) {
    assert.ok(budgets[i] > budgets[i - 1], `budget ${PRESET_IDS[i]} (${budgets[i]}) must exceed ${PRESET_IDS[i - 1]}`);
  }
  assert.equal(diff.spawnBudget({ baseBudget: 1 }, 'easy'), 1);
  assert.equal(diff.spawnBudget(undefined, 'normal'), 10, 'areas without a baseBudget use the default');
});

test('player level scaling is strictly monotonic for hp and damage on every monster, and speed never falls', () => {
  for (const def of monsters) {
    for (const id of [...PRESET_IDS, 'custom']) {
      let prev = diff.scaleMonster(def, 1, id, 1);
      for (let level = 2; level <= 12; level++) {
        const cur = diff.scaleMonster(def, level, id, 1);
        assert.ok(cur.hp > prev.hp, `${def.id}/${id}: hp must grow at level ${level}`);
        assert.ok(cur.damage > prev.damage, `${def.id}/${id}: damage must grow at level ${level}`);
        assert.ok(cur.speed >= prev.speed, `${def.id}/${id}: speed must not fall at level ${level}`);
        prev = cur;
      }
    }
  }
});

test('area level raises xp and loot quality strictly, and hp and damage for ordinary monsters via perLevel', () => {
  const grunt = RPG.data.monsters['husk-grunt'];
  let prev = diff.scaleMonster(grunt, 3, 'normal', 1);
  for (let area = 2; area <= 5; area++) {
    const cur = diff.scaleMonster(grunt, 3, 'normal', area);
    assert.ok(cur.xp > prev.xp, `xp must grow at area ${area}`);
    assert.ok(cur.lootQuality > prev.lootQuality, `lootQuality must grow at area ${area}`);
    assert.ok(cur.hp > prev.hp, `hp must grow at area ${area} (perLevel.hp > 1)`);
    assert.ok(cur.damage > prev.damage, `damage must grow at area ${area} (perLevel.damage > 1)`);
    prev = cur;
  }
});

test('boss hp follows its data: flat per area (perLevel.hp = 1) but still rises with player level', () => {
  const boss = RPG.data.monsters['warden-of-ash'];
  assert.equal(diff.scaleMonster(boss, 5, 'normal', 1).hp, diff.scaleMonster(boss, 5, 'normal', 3).hp);
  assert.ok(diff.scaleMonster(boss, 6, 'normal', 1).hp > diff.scaleMonster(boss, 5, 'normal', 1).hp);
});

test('scaling and resolution are pure: same inputs give the same output and inputs are not mutated', () => {
  const grunt = RPG.data.monsters['husk-grunt'];
  const cfg = plain(diff.resolve('hard'));
  const before = JSON.stringify(cfg);
  const a = diff.scaleMonster(grunt, 4, cfg, 3);
  const b = diff.scaleMonster(grunt, 4, cfg, 3);
  assert.deepEqual(a, b);
  assert.equal(JSON.stringify(cfg), before, 'config passed in is not mutated');
  assert.deepEqual(plain(diff.resolve('hard')), plain(presets.hard), 'preset data is not mutated');
});

test('scaleMonster rejects invalid levels instead of guessing', () => {
  const grunt = RPG.data.monsters['husk-grunt'];
  assert.throws(() => diff.scaleMonster(grunt, 0, 'normal'), /level must be an integer >= 1/);
  assert.throws(() => diff.scaleMonster(grunt, 1.5, 'normal'), /level must be an integer >= 1/);
  assert.throws(() => diff.scaleMonster(grunt, 1, 'normal', 0), /areaLevel must be an integer >= 1/);
});

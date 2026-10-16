'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
// Uses the shared world loader directly: tests/data/load.js is owned by the items/difficulty work and changes under it.
const { loadRPG } = require('../world/load.js');

const RPG = loadRPG(['data/monsters.js']);
const MONSTERS = RPG.data.monsters;
const ALL = Object.values(MONSTERS);

const ARCHETYPES = ['melee', 'fast', 'ranged', 'tank', 'exploder', 'summoner', 'boss'];
const AI_IDS = ['melee', 'flanker', 'ranged', 'tank', 'exploder', 'summoner', 'boss'];
const TELEGRAPHS = ['circle', 'cone', 'line', 'ring'];
const ATTACK_KINDS = ['melee', 'projectile', 'aoe', 'nova', 'charge', 'summon'];
const DEATH_ANIMS = ['pop', 'shatter', 'dissolve', 'explode'];
// Boss telegraphs must give the player at least this many seconds to react (DATA-DEFINITIONS §5).
const BOSS_MIN_WINDUP = 0.8;

// Returns a list of schema problems for one definition; an empty list means it is valid.
function schemaErrors(def) {
  const errors = [];
  const need = (cond, msg) => { if (!cond) errors.push(msg); };
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

  need(typeof def.id === 'string' && def.id.length > 0, 'id');
  need(typeof def.name === 'string' && def.name.length > 0, 'name');
  need(ARCHETYPES.includes(def.archetype), 'archetype');
  need([1, 2, 3, 4].includes(def.tier), 'tier');
  need(Array.isArray(def.areas) && def.areas.length > 0, 'areas');
  need(typeof def.shape === 'string', 'shape');
  need(isNum(def.radius) && def.radius > 0, 'radius');
  need(typeof def.color === 'string', 'color');
  need(typeof def.outline === 'string', 'outline');
  need(typeof def.eyes === 'boolean', 'eyes');

  const s = def.baseStats || {};
  need(isNum(s.hp) && s.hp > 0, 'baseStats.hp');
  need(isNum(s.damage) && s.damage > 0, 'baseStats.damage');
  need(isNum(s.speed) && s.speed > 0, 'baseStats.speed');
  need(isNum(s.armor) && s.armor >= 0, 'baseStats.armor');
  need(isNum(def.perLevel && def.perLevel.hp) && isNum(def.perLevel && def.perLevel.damage), 'perLevel');

  need(AI_IDS.includes(def.ai), 'ai');
  const p = def.aiParams || {};
  need(isNum(p.aggroRange) && isNum(p.leashRange) && isNum(p.attackRange), 'aiParams ranges');
  need(isNum(p.attackCooldown) && isNum(p.windup), 'aiParams timing');

  need(Array.isArray(def.attacks) && def.attacks.length > 0, 'attacks');
  for (const a of def.attacks || []) {
    need(typeof a.id === 'string' && a.id.length > 0, `attack id (${a.id})`);
    need(ATTACK_KINDS.includes(a.kind), `attack kind (${a.id})`);
    need(isNum(a.damageMult) && a.damageMult >= 0, `attack damageMult (${a.id})`);
    need(isNum(a.windup) && a.windup >= 0, `attack windup (${a.id})`);
    need(isNum(a.weight) && a.weight > 0, `attack weight (${a.id})`);
    if (a.telegraph !== undefined) need(TELEGRAPHS.includes(a.telegraph), `attack telegraph (${a.id})`);
  }

  need(isNum(def.xp) && def.xp >= 0, 'xp');
  need(Array.isArray(def.gold) && def.gold.length === 2 && def.gold[0] <= def.gold[1], 'gold range');
  const loot = def.lootTable || {};
  need(typeof loot.id === 'string' && loot.id.length > 0, 'lootTable.id');
  need(isNum(loot.dropChance) && loot.dropChance >= 0 && loot.dropChance <= 1, 'lootTable.dropChance');
  need(Number.isInteger(loot.rolls) && loot.rolls >= 0, 'lootTable.rolls');
  need(isNum(loot.rarityBoost), 'lootTable.rarityBoost');

  need(typeof def.sfx === 'object' && def.sfx !== null && typeof def.sfx.hit === 'string', 'sfx');
  need(DEATH_ANIMS.includes(def.deathAnim), 'deathAnim');
  need(isNum(def.spawnWeight) && def.spawnWeight >= 0, 'spawnWeight');
  need(Array.isArray(def.groupSize) && def.groupSize.length === 2 && def.groupSize[0] <= def.groupSize[1], 'groupSize');
  need(typeof def.eliteAllowed === 'boolean', 'eliteAllowed');
  return errors;
}

test('every monster definition passes the schema check', () => {
  assert.ok(ALL.length >= 7, 'expected the seven archetypes plus the boss');
  for (const def of ALL) {
    assert.deepEqual(schemaErrors(def), [], `${def.id} has schema errors`);
  }
});

test('definitions are registered under their own id and frozen', () => {
  for (const [key, def] of Object.entries(MONSTERS)) {
    assert.equal(def.id, key);
    assert.equal(Object.isFrozen(def), true, `${key} must be frozen`);
    assert.equal(Object.isFrozen(def.baseStats), true, `${key}.baseStats must be frozen`);
  }
});

test('all seven archetypes are present exactly once', () => {
  const archetypes = ALL.map((d) => d.archetype).sort();
  assert.deepEqual(archetypes, [...ARCHETYPES].sort());
});

test('each archetype has a distinct AI behaviour id', () => {
  const ais = ALL.map((d) => d.ai);
  assert.equal(new Set(ais).size, ais.length, `duplicate ai ids: ${ais}`);
  assert.deepEqual([...ais].sort(), [...AI_IDS].sort());
});

test('monsters are not recolours: each has its own silhouette', () => {
  const shapes = ALL.map((d) => d.shape);
  assert.equal(new Set(shapes).size, shapes.length, `duplicate shapes: ${shapes}`);
});

test('boss has at least two phases with descending HP thresholds', () => {
  const boss = MONSTERS['warden-of-ash'];
  assert.equal(boss.archetype, 'boss');
  assert.ok(boss.phases.length >= 2, 'boss needs 2+ phases');
  assert.equal(boss.phases[0].hpBelow, 1, 'first phase must start at full HP');
  for (let i = 1; i < boss.phases.length; i++) {
    assert.ok(boss.phases[i].hpBelow < boss.phases[i - 1].hpBelow, `phase ${i} threshold must be lower`);
  }
  assert.ok(boss.baseStats.hp >= 1000, 'boss should have a large HP pool');
});

test('boss phases only reference defined attacks, and minPhase is a valid phase index', () => {
  const boss = MONSTERS['warden-of-ash'];
  const attackIds = new Set(boss.attacks.map((a) => a.id));
  boss.phases.forEach((phase, index) => {
    for (const id of phase.attacks) {
      assert.ok(attackIds.has(id), `phase ${index} references unknown attack ${id}`);
    }
  });
  for (const attack of boss.attacks) {
    if (attack.minPhase === undefined) continue;
    assert.ok(Number.isInteger(attack.minPhase), `${attack.id} minPhase must be an integer`);
    assert.ok(attack.minPhase >= 0 && attack.minPhase < boss.phases.length, `${attack.id} minPhase out of range`);
  }
});

test('every boss attack is telegraphed with at least the minimum warning time', () => {
  const boss = MONSTERS['warden-of-ash'];
  assert.ok(boss.attacks.length >= 2, 'boss needs several attack patterns');
  for (const attack of boss.attacks) {
    assert.ok(TELEGRAPHS.includes(attack.telegraph), `${attack.id} has no telegraph`);
    assert.ok(attack.windup >= BOSS_MIN_WINDUP, `${attack.id} windup ${attack.windup}s is below ${BOSS_MIN_WINDUP}s`);
  }
});

test('boss has a special mechanic and better rewards than ordinary monsters', () => {
  const boss = MONSTERS['warden-of-ash'];
  assert.equal(typeof boss.mechanic.id, 'string');
  assert.ok(boss.mechanic.desc.length > 0, 'mechanic needs a description');
  assert.equal(boss.rewards.guaranteedRarity, 'epic');
  for (const def of ALL.filter((d) => d.archetype !== 'boss')) {
    assert.ok(boss.xp > def.xp, `boss xp should exceed ${def.id}`);
    assert.ok(boss.lootTable.rarityBoost > def.lootTable.rarityBoost, `boss loot should beat ${def.id}`);
  }
});

test('summon references resolve to a defined monster', () => {
  for (const def of ALL) {
    const summons = [
      ...(def.attacks || []).filter((a) => a.kind === 'summon').map((a) => a.summonDefId),
      def.aiParams.summonDefId,
    ].filter((id) => id !== undefined);
    for (const id of summons) {
      assert.ok(MONSTERS[id], `${def.id} summons unknown monster ${id}`);
    }
  }
});

test('the schema check rejects a broken definition', () => {
  const broken = JSON.parse(JSON.stringify(MONSTERS['husk-grunt']));
  broken.ai = 'teleport';
  broken.attacks[0].telegraph = 'spiral';
  broken.baseStats.hp = 0;
  const errors = schemaErrors(broken);
  assert.ok(errors.includes('ai'));
  assert.ok(errors.some((e) => e.startsWith('attack telegraph')));
  assert.ok(errors.includes('baseStats.hp'));
});

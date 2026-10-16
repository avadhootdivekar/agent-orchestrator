'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

// Namespace and util come first: the data module reads RPG.core.util at load time.
const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'data/weapons.js']);
const weapons = Object.values(RPG.data.weapons);

const CATEGORIES = ['melee', 'ranged', 'area'];
const BEHAVIORS = ['swing', 'thrust', 'smash', 'projectile', 'spread', 'beam', 'arc', 'aoeGround', 'explosive'];
const DAMAGE_TYPES = ['phys', 'fire', 'cold', 'lightning', 'poison', 'arcane'];
const STATUS_IDS = ['slow', 'stun', 'freeze', 'root', 'burn', 'poison', 'shield', 'haste', 'armorUp', 'reflect', 'invuln'];
const NUMBER_FIELDS = [
  'attackInterval', 'windup', 'active', 'recover', 'range', 'arc', 'aoeRadius', 'knockback',
  'critChanceBonus', 'critMultBonus', 'resourceCost', 'moveSlowWhileAttacking',
];
const PROJECTILE_FIELDS = ['speed', 'radius', 'lifetime', 'pierce', 'bounce', 'homing'];

const isNonNeg = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const isInt = (v) => Number.isInteger(v);

// Returns a list of schema problems for one WeaponDefinition (empty when valid). Mirrors DATA-DEFINITIONS.md §3.
function schemaProblems(def) {
  const problems = [];
  const need = (ok, msg) => { if (!ok) problems.push(msg); };

  need(typeof def.id === 'string' && def.id.length > 0, 'id must be a non-empty string');
  need(typeof def.name === 'string' && def.name.length > 0, 'name must be a non-empty string');
  need(CATEGORIES.includes(def.category), `category "${def.category}" not in ${CATEGORIES}`);
  need(BEHAVIORS.includes(def.behavior), `behavior "${def.behavior}" not in ${BEHAVIORS}`);
  need(Array.isArray(def.baseDamage) && def.baseDamage.length === 2 &&
    def.baseDamage.every(isNonNeg) && def.baseDamage[0] <= def.baseDamage[1], 'baseDamage must be [min, max] with 0 <= min <= max');
  need(DAMAGE_TYPES.includes(def.damageType), `damageType "${def.damageType}" not in ${DAMAGE_TYPES}`);
  for (const field of NUMBER_FIELDS) need(isNonNeg(def[field]), `${field} must be a non-negative number`);
  need(def.attackInterval > 0, 'attackInterval must be > 0');
  need(def.range > 0, 'range must be > 0');
  need(def.moveSlowWhileAttacking <= 1, 'moveSlowWhileAttacking must be within 0..1');
  need(def.arc <= Math.PI * 2, 'arc must be within 0..2π');
  need(def.projectile === null || (typeof def.projectile === 'object' &&
    PROJECTILE_FIELDS.every((f) => isNonNeg(def.projectile[f])) && def.projectile.speed > 0 &&
    isInt(def.projectile.pierce) && typeof def.projectile.shape === 'string' && typeof def.projectile.trail === 'boolean'),
  'projectile must be null or a ProjectileSpec');
  need(isInt(def.projectileCount) && def.projectileCount >= 1, 'projectileCount must be an int >= 1');
  need(typeof def.critChanceBonus === 'number' && def.critChanceBonus <= 1, 'critChanceBonus must be <= 1');
  need(Array.isArray(def.status) && def.status.every((s) => STATUS_IDS.includes(s.id) &&
    isNonNeg(s.duration) && (s.chance === undefined || (s.chance >= 0 && s.chance <= 1))), 'status must be StatusSpec[]');
  need(Array.isArray(def.tags) && def.tags.every((t) => typeof t === 'string'), 'tags must be string[]');
  need(def.sfx && typeof def.sfx.attack === 'string' && typeof def.sfx.hit === 'string', 'sfx must name attack and hit');
  need(typeof def.color === 'string' && def.color.length > 0, 'color must be a palette token');
  return problems;
}

test('registry has at least 3 weapons, keyed by id', () => {
  assert.ok(weapons.length >= 3, `expected >= 3 weapons, got ${weapons.length}`);
  for (const [key, def] of Object.entries(RPG.data.weapons)) {
    assert.equal(def.id, key, `registry key "${key}" must equal def.id`);
  }
});

test('every weapon passes the schema validator', () => {
  for (const def of weapons) {
    assert.deepEqual(schemaProblems(def), [], `${def.id} has schema problems`);
  }
});

test('weapon ids are unique and names are unique', () => {
  assert.equal(new Set(weapons.map((w) => w.id)).size, weapons.length, 'duplicate id');
  assert.equal(new Set(weapons.map((w) => w.name)).size, weapons.length, 'duplicate name');
});

test('weapons cover melee, ranged and area categories exactly once each', () => {
  const categories = weapons.map((w) => w.category).sort();
  assert.deepEqual(categories, [...CATEGORIES].sort());
});

test('categories use distinct behaviour kinds, not renamed copies of one behaviour', () => {
  const behaviors = new Set(weapons.map((w) => w.behavior));
  assert.equal(behaviors.size, weapons.length, 'two weapons share a behaviour kind');
});

test('melee is a close cone swing with no projectile and knockback', () => {
  const melee = weapons.find((w) => w.category === 'melee');
  assert.equal(melee.projectile, null);
  assert.ok(melee.arc > 0, 'melee needs a swing cone');
  assert.ok(melee.knockback > 0, 'melee should knock back');
  assert.equal(melee.aoeRadius, 0);
});

test('ranged fires a travelling projectile with pierce and no area', () => {
  const ranged = weapons.find((w) => w.category === 'ranged');
  assert.ok(ranged.projectile !== null, 'ranged needs a projectile');
  assert.ok(ranged.projectile.speed > 0 && ranged.projectile.pierce >= 1, 'ranged projectile must move and pierce');
  assert.equal(ranged.aoeRadius, 0);
  assert.equal(ranged.arc, 0);
});

test('area is a ground AoE with a slow status and no projectile', () => {
  const area = weapons.find((w) => w.category === 'area');
  assert.equal(area.projectile, null);
  assert.ok(area.aoeRadius > 0, 'area needs a radius');
  assert.equal(area.behavior, 'aoeGround');
  assert.ok(area.status.some((s) => s.id === 'slow' && s.strength > 0), 'area should apply slow');
});

test('weapons differ behaviourally: range, area, energy cost and status, not only damage', () => {
  const melee = weapons.find((w) => w.category === 'melee');
  const ranged = weapons.find((w) => w.category === 'ranged');
  const area = weapons.find((w) => w.category === 'area');

  assert.ok(new Set([melee.range, ranged.range, area.range]).size === 3, 'ranges must differ');
  assert.ok(new Set([melee.resourceCost, ranged.resourceCost, area.resourceCost]).size === 3, 'energy costs must differ');
  assert.ok(area.aoeRadius > 0 && melee.aoeRadius === 0 && ranged.aoeRadius === 0, 'only area has impact radius');
  assert.ok(area.status.length > 0 && melee.status.length === 0 && ranged.status.length === 0, 'only area applies status');
  assert.ok(ranged.critChanceBonus > melee.critChanceBonus, 'ranged should carry the crit bonus');
});

test('weapon definitions are frozen (data, not mutable runtime state)', () => {
  for (const def of weapons) {
    assert.ok(Object.isFrozen(def), `${def.id} must be frozen`);
    assert.ok(Object.isFrozen(def.baseDamage), `${def.id}.baseDamage must be frozen`);
  }
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'data/skills.js']);
const SKILLS = RPG.data.skills;

// Allowed values from DATA-DEFINITIONS.md §4 and §1 (StatMap keys used by passiveMods).
const CATEGORIES = ['offensive', 'defensive', 'regenerative', 'mobility', 'control'];
const TYPES = ['active', 'passive'];
const EFFECTS = ['projectile', 'aoe', 'nova', 'buff', 'heal', 'dash', 'teleport', 'chain', 'summonZone', 'whirl'];
const TARGETINGS = ['self', 'aim', 'ground', 'nearest', 'cone'];
const MOD_KINDS = ['flat', 'inc', 'more'];
const STAT_KEYS = [
  'maxHp', 'maxResource', 'damage', 'attackSpeed', 'moveSpeed', 'armor', 'critChance', 'critDamage',
  'lifeRegen', 'resourceRegen', 'cooldownReduction', 'lifeSteal', 'skillDamage', 'resFire', 'resCold',
  'resLightning', 'resPoison', 'resArcane', 'thorns', 'pickupRadius', 'goldFind', 'xpGain',
];

const isNonNegNumber = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const isPosInt = (v) => Number.isInteger(v) && v >= 1;
const hasKeys = (o) => o !== null && typeof o === 'object' && Object.keys(o).length > 0;

/**
 * Returns a list of human-readable problems with a skills map (empty when valid). Checks schema, id
 * uniqueness, prerequisite resolution and acyclicity, per-rank upgrade data and category coverage.
 */
function validateSkills(skills) {
  const errors = [];
  const ids = Object.keys(skills);

  for (const key of ids) {
    const s = skills[key];
    const where = `skill '${key}'`;
    if (s.id !== key) errors.push(`${where}: id field '${s.id}' does not match its key`);
    if (typeof s.name !== 'string' || s.name === '') errors.push(`${where}: missing name`);
    if (!CATEGORIES.includes(s.category)) errors.push(`${where}: unknown category '${s.category}'`);
    if (!TYPES.includes(s.type)) errors.push(`${where}: unknown type '${s.type}'`);
    if (!isPosInt(s.maxRank)) errors.push(`${where}: maxRank must be a positive integer`);
    if (!isPosInt(s.tier)) errors.push(`${where}: tier must be a positive integer`);
    if (!s.icon || typeof s.icon.glyph !== 'string' || typeof s.icon.color !== 'string') {
      errors.push(`${where}: icon needs glyph and color`);
    }
    if (!s.requires || !isNonNegNumber(s.requires.level)) errors.push(`${where}: requires.level missing`);

    if (s.type === 'active') {
      if (!EFFECTS.includes(s.effect)) errors.push(`${where}: unknown effect '${s.effect}'`);
      if (!TARGETINGS.includes(s.targeting)) errors.push(`${where}: unknown targeting '${s.targeting}'`);
      for (const f of ['cost', 'cooldown', 'castTime']) {
        if (!isNonNegNumber(s[f])) errors.push(`${where}: ${f} must be a non-negative number`);
      }
      if (!hasKeys(s.params)) errors.push(`${where}: active skill needs params`);
      if (!hasKeys(s.rankScaling)) errors.push(`${where}: active skill needs rankScaling`);
      for (const p of Object.keys(s.rankScaling || {})) {
        if (!(s.params && p in s.params)) errors.push(`${where}: rankScaling '${p}' is not a param`);
      }
    } else if (s.type === 'passive') {
      if (s.effect !== null || s.targeting !== null) errors.push(`${where}: passive must have null effect/targeting`);
      if (!Array.isArray(s.passiveMods) || s.passiveMods.length === 0) {
        errors.push(`${where}: passive needs passiveMods`);
      }
      const mods = s.passiveMods || [];
      for (const m of mods) {
        if (!STAT_KEYS.includes(m.stat)) errors.push(`${where}: passiveMod stat '${m.stat}' unknown`);
        if (!MOD_KINDS.includes(m.kind)) errors.push(`${where}: passiveMod kind '${m.kind}' unknown`);
        if (typeof m.value !== 'number') errors.push(`${where}: passiveMod '${m.stat}' value not a number`);
      }
      if (!hasKeys(s.rankScaling)) errors.push(`${where}: passive skill needs rankScaling`);
      for (const stat of Object.keys(s.rankScaling || {})) {
        if (!mods.some((m) => m.stat === stat)) errors.push(`${where}: rankScaling '${stat}' is not a passiveMod`);
      }
    }

    const upgradeIds = new Set();
    const upgrades = s.upgrades || [];
    if (upgrades.length < 1 || upgrades.length > 3) errors.push(`${where}: needs 1-3 upgrades`);
    for (const u of upgrades) {
      const uw = `${where} upgrade '${u.id}'`;
      if (upgradeIds.has(u.id)) errors.push(`${uw}: duplicate upgrade id`);
      upgradeIds.add(u.id);
      if (!isPosInt(u.maxRank)) errors.push(`${uw}: maxRank must be a positive integer`);
      if (!isPosInt(u.cost)) errors.push(`${uw}: cost must be a positive integer`);
      if (!isPosInt(u.requiresRank) || u.requiresRank > s.maxRank) {
        errors.push(`${uw}: requiresRank must be within 1..${s.maxRank}`);
      }
      const effects = [hasKeys(u.paramDeltas), Array.isArray(u.passiveMods) && u.passiveMods.length > 0, !!u.addStatus];
      if (!effects.some(Boolean)) errors.push(`${uw}: has no paramDeltas, passiveMods or addStatus`);
      if (s.type === 'active' && hasKeys(u.paramDeltas)) {
        for (const p of Object.keys(u.paramDeltas)) {
          if (!(s.params && p in s.params)) errors.push(`${uw}: paramDelta '${p}' is not a param`);
        }
      }
    }
  }

  // Prerequisites: each referenced skill exists, the required rank is reachable, and no skill requires itself.
  const requiresOf = {};
  for (const key of ids) {
    const s = skills[key];
    requiresOf[key] = [];
    for (const [prereq, rank] of Object.entries((s.requires && s.requires.skills) || {})) {
      const where = `skill '${key}' requires '${prereq}'`;
      if (!(prereq in skills)) {
        errors.push(`${where}: prerequisite does not resolve`);
        continue;
      }
      if (prereq === key) errors.push(`${where}: skill requires itself`);
      if (!isPosInt(rank) || rank > skills[prereq].maxRank) errors.push(`${where}: rank ${rank} out of range`);
      requiresOf[key].push(prereq);
    }
  }

  // Acyclic: depth-first search with an on-stack set; a back edge is a cycle.
  const state = {};
  const visit = (key, trail) => {
    if (state[key] === 'done') return;
    if (state[key] === 'active') {
      errors.push(`prerequisite cycle: ${[...trail, key].join(' -> ')}`);
      return;
    }
    state[key] = 'active';
    for (const next of requiresOf[key] || []) visit(next, [...trail, key]);
    state[key] = 'done';
  };
  for (const key of ids) visit(key, []);

  for (const category of CATEGORIES) {
    if (!ids.some((k) => skills[k].category === category)) errors.push(`no skill in category '${category}'`);
  }
  if (!ids.some((k) => skills[k].type === 'active')) errors.push('no active skill');
  if (!ids.some((k) => skills[k].type === 'passive')) errors.push('no passive skill');

  return errors;
}

test('registers the skill catalogue on RPG.data.skills, frozen', () => {
  assert.ok(SKILLS && typeof SKILLS === 'object');
  assert.equal(Object.isFrozen(SKILLS), true);
  assert.equal(Object.isFrozen(SKILLS.fireball), true);
  assert.equal(Object.isFrozen(SKILLS.fireball.upgrades[0]), true);
});

test('has at least 6 skills, each id unique and matching its key', () => {
  const ids = Object.keys(SKILLS);
  assert.ok(ids.length >= 6, `expected >= 6 skills, got ${ids.length}`);
  assert.equal(new Set(ids).size, ids.length);
  for (const key of ids) assert.equal(SKILLS[key].id, key);
});

test('covers all five categories with both active and passive skills', () => {
  const all = Object.values(SKILLS);
  for (const category of CATEGORIES) {
    assert.ok(all.some((s) => s.category === category), `no skill in ${category}`);
  }
  assert.ok(all.some((s) => s.type === 'active'));
  assert.ok(all.some((s) => s.type === 'passive'));
});

test('catalogue passes the full schema, prerequisite and acyclicity validator', () => {
  assert.deepEqual(validateSkills(SKILLS), []);
});

test('every skill has per-rank upgrade data (rankScaling and 1-3 upgrades)', () => {
  for (const s of Object.values(SKILLS)) {
    assert.ok(Object.keys(s.rankScaling).length > 0, `${s.id} has no rankScaling`);
    assert.ok(s.upgrades.length >= 1 && s.upgrades.length <= 3, `${s.id} upgrade count`);
    for (const u of s.upgrades) {
      assert.ok(u.maxRank >= 1 && u.cost >= 1 && u.requiresRank >= 1, `${s.id}/${u.id} rank data`);
    }
  }
});

test('prerequisite tree has a real dependency chain that resolves', () => {
  // Frost Nova needs Fireball, Ground Slam needs Whirlwind, Focus needs Mend which needs Shield.
  // Objects built inside the vm context have another realm's prototypes, so compare as plain JSON.
  const plain = (v) => JSON.parse(JSON.stringify(v));
  assert.deepEqual(plain(SKILLS['frost-nova'].requires.skills), { fireball: 1 });
  assert.deepEqual(plain(SKILLS['ground-slam'].requires.skills), { whirlwind: 1 });
  assert.deepEqual(plain(SKILLS.focus.requires.skills), { mend: 1 });
  assert.deepEqual(plain(SKILLS.mend.requires.skills), { shield: 1 });
});

test('validator flags a prerequisite cycle', () => {
  const cyclic = {
    a: { ...SKILLS.fireball, id: 'a', requires: { level: 1, skills: { b: 1 } } },
    b: { ...SKILLS.fireball, id: 'b', requires: { level: 1, skills: { a: 1 } } },
  };
  const errors = validateSkills(cyclic);
  assert.ok(errors.some((e) => e.startsWith('prerequisite cycle')), errors.join('\n'));
});

test('validator flags a prerequisite that does not resolve', () => {
  const dangling = {
    a: { ...SKILLS.fireball, id: 'a', requires: { level: 1, skills: { ghost: 1 } } },
  };
  const errors = validateSkills(dangling);
  assert.ok(errors.some((e) => e.includes("'ghost'") && e.includes('does not resolve')), errors.join('\n'));
});

test('validator flags an upgrade whose requiresRank exceeds the skill maxRank', () => {
  const bad = {
    fireball: {
      ...SKILLS.fireball,
      upgrades: [{ ...SKILLS.fireball.upgrades[0], requiresRank: 9 }],
    },
  };
  const errors = validateSkills(bad);
  assert.ok(errors.some((e) => e.includes('requiresRank')), errors.join('\n'));
});

test('validator flags a passive modifier with an unknown stat', () => {
  const bad = {
    vitality: {
      ...SKILLS.vitality,
      passiveMods: [{ stat: 'notAStat', kind: 'flat', value: 1 }],
      rankScaling: { notAStat: 1 },
    },
  };
  const errors = validateSkills(bad);
  assert.ok(errors.some((e) => e.includes("'notAStat'")), errors.join('\n'));
});

test('validator flags a catalogue that leaves a category empty', () => {
  const onlyOffense = { fireball: SKILLS.fireball };
  const errors = validateSkills(onlyOffense);
  assert.ok(errors.some((e) => e.includes("'defensive'")), errors.join('\n'));
});

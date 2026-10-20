'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'data/skills.js', 'world/stats.js', 'world/skills.js']);
const sk = RPG.world.skills;
const plain = (v) => JSON.parse(JSON.stringify(v));

function makePlayer(over) {
  const p = { level: 10, resource: 100, gold: 1000, stats: new RPG.world.StatBlock(), cooldowns: {} };
  p.skills = sk.createSkillState(over && over.points !== undefined ? over.points : 20);
  return Object.assign(p, over && over.level ? { level: over.level } : {});
}

test('learn spends one point per rank and respects the rank cap', () => {
  const p = makePlayer({ points: 10 });
  for (let r = 1; r <= 5; r++) assert.equal(sk.learnSkill(null, p, 'fireball'), true);
  assert.equal(sk.getSkillRank(p, 'fireball'), 5);
  assert.equal(p.skills.points, 5);
  assert.equal(p.skills.spentPoints, 5);
  assert.deepEqual(plain(sk.canLearn(p, 'fireball')), { ok: false, reason: 'max-rank' });
  assert.equal(sk.learnSkill(null, p, 'fireball'), false);
  assert.equal(p.skills.points, 5);
});

test('unlock failure paths: unknown, level, prerequisite, no points', () => {
  const p = makePlayer({ points: 5, level: 1 });
  assert.equal(sk.canLearn(p, 'nope').reason, 'unknown-skill');
  assert.equal(sk.canLearn(p, 'ground-slam').reason, 'level'); // needs level 3
  p.level = 3;
  assert.equal(sk.canLearn(p, 'ground-slam').reason, 'prerequisite'); // needs whirlwind 1
  assert.equal(sk.learnSkill(null, p, 'ground-slam'), false);
  assert.equal(sk.learnSkill(null, p, 'whirlwind'), true);
  assert.equal(sk.canLearn(p, 'ground-slam').ok, true);
  p.skills.points = 0;
  assert.equal(sk.canLearn(p, 'ground-slam').reason, 'no-points');
  assert.equal(sk.learnSkill(null, p, 'ground-slam'), false);
  assert.equal(sk.getSkillRank(p, 'ground-slam'), 0);
});

test('upgrades need the skill learned, parent rank, points, and respect their own cap', () => {
  const p = makePlayer({ points: 20 });
  assert.equal(sk.canUpgrade(p, 'fireball', 'bigger-explosion').reason, 'not-learned');
  assert.equal(sk.canUpgrade(p, 'fireball', 'bogus').reason, 'unknown-upgrade');
  sk.learnSkill(null, p, 'fireball');
  assert.equal(sk.canUpgrade(p, 'fireball', 'multiple-projectiles').reason, 'parent-rank'); // needs rank 3
  assert.equal(sk.upgradeSkill(null, p, 'fireball', 'bigger-explosion'), true);
  sk.upgradeSkill(null, p, 'fireball', 'bigger-explosion');
  sk.upgradeSkill(null, p, 'fireball', 'bigger-explosion');
  assert.equal(sk.canUpgrade(p, 'fireball', 'bigger-explosion').reason, 'max-rank');
  assert.equal(p.skills.spentPoints, 1 + 3);
  sk.learnSkill(null, p, 'fireball'); sk.learnSkill(null, p, 'fireball');
  const before = p.skills.points;
  assert.equal(sk.upgradeSkill(null, p, 'fireball', 'multiple-projectiles'), true); // cost 2
  assert.equal(p.skills.points, before - 2);
  p.skills.points = 1;
  assert.equal(sk.canUpgrade(p, 'fireball', 'multiple-projectiles').reason, 'no-points');
});

test('passives apply through StatBlock source skill:<id> and scale with rank and upgrades', () => {
  const p = makePlayer({ points: 20 });
  const base = p.stats.get('maxHp');
  sk.learnSkill(null, p, 'vitality');
  assert.equal(p.stats.get('maxHp'), base + 10);
  assert.ok(p.stats.mods.every((m) => m.source === 'skill:vitality'));
  sk.learnSkill(null, p, 'vitality'); // rank 2: +10 + 10
  assert.equal(p.stats.get('maxHp'), base + 20);
  sk.upgradeSkill(null, p, 'vitality', require_first_upgrade('vitality'));
  assert.ok(p.stats.get('maxHp') > base + 20);
  sk.applyPassives(p); // idempotent
  sk.applyPassives(p);
  assert.equal(p.stats.mods.filter((m) => m.stat === 'maxHp').length, 1);
});

function require_first_upgrade(id) { return RPG.data.skills[id].upgrades[0].id; }

test('respec refunds every point and removes passive modifiers and bar entries', () => {
  const p = makePlayer({ points: 12 });
  const hp = p.stats.get('maxHp');
  sk.learnSkill(null, p, 'vitality'); sk.learnSkill(null, p, 'vitality');
  sk.learnSkill(null, p, 'fireball');
  sk.upgradeSkill(null, p, 'fireball', 'bigger-explosion');
  sk.upgradeSkill(null, p, 'vitality', require_first_upgrade('vitality'));
  assert.equal(sk.equipSkill(p, 'fireball', 2), true);
  const spent = p.skills.spentPoints;
  assert.ok(spent > 0 && p.skills.points === 12 - spent);
  const r = sk.respec(null, p, 0);
  assert.deepEqual(plain(r), { ok: true, refunded: spent });
  assert.equal(p.skills.points, 12);
  assert.equal(p.skills.spentPoints, 0);
  assert.deepEqual(plain(p.skills.learned), {});
  assert.ok(p.skills.bar.every((s) => s === null));
  assert.equal(p.stats.mods.length, 0);
  assert.equal(p.stats.get('maxHp'), hp);
});

test('respec charges gold and refuses when the player cannot pay', () => {
  const p = makePlayer({ points: 3 });
  sk.learnSkill(null, p, 'fireball');
  p.gold = 10;
  const cost = sk.respecCost(p);
  assert.equal(cost, 500);
  assert.equal(sk.respec(null, p, cost).ok, false);
  assert.equal(sk.getSkillRank(p, 'fireball'), 1);
  p.gold = 600;
  assert.equal(sk.respec(null, p, cost).ok, true);
  assert.equal(p.gold, 100);
});

test('equipSkill accepts only learned active skills and keeps one slot per skill', () => {
  const p = makePlayer({ points: 5 });
  assert.equal(sk.equipSkill(p, 'fireball', 0), false); // not learned
  sk.learnSkill(null, p, 'fireball'); sk.learnSkill(null, p, 'vitality');
  assert.equal(sk.equipSkill(p, 'vitality', 0), false); // passive
  assert.equal(sk.equipSkill(p, 'fireball', 9), false); // out of range
  assert.equal(sk.equipSkill(p, 'fireball', 0), true);
  assert.equal(sk.equipSkill(p, 'fireball', 3), true);
  assert.deepEqual(plain(p.skills.bar.map((s) => s)), [null, null, null, 'fireball', null, null, null]);
});

test('casting is gated by slot, resource and cooldown, and cooldown reduction applies', () => {
  const p = makePlayer({ points: 5 });
  sk.learnSkill(null, p, 'fireball');
  assert.equal(sk.tryCastSkill(null, p, 0).reason, 'empty-slot');
  sk.equipSkill(p, 'fireball', 0);
  p.resource = 5; // fireball costs 12
  assert.equal(sk.tryCastSkill(null, p, 0).reason, 'resource');
  assert.equal(p.resource, 5);
  p.resource = 30;
  const cast = sk.tryCastSkill(null, p, 0);
  assert.equal(cast.ok, true);
  assert.equal(cast.skillId, 'fireball');
  assert.equal(p.resource, 18);
  assert.equal(p.cooldowns['skill:fireball'], 3);
  assert.equal(sk.tryCastSkill(null, p, 0).reason, 'cooldown');
  assert.equal(p.resource, 18); // failed cast costs nothing
  sk.updateSkills(null, p, 2);
  assert.equal(sk.tryCastSkill(null, p, 0).reason, 'cooldown');
  sk.updateSkills(null, p, 1);
  assert.equal(sk.cooldownRemaining(p, 'fireball'), 0);
  p.stats.addModifier({ source: 'buff:x', stat: 'cooldownReduction', kind: 'flat', value: 0.2 });
  assert.equal(sk.tryCastSkill(null, p, 0).ok, true);
  assert.ok(Math.abs(p.cooldowns['skill:fireball'] - 2.4) < 1e-9);
});

test('resolveSkillParams applies rank scaling and upgrade deltas', () => {
  const p = makePlayer({ points: 20 });
  for (let i = 0; i < 3; i++) sk.learnSkill(null, p, 'fireball');
  sk.upgradeSkill(null, p, 'fireball', 'bigger-explosion');
  const params = sk.resolveSkillParams(p, RPG.data.skills.fireball);
  assert.ok(Math.abs(params.damageMult - (1.4 + 0.15 * 2)) < 1e-9);
  assert.equal(params.aoeRadius, 60 + 6 * 2 + 12);
});

test('serialize/restore round-trips and re-applies passives', () => {
  const p = makePlayer({ points: 10 });
  sk.learnSkill(null, p, 'vitality'); sk.learnSkill(null, p, 'fireball');
  sk.upgradeSkill(null, p, 'fireball', 'bigger-explosion');
  sk.equipSkill(p, 'fireball', 1);
  const snap = JSON.parse(JSON.stringify(sk.serialize(p)));
  const q = makePlayer({ points: 0 });
  sk.restore(q, snap);
  assert.deepEqual(plain(sk.serialize(q)), snap);
  assert.equal(q.stats.get('maxHp'), p.stats.get('maxHp'));
});

test('restore drops unknown content and refunds it as points', () => {
  const q = makePlayer({ points: 0 });
  sk.restore(q, { learned: { fireball: 2, ghost: 3 }, upgrades: { ghost: { x: 1 } }, bar: ['ghost', 'fireball', 'vitality'], points: 1, spentPoints: 5 });
  assert.equal(sk.getSkillRank(q, 'ghost'), 0);
  assert.equal(q.skills.spentPoints, 2);
  assert.equal(q.skills.points, 1 + 3);
  assert.deepEqual(plain(q.skills.bar), [null, 'fireball', null, null, null, null, null]);
});

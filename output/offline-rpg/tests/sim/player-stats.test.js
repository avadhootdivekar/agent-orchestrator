'use strict';
// Player stat model: sim/player.js keeps `statBlock` (modifiers) plus `stats` (a plain snapshot). combat.js, skills.js and
// the combat stage must work with either shape, and stage 3-4 hits (melee, immediate skill hits) must emit HIT.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine, DT } = require('../integration/helpers.js');

let RPG;
test.before(async () => { RPG = await loadEngine(); });

function world() {
  const w = RPG.sim.createWorld({ seed: 5 });
  const p = w.player;
  p.resource = p.stats.maxResource = 500;
  return w;
}

function equipGear(p) {
  const eq = { slots: p.equipment };
  const item = RPG.world.loot.generateItem(RPG.core.Rng.create(3), { baseId: 'iron-helm', rarity: 'common', ilvl: 1 });
  RPG.world.equipment.equip(eq, p.statBlock, item);
  RPG.sim.player.refreshStats(p);
  return item;
}

// Learns each id in order (ground-slam needs whirlwind first).
function learn(w, ...ids) {
  const p = w.player;
  p.level = 20;
  // A real level-up writes one 'level' modifier per stat (sim/rewards.js); a restore reapplies them.
  const growth = RPG.world.progression.LEVEL_STAT_GROWTH;
  p.statBlock.removeModifiersBySource('level');
  for (const stat of Object.keys(growth)) p.statBlock.addModifier({ source: 'level', stat, kind: 'flat', value: growth[stat] * (p.level - 1) });
  RPG.sim.player.refreshStats(p);
  p.skills.points = 20;
  for (const id of ids) assert.equal(RPG.world.skills.learnSkill(w, p, id), true, 'learn ' + id);
}

function castIntent(slot, aim) {
  const skills = [];
  skills[slot] = true;
  return { move: { x: 0, y: 0 }, aim, attack: false, skills, dash: false, toggles: {} };
}

function hitsFor(w, id) {
  const out = [];
  const off = RPG.core.events.on(RPG.core.events.NAMES.HIT, (h) => { if (h.targetId === id) out.push(h); });
  return { out, off };
}

test('combat.js reads a StatBlock and a plain snapshot identically', () => {
  const sb = new RPG.world.StatBlock();
  sb.addModifier({ source: 't', stat: 'armor', kind: 'flat', value: 30 });
  const ent = { stats: sb }, snap = { stats: sb.snapshot() };
  for (const k of ['armor', 'maxHp', 'critChance', 'resFire']) assert.equal(RPG.world.combat.stat(ent, k), RPG.world.combat.stat(snap, k), k);
  assert.equal(RPG.world.combat.stat({}, 'armor'), 0);
});

test('casting a real skill via sim.step on a createWorld player does not throw and applies its effect', () => {
  const w = world(), p = w.player;
  learn(w, 'whirlwind', 'ground-slam');
  RPG.world.skills.equipSkill(p, 'ground-slam', 0);
  const m = RPG.world.spawnMonster(w, 'husk-grunt', p.x + 40, p.y);
  const before = m.hp, res = p.resource;
  // A slam can kill the grunt, and a dead monster is swept back into the pool (its fields cleared), so damage is read from the HIT event.
  const { out: hits, off } = hitsFor(w, m.id);
  RPG.sim.step(w, castIntent(0, { x: m.x, y: m.y }), DT);
  off();
  assert.ok(p.resource < res, 'resource spent');
  assert.ok(hits.length > 0 && hits[0].dealt > 0 || m.hp < before, 'slam damaged the monster');
  assert.ok(RPG.world.skills.cooldownRemaining(p, 'ground-slam') > 0, 'cooldown started');
});

test('learning a skill after equipping gear does not throw and keeps gear stats', () => {
  const w = world(), p = w.player;
  const armor0 = p.stats.armor;
  equipGear(p);
  const armor1 = p.stats.armor;
  assert.ok(armor1 > armor0, 'gear raised armor');
  learn(w, 'whirlwind', 'ground-slam');
  assert.equal(p.stats.armor, armor1, 'snapshot still carries gear');
  assert.equal(typeof p.stats.get, 'undefined', 'stats stays a plain snapshot');
});

test('stats from gear + passives are identical before and after a snapshot restore', () => {
  const w = world(), p = w.player;
  equipGear(p);
  const passive = Object.keys(RPG.data.skills).find((id) => {
    const d = RPG.data.skills[id];
    return d.type === 'passive' && !(d.requires && d.requires.skills) && !((d.requires && d.requires.level) > 1);
  });
  assert.ok(passive, 'a prerequisite-free passive exists');
  learn(w, passive);
  const before = JSON.stringify(p.statBlock.snapshot());
  assert.equal(JSON.stringify(p.stats), before, 'live snapshot matches the block');
  w.difficulty = 'normal';
  const doc = JSON.parse(JSON.stringify(RPG.persistence.snapshot(w, { settings: {}, name: 'x' })));
  const r = RPG.persistence.restore(doc);
  assert.equal(r.ok, true, r.error);
  const w2 = r.world;
  assert.equal(JSON.stringify(w2.player.statBlock.snapshot()), before);
  assert.equal(JSON.stringify(w2.player.stats), before);
});

test('a melee swing that connects pushes a HIT into ctx.hits and publishes it', () => {
  const w = world(), p = w.player;
  const sword = RPG.world.loot.generateItem(RPG.core.Rng.create(1), { baseId: 'sword', rarity: 'common', ilvl: 1 });
  sword.weaponId = 'sword';
  p.weaponSlots[0] = sword;
  const m = RPG.world.spawnMonster(w, 'husk-grunt', p.x + 24, p.y);
  m.hp = m.maxHp = 100000;
  const { out, off } = hitsFor(w, m.id);
  const it = { move: { x: 0, y: 0 }, aim: { x: m.x, y: m.y }, attack: true, skills: [], dash: false, toggles: {} };
  for (let i = 0; i < 40 && !out.length; i++) RPG.sim.step(w, it, DT);
  off && off();
  assert.ok(m.hp < m.maxHp, 'monster damaged');
  assert.ok(out.length >= 1, 'HIT event for the swing');
  assert.ok(out[0].dealt > 0);
});

test('an immediate skill hit publishes a HIT event', () => {
  const w = world(), p = w.player;
  learn(w, 'whirlwind', 'ground-slam');
  RPG.world.skills.equipSkill(p, 'ground-slam', 0);
  const m = RPG.world.spawnMonster(w, 'husk-grunt', p.x + 40, p.y);
  m.hp = m.maxHp = 100000;
  const { out, off } = hitsFor(w, m.id);
  RPG.sim.step(w, castIntent(0, { x: m.x, y: m.y }), DT);
  off && off();
  assert.equal(out.length, 1, 'exactly one HIT (not double-reported by stage 8)');
  assert.ok(out[0].dealt > 0);
});

test('hurtPlayer is blocked during dash i-frames and works after', () => {
  const w = world(), p = w.player;
  const it = { move: { x: 1, y: 0 }, aim: { x: p.x + 100, y: p.y }, attack: false, skills: [], dash: true, toggles: {} };
  RPG.sim.step(w, it, DT);
  assert.ok(p.dash.t > 0);
  const hp = p.hp;
  assert.equal(RPG.sim.hurtPlayer(w, 20, { type: 'phys' }).blocked, 'invuln');
  assert.equal(p.hp, hp);
  const idle = { move: { x: 0, y: 0 }, aim: { x: p.x, y: p.y }, attack: false, skills: [], dash: false, toggles: {} };
  for (let i = 0; i < 20; i++) RPG.sim.step(w, idle, DT);
  assert.equal(RPG.sim.hurtPlayer(w, 20, { type: 'phys' }).blocked, null);
  assert.ok(p.hp < hp);
});

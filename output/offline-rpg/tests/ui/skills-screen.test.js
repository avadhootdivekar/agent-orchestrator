'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'core/entities.js', 'data/skills.js', 'world/stats.js',
  'world/skills.js', 'sim/player.js', 'ui/screens.js', 'ui/skills-screen.js']);
const ss = RPG.ui.skillsScreen;
const plain = (v) => JSON.parse(JSON.stringify(v));

// A level-1 player with `points` skill points to spend. The skill state is the real one from world/skills.js.
const newPlayer = (points = 0, level = 1) => {
  const p = RPG.sim.player.create({ nextEntityId: 1 }, 0, 0);
  p.level = level;
  p.gold = 0;
  p.skills = RPG.world.skills.createSkillState(points);
  // world/skills.js reads and writes passive modifiers on player.stats, so the live game points it at the StatBlock
  // (same wiring as tests/persistence/snapshot.test.js).
  p.stats = p.statBlock;
  return p;
};

// Mock 2D context: every method is a no-op, and fillText strings are recorded so tests can read what was drawn.
const recordingCtx = () => {
  const texts = [];
  const ctx = new Proxy({}, {
    get: (_t, key) => (key === 'fillText' ? (s) => texts.push(String(s)) : () => {}),
    set: () => true,
  });
  return { ctx, texts };
};

test('learning a skill spends one point and raises its rank', () => {
  const p = newPlayer(3);
  assert.deepEqual(plain(ss.learn(p, 'fireball')), { ok: true });
  assert.equal(p.skills.points, 2);
  assert.equal(p.skills.spentPoints, 1);
  assert.equal(p.skills.learned.fireball, 1);
});

test('learning is refused with no points left, and nothing changes', () => {
  const p = newPlayer(0);
  const before = plain(p.skills);
  assert.deepEqual(plain(ss.learn(p, 'fireball')), { ok: false, reason: 'no-points' });
  assert.deepEqual(plain(p.skills), before);
});

test('learning respects the level requirement', () => {
  const p = newPlayer(5, 3); // mend needs level 4
  assert.deepEqual(plain(ss.learn(p, 'mend')), { ok: false, reason: 'level' });
  assert.equal(p.skills.points, 5);
  assert.equal(p.skills.learned.mend, undefined);
});

test('learning respects the prerequisite skill rank', () => {
  const p = newPlayer(5, 4); // level is enough, but mend needs shield 1 first
  assert.deepEqual(plain(ss.learn(p, 'mend')), { ok: false, reason: 'prerequisite' });
  ss.learn(p, 'shield');
  assert.deepEqual(plain(ss.learn(p, 'mend')), { ok: true });
  assert.equal(p.skills.learned.mend, 1);
});

test('learning stops at the max rank', () => {
  const p = newPlayer(50);
  for (let i = 0; i < RPG.data.skills.fireball.maxRank; i++) assert.equal(ss.learn(p, 'fireball').ok, true);
  assert.deepEqual(plain(ss.learn(p, 'fireball')), { ok: false, reason: 'max-rank' });
});

test('an upgrade needs its parent learned and spends its cost in points', () => {
  const p = newPlayer(4);
  assert.deepEqual(plain(ss.upgrade(p, 'fireball', 'bigger-explosion')), { ok: false, reason: 'not-learned' });
  ss.learn(p, 'fireball');
  assert.deepEqual(plain(ss.upgrade(p, 'fireball', 'bigger-explosion')), { ok: true });
  assert.equal(p.skills.points, 2);
  assert.equal(p.skills.upgrades.fireball['bigger-explosion'], 1);
});

test('the requirement text lists the level and prerequisite skill', () => {
  assert.equal(ss.requirementText('mend'), 'Level 4, Shield 1');
  assert.equal(ss.requirementText('fireball'), '');
});

test('the tree lists every skill and every upgrade, in a stable order', () => {
  const rows = plain(ss.treeRows());
  const skillRows = rows.filter((r) => r.kind === 'skill').map((r) => r.skillId);
  assert.deepEqual(skillRows.slice().sort(), plain(Object.keys(RPG.data.skills)).sort());
  const upgradeCount = Object.values(RPG.data.skills).reduce((n, d) => n + d.upgrades.length, 0);
  assert.equal(rows.length - skillRows.length, upgradeCount);
  assert.deepEqual(plain(ss.treeRows()), plain(rows));
});

test('equip-to-bar puts a learned active skill in the hotkey slot', () => {
  const p = newPlayer(2);
  ss.learn(p, 'fireball');
  assert.deepEqual(plain(ss.equipToBar(p, 'fireball', 2)), { ok: true });
  assert.equal(p.skills.bar[2], 'fireball');
});

test('equip-to-bar refuses unlearned skills, passives and bad slots', () => {
  const p = newPlayer(5);
  assert.deepEqual(plain(ss.equipToBar(p, 'fireball', 0)), { ok: false, reason: 'not-slottable' });
  ss.learn(p, 'vitality'); // passive
  assert.deepEqual(plain(ss.equipToBar(p, 'vitality', 0)), { ok: false, reason: 'not-slottable' });
  ss.learn(p, 'fireball');
  assert.equal(ss.equipToBar(p, 'fireball', 7).ok, false);
  assert.equal(ss.equipToBar(p, 'fireball', -1).ok, false);
});

test('the screen equips the cursor skill to the chosen slot through its onKey', () => {
  const p = newPlayer(2);
  const screen = ss.createScreen(p, { x: 0, y: 0 });
  screen.onKey('learn'); // cursor is on fireball, the first row
  assert.equal(p.skills.learned.fireball, 1);
  assert.deepEqual(plain(screen.onKey('equip', 4)), { ok: true });
  assert.equal(p.skills.bar[4], 'fireball');
});

test('the screen moves the cursor over the tree and clamps at both ends', () => {
  const p = newPlayer(0);
  const screen = ss.createScreen(p, { x: 0, y: 0 });
  screen.onKey('up');
  const { ctx, texts } = recordingCtx();
  screen.draw(ctx);
  assert.ok(texts.some((t) => t.startsWith('Skills')));
  for (let i = 0; i < ss.treeRows().length + 5; i++) screen.onKey('down');
  assert.doesNotThrow(() => screen.draw(recordingCtx().ctx));
});

test('respec refunds every spent point for gold and clears learned skills and the bar', () => {
  const p = newPlayer(3, 2);
  p.gold = 100;
  ss.learn(p, 'fireball');
  ss.equipToBar(p, 'fireball', 0);
  const r = ss.respec(p);
  assert.deepEqual(plain(r), { ok: true, refunded: 1 });
  assert.equal(p.gold, 100 - RPG.world.skills.respecCost(p));
  assert.equal(p.skills.points, 3);
  assert.equal(p.skills.spentPoints, 0);
  assert.deepEqual(plain(p.skills.learned), {});
  assert.equal(p.skills.bar[0], null);
});

test('respec is refused without gold and with nothing to refund', () => {
  const p = newPlayer(1, 2);
  assert.deepEqual(plain(ss.respec(p)), { ok: false, reason: 'nothing-to-respec' });
  ss.learn(p, 'fireball');
  assert.deepEqual(plain(ss.respec(p)), { ok: false, reason: 'no-gold' });
  assert.equal(p.skills.learned.fireball, 1);
});

test('the screen draws the points and the lock reason for the cursor row', () => {
  const p = newPlayer(2, 1);
  const screen = ss.createScreen(p, { x: 0, y: 0 });
  const { ctx, texts } = recordingCtx();
  screen.draw(ctx);
  assert.ok(texts.includes('Skills  (points: 2)'));
  assert.ok(texts.some((t) => t.includes('Fireball')));
});

test('the screen registers with the real screen manager and draws when opened', () => {
  const p = newPlayer(1);
  const mgr = RPG.ui.screens.create({ bindings: {} });
  mgr.register(ss.createScreen(p, { x: 0, y: 0 }));
  mgr.open('skills');
  assert.equal(mgr.top(), 'skills');
  assert.equal(mgr.paused(), true);
  const { ctx, texts } = recordingCtx();
  mgr.draw(ctx);
  assert.ok(texts.some((t) => t.startsWith('Skills')));
});

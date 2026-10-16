'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['world/progression.js']);
const prog = RPG.world.progression;

// Objects built inside the vm context have another realm's prototypes, so compare them as plain JSON.
const plain = (v) => JSON.parse(JSON.stringify(v));

test('xpToNext follows the documented curve and is strictly increasing', () => {
  assert.equal(prog.xpToNext(1), 100);
  assert.equal(prog.xpToNext(2), 283); // round(100 * 2^1.5)
  let prev = 0;
  for (let level = 1; level < prog.MAX_LEVEL; level++) {
    const cost = prog.xpToNext(level);
    assert.ok(cost > prev, `cost for level ${level} (${cost}) must exceed ${prev}`);
    prev = cost;
  }
});

test('totalXpForLevel gives exact thresholds at each level boundary', () => {
  assert.equal(prog.totalXpForLevel(1), 0);
  assert.equal(prog.totalXpForLevel(2), 100);
  assert.equal(prog.totalXpForLevel(3), 100 + 283);
  for (let level = 2; level <= prog.MAX_LEVEL; level++) {
    assert.ok(prog.totalXpForLevel(level) > prog.totalXpForLevel(level - 1));
  }
});

test('XP curve helpers reject invalid levels instead of guessing', () => {
  assert.throws(() => prog.xpToNext(0), /level must be an integer >= 1/);
  assert.throws(() => prog.xpToNext(1.5), /level must be an integer >= 1/);
  assert.throws(() => prog.totalXpForLevel(-1), /level must be an integer >= 1/);
});

test('XP just below a threshold does not level up; reaching it does, with the remainder carried', () => {
  const p = prog.createProgression();
  prog.addXp(p, 99);
  assert.equal(p.level, 1);
  assert.equal(p.xp, 99);

  prog.addXp(p, 1);
  assert.equal(p.level, 2);
  assert.equal(p.xp, 0);

  prog.addXp(p, 282); // one short of the level 2 -> 3 cost of 283
  assert.equal(p.level, 2);
  assert.equal(p.xp, 282);

  prog.addXp(p, 1);
  assert.equal(p.level, 3);
  assert.equal(p.xp, 0);
});

test('one award can cross several levels and carries the remainder into the final level', () => {
  const p = prog.createProgression();
  const { gained, levelUps } = prog.addXp(p, prog.totalXpForLevel(3) + 50); // 383 + 50 = 433
  assert.equal(gained, 433);
  assert.equal(p.level, 3);
  assert.equal(p.xp, 50);
  assert.deepEqual(plain(levelUps.map((g) => g.level)), [2, 3]);
});

test('skill points are granted per level and the total granted is recorded', () => {
  const p = prog.createProgression();
  assert.equal(p.skillPointsGranted, 0);

  const { levelUps } = prog.addXp(p, prog.totalXpForLevel(4));
  assert.equal(p.level, 4);
  assert.equal(p.skillPointsGranted, 3);
  assert.deepEqual(plain(levelUps.map((g) => g.skillPoints)), [1, 1, 1]);
});

test('level-up hook runs once per level gained, in order, with stat growth attached', () => {
  const seen = [];
  const p = prog.createProgression({ onLevelUp: (grant) => seen.push(plain(grant)) });
  prog.addXp(p, prog.totalXpForLevel(3));
  assert.deepEqual(seen.map((g) => g.level), [2, 3]);
  assert.deepEqual(seen[0].statGrowth, { maxHp: 12, maxResource: 3, damage: 1.2 });
  assert.equal(seen[0].skillPoints, 1);
});

test('xpMultiplier scales awards and rounds down', () => {
  const boosted = prog.createProgression({ xpMultiplier: 1.25 });
  assert.equal(prog.addXp(boosted, 100).gained, 125);
  assert.equal(boosted.level, 2);
  assert.equal(boosted.xp, 25);

  const reduced = prog.createProgression({ xpMultiplier: 0.9 });
  assert.equal(prog.addXp(reduced, 15).gained, 13); // floor(13.5)
  assert.equal(reduced.xp, 13);
});

test('the level cap stops level-ups and discards XP earned at the cap', () => {
  const p = prog.createProgression({ maxLevel: 3 });
  prog.addXp(p, 1000000);
  assert.equal(p.level, 3);
  assert.equal(p.xp, 0);
  assert.equal(p.skillPointsGranted, 2);
});

test('invalid awards and options are rejected', () => {
  const p = prog.createProgression();
  assert.throws(() => prog.addXp(p, -1), /XP amount must be a finite number >= 0/);
  assert.throws(() => prog.addXp(p, NaN), /XP amount must be a finite number >= 0/);
  assert.throws(() => prog.createProgression({ xpMultiplier: -0.5 }), /xpMultiplier must be a finite number/);
  assert.throws(() => prog.createProgression({ maxLevel: 0 }), /maxLevel must be an integer >= 1/);
});

test('serialize -> JSON -> restore round-trips and play continues identically', () => {
  const original = prog.createProgression();
  prog.addXp(original, 500);
  const snapshot = JSON.parse(JSON.stringify(prog.serialize(original)));
  assert.deepEqual(snapshot, { level: original.level, xp: original.xp, skillPointsGranted: original.skillPointsGranted });

  const restored = prog.restore(snapshot);
  assert.equal(restored.level, original.level);
  assert.equal(restored.xp, original.xp);
  assert.equal(restored.skillPointsGranted, original.skillPointsGranted);

  prog.addXp(original, 2000);
  prog.addXp(restored, 2000);
  assert.deepEqual(plain(prog.serialize(restored)), plain(prog.serialize(original)));
});

test('restore takes the multiplier from the caller, not from the save', () => {
  const restored = prog.restore({ level: 1, xp: 0, skillPointsGranted: 0 }, { xpMultiplier: 2 });
  assert.equal(prog.addXp(restored, 10).gained, 20);
});

test('restore rejects snapshots that could not come from serialize', () => {
  const bad = [
    null,
    { level: 0, xp: 0, skillPointsGranted: 0 },
    { level: 2.5, xp: 0, skillPointsGranted: 0 },
    { level: 99, xp: 0, skillPointsGranted: 0 },
    { level: 1, xp: -1, skillPointsGranted: 0 },
    { level: 1, xp: 100, skillPointsGranted: 0 }, // a full level's XP should already have levelled up
    { level: 1, xp: 1.5, skillPointsGranted: 0 },
    { level: 1, xp: 0 },
    { level: 1, xp: 0, skillPointsGranted: -1 },
  ];
  for (const data of bad) {
    assert.throws(() => prog.restore(data));
  }
});

test('restore rejects XP stored at the level cap', () => {
  assert.throws(() => prog.restore({ level: 3, xp: 5, skillPointsGranted: 2 }, { maxLevel: 3 }), /out of range/);
  assert.throws(() => prog.restore({ level: 4, xp: 0, skillPointsGranted: 3 }, { maxLevel: 3 }), /exceeds cap/);
});

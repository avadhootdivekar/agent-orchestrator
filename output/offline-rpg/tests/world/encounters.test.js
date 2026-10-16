'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'core/rng.js', 'data/difficulties.js', 'data/monsters.js', 'world/encounters.js']);
const { Rng } = RPG.core;
const { encounters } = RPG.world;
const DIFF = RPG.data.difficulties;

// Inline fixtures: an area with a mixed table, and a 12-slot grid with a 40-unit radius.
const AREA = {
  spawnTable: [
    { monsterId: 'husk-grunt', weight: 3, min: 2, max: 4 },
    { monsterId: 'skitter', weight: 2, min: 3, max: 5 },
    { monsterId: 'bone-archer', weight: 2, min: 1, max: 2 },
    { monsterId: 'stone-brute', weight: 1, min: 1, max: 1 },
  ],
  budget: 40,
  eliteChance: 0.2,
};
const SLOTS = Array.from({ length: 12 }, (_, i) => ({ x: 100 + (i % 4) * 150, y: 100 + Math.floor(i / 4) * 150, r: 40 }));

const compose = (seed, area = AREA, slots = SLOTS, diff = DIFF.normal) =>
  encounters.compose(area, slots, 3, diff, new Rng(seed));

test('same seed gives the same composition, a different seed gives a different one', () => {
  assert.deepEqual(compose(1234), compose(1234));
  assert.notDeepEqual(compose(1234), compose(4321));
});

test('budget is respected and actually used, scaled by spawnDensity', () => {
  const spent = (list) => list.reduce((sum, m) => sum + encounters.pointCost(m.defId, m.elite), 0);
  let total = 0;
  for (let seed = 1; seed <= 200; seed++) {
    const cost = spent(compose(seed));
    assert.ok(cost <= AREA.budget, `seed ${seed} spent ${cost} of ${AREA.budget}`);
    total += cost;
  }
  assert.ok(total / 200 > AREA.budget * 0.5, `average spend ${total / 200} is too low to test the budget`);

  for (let seed = 1; seed <= 50; seed++) {
    assert.ok(spent(compose(seed, AREA, SLOTS, DIFF.hard)) <= AREA.budget * DIFF.hard.spawnDensity);
  }
});

test('only real, non-boss monster ids are emitted, each inside a slot disc', () => {
  for (let seed = 1; seed <= 100; seed++) {
    for (const m of compose(seed)) {
      const def = RPG.data.monsters[m.defId];
      assert.ok(def, `unknown monster ${m.defId}`);
      assert.notEqual(def.archetype, 'boss');
      assert.ok(AREA.spawnTable.some((e) => e.monsterId === m.defId), `${m.defId} is not in the spawnTable`);
      assert.ok(SLOTS.some((s) => Math.hypot(m.x - s.x, m.y - s.y) <= s.r + 0.05), `(${m.x}, ${m.y}) is outside every slot`);
      assert.equal(m.elite, m.affixes.length > 0);
    }
  }
});

test('elite ratio over 500 rolls is close to the configured eliteChance', () => {
  // One slot and a one-member table give exactly one elite roll per composition.
  const single = { spawnTable: [{ monsterId: 'husk-grunt', weight: 1, min: 1, max: 1 }], budget: 10, eliteChance: 0.2 };
  let elites = 0;
  for (let seed = 1; seed <= 500; seed++) {
    const [m] = compose(seed, single, SLOTS.slice(0, 1));
    assert.equal(m.defId, 'husk-grunt');
    if (m.elite) {
      elites++;
      assert.equal(m.affixes.length, 1);
      assert.ok(encounters.ELITE_AFFIXES.includes(m.affixes[0]));
    } else {
      assert.equal(m.affixes.length, 0);
    }
  }
  const ratio = elites / 500;
  assert.ok(Math.abs(ratio - 0.2) < 0.05, `elite ratio ${ratio}`);
});

test('higher difficulty gives more spawns and more elites', () => {
  const totals = (diff) => {
    let count = 0, elites = 0;
    for (let seed = 1; seed <= 200; seed++) {
      const list = compose(seed, AREA, SLOTS, diff);
      count += list.length;
      elites += list.filter((m) => m.elite).length;
    }
    return { count, elites };
  };
  const easy = totals(DIFF.easy), nightmare = totals(DIFF.nightmare);
  assert.ok(nightmare.count > easy.count, JSON.stringify({ easy, nightmare }));
  assert.ok(nightmare.elites > easy.elites, JSON.stringify({ easy, nightmare }));
});

test('an empty slot list gives an empty composition', () => {
  // Length check, not deepEqual: the module's arrays come from the vm realm and fail a prototype comparison with [].
  assert.equal(compose(5, AREA, []).length, 0);
});

test('unknown monsters and bosses in a spawnTable are rejected', () => {
  const bad = (monsterId) => ({ spawnTable: [{ monsterId, weight: 1, min: 1, max: 1 }], budget: 5, eliteChance: 0 });
  assert.throws(() => compose(1, bad('no-such-monster')), /unknown monster no-such-monster/);
  assert.throws(() => compose(1, bad('warden-of-ash')), /boss warden-of-ash/);
});

test('boss modifiers are distinct pool members, seeded, and scale with bossHp', () => {
  const boss = RPG.data.monsters['warden-of-ash'];
  const roll = (seed, diff) => encounters.bossModifiers(boss, diff, new Rng(seed));
  assert.deepEqual(roll(9, DIFF.hard), roll(9, DIFF.hard));
  for (let seed = 1; seed <= 50; seed++) {
    const mods = roll(seed, DIFF.nightmare);
    assert.equal(mods.length, 2);
    assert.equal(new Set(mods).size, mods.length);
    for (const id of mods) assert.ok(boss.modifierPool.includes(id));
  }
  assert.equal(roll(1, DIFF.normal).length, 1);
  assert.equal(roll(1, DIFF.custom).length, Math.min(boss.modifierPool.length, Math.round(DIFF.custom.bossHp)));
});

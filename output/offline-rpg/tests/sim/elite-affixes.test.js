'use strict';

// Elite affixes (data/monsters.js RPG.data.eliteAffixes): each one changes behaviour or stats in the sim, and a seed
// gives the same elite composition every time.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js', 'core/state.js',
  'data/difficulties.js', 'data/monsters.js', 'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/spatial.js',
  'world/combat.js', 'world/ai.js', 'world/boss.js', 'world/world.js', 'world/encounters.js', 'data/maps/test-map.js',
  'sim/player.js', 'sim/combat-step.js', 'sim/sim.js', 'sim/monsters.js',
]);
const stage = RPG.sim.stage;
const DT = 1 / 60;
const AFFIXES = RPG.data.eliteAffixes;
const IDS = Object.keys(AFFIXES);

const OPEN_ROWS = Array.from({ length: 30 }, (_, y) => (y === 0 || y === 29 ? '#'.repeat(40) : '#' + '.'.repeat(38) + '#'));

function makeWorld(seed = 7) {
  const w = RPG.sim.createWorld({ seed });
  w.area.map = RPG.world.tilemap.fromRows(OPEN_ROWS, 32);
  w.player.x = 600; w.player.y = 400;
  w.player.hp = w.player.maxHp = 100000;
  w.scratch = { dt: DT };
  return w;
}

const spawn = (w, affix, x = 100, y = 400, defId = 'husk-grunt') =>
  RPG.world.spawnMonster(w, defId, x, y, { elite: !!affix, affixes: affix ? [affix] : [] });

// stage 'affixes' runs between statuses and resolve; this mirrors that for a single tick.
function affixTick(w) { stage.affixes(w, w.scratch); }

test('data: every affix has a name, a colour token and at least one numeric effect', () => {
  assert.deepEqual([...IDS], ['fast', 'shielded', 'vampiric', 'explosive-death', 'regenerating']);
  const effects = ['speedMult', 'shieldFraction', 'leechFraction', 'deathRadius', 'regenPerSecond'];
  for (const id of IDS) {
    const a = AFFIXES[id];
    assert.equal(a.id, id);
    assert.ok(a.name && /^affix-/.test(a.color), id + ' needs a name and an affix-* colour token');
    assert.ok(effects.some((k) => typeof a[k] === 'number' && a[k] > 0), id + ' has no effect numbers');
  }
  assert.equal(new Set(IDS.map((id) => AFFIXES[id].color)).size, IDS.length, 'colour tokens are distinct');
  assert.deepEqual([...RPG.world.encounters.ELITE_AFFIXES], [...IDS]);
});

test('sim runs the affixes stage between statuses and resolve', () => {
  const o = RPG.sim.ORDER;
  assert.equal(o.indexOf('affixes'), o.indexOf('statuses') + 1);
  assert.equal(o.indexOf('resolve'), o.indexOf('affixes') + 1);
});

test('fast: the elite closes distance faster than the same elite without the affix', () => {
  const run = (affix) => {
    const w = makeWorld();
    const m = spawn(w, affix, 430, 400);
    m.elite = true;
    const x0 = m.x;
    for (let i = 0; i < 30; i++) {
      stage.spatial(w, w.scratch); stage.monsters(w, w.scratch); stage.movement(w, w.scratch);
    }
    return m.x - x0;
  };
  const base = run(null), fast = run('fast');
  assert.ok(base > 0);
  assert.ok(fast > base * (AFFIXES.fast.speedMult - 0.05), `fast moved ${fast}, plain ${base}`);
  assert.equal(RPG.sim.monsters.affixSpeedMult({ elite: true, affixes: ['fast'] }), AFFIXES.fast.speedMult);
  assert.equal(RPG.sim.monsters.affixSpeedMult({ elite: false, affixes: ['fast'] }), 1);
});

test('shielded: a shield worth a fraction of max hp soaks damage before hp, then damage goes through', () => {
  const w = makeWorld();
  const m = spawn(w, 'shielded'), plain = spawn(w, null, 200, 400);
  const shield = m.maxHp * AFFIXES.shielded.shieldFraction;
  affixTick(w);
  assert.equal(m.shield, shield);
  m.hp -= 10; plain.hp -= 10;
  affixTick(w);
  assert.equal(m.hp, m.maxHp, 'soaked by the shield');
  assert.equal(m.shield, shield - 10);
  assert.equal(plain.maxHp - plain.hp, 10, 'plain monsters are untouched by the stage');
  assert.equal(plain.shield, undefined);

  // Spend the rest of the shield, then the next hit lands on hp.
  m.hp -= m.shield; affixTick(w);
  assert.equal(m.shield, 0);
  assert.equal(m.hp, m.maxHp);
  m.hp -= 10; affixTick(w);
  assert.equal(m.hp, m.maxHp - 10);
});

test('shielded: a lethal blow the shield covers is survived, and an elite whose shield is gone still dies', () => {
  const w = makeWorld();
  const m = spawn(w, 'shielded');
  affixTick(w);
  m.hp = 0; m.dead = true;     // resolveHit caps damage at the hp left, so a kill loses exactly the hp it had
  affixTick(w);
  assert.ok(!m.dead && m.hp > 0, 'shield absorbed the killing blow');
  // Keep killing it: finite shield means it must die within a bounded number of lethal blows.
  let blows = 0;
  while (!m.dead && blows++ < 100) { m.hp = 0; m.dead = true; affixTick(w); }
  assert.ok(m.dead, 'shielded elite is not unkillable');
  assert.ok(blows < 100);
});

test('vampiric: heals by a fraction of the damage it deals to the player (melee and enemy shots)', () => {
  const w = makeWorld();
  const m = spawn(w, 'vampiric');
  m.hp = m.maxHp / 2;
  const before = m.hp;
  const out = RPG.sim.hurtPlayer(w, 40, { source: 'monster', attackerId: m.id, type: 'phys', ignoreArmor: true });
  assert.ok(out.dealt > 0);
  assert.ok(Math.abs(m.hp - (before + out.dealt * AFFIXES.vampiric.leechFraction)) < 1e-6, `hp ${m.hp}`);

  const shooter = spawn(w, 'vampiric', 150, 400);
  shooter.hp = 1;
  w.player.status = {};   // clear the i-frames the first hit bought
  const out2 = RPG.sim.hurtPlayer(w, 40, { type: 'phys', ignoreArmor: true, source: shooter });
  assert.ok(Math.abs(shooter.hp - (1 + out2.dealt * AFFIXES.vampiric.leechFraction)) < 1e-6);

  const plain = spawn(w, null, 200, 400);
  plain.hp = 5;
  w.player.status = {};
  RPG.sim.hurtPlayer(w, 40, { source: 'monster', attackerId: plain.id, type: 'phys', ignoreArmor: true });
  assert.equal(plain.hp, 5, 'a non-vampiric monster does not heal');
});

test('vampiric: healing never exceeds max hp', () => {
  const w = makeWorld();
  const m = spawn(w, 'vampiric');
  m.hp = m.maxHp - 1;
  RPG.sim.hurtPlayer(w, 500, { source: 'monster', attackerId: m.id, type: 'phys', ignoreArmor: true });
  assert.equal(m.hp, m.maxHp);
});

test('explosive-death: blasts the player in range exactly once, and not outside the radius', () => {
  const w = makeWorld();
  const near = spawn(w, 'explosive-death', 560, 400), far = spawn(w, 'explosive-death', 100, 100);
  near.dead = true; far.dead = true;
  const hp0 = w.player.hp;
  affixTick(w); affixTick(w);
  const lost = hp0 - w.player.hp;
  assert.ok(lost > 0, 'player in range takes the blast');
  assert.ok(lost <= near.damage * AFFIXES['explosive-death'].deathDamageMult + 1e-6);
  assert.equal(near.affixBlasted, true);

  const w2 = makeWorld();
  const far2 = spawn(w2, 'explosive-death', 100, 100);
  far2.dead = true;
  const hp1 = w2.player.hp;
  affixTick(w2);
  assert.equal(w2.player.hp, hp1, 'player out of range is unharmed');
});

test('explosive-death: only elites with the affix explode', () => {
  const w = makeWorld();
  const a = spawn(w, null, 560, 400), b = spawn(w, 'fast', 560, 400);
  a.dead = true; b.dead = true;
  const hp0 = w.player.hp;
  affixTick(w);
  assert.equal(w.player.hp, hp0);
});

test('regenerating: heals over time up to max hp; other elites do not', () => {
  const w = makeWorld();
  const m = spawn(w, 'regenerating'), other = spawn(w, 'fast', 200, 400);
  m.hp = other.hp = m.maxHp / 2;
  const half = m.hp;
  for (let i = 0; i < 60; i++) affixTick(w);
  const expected = AFFIXES.regenerating.regenPerSecond * m.maxHp;
  assert.ok(Math.abs((m.hp - half) - expected) < expected * 0.02, `healed ${m.hp - half}, expected ${expected}`);
  assert.equal(other.hp, half);
  for (let i = 0; i < 60 * 60; i++) affixTick(w);
  assert.equal(m.hp, m.maxHp);
});

test('non-elite monsters ignore affix lists', () => {
  const w = makeWorld();
  const m = RPG.world.spawnMonster(w, 'husk-grunt', 100, 400, { elite: false, affixes: ['regenerating'] });
  m.hp = 5;
  affixTick(w);
  assert.equal(m.hp, 5);
});

test('encounters: same seed gives identical elite composition and affixes; every affix can be rolled', () => {
  const AREA = {
    spawnTable: [{ monsterId: 'husk-grunt', weight: 3, min: 2, max: 4 }, { monsterId: 'skitter', weight: 2, min: 3, max: 5 }],
    budget: 60, eliteChance: 0.6,
  };
  const slots = Array.from({ length: 12 }, (_, i) => ({ x: 100 + (i % 4) * 150, y: 100 + Math.floor(i / 4) * 150, r: 40 }));
  const compose = (seed) => RPG.world.encounters.compose(AREA, slots, 3, RPG.data.difficulties.normal, new RPG.core.Rng(seed));
  const seen = new Set();
  for (let seed = 1; seed <= 100; seed++) {
    const a = compose(seed), b = compose(seed);
    assert.deepEqual(a, b, 'seed ' + seed);
    for (const m of a) {
      if (!m.elite) continue;
      assert.equal(m.affixes.length, 1);
      assert.ok(IDS.includes(m.affixes[0]));
      seen.add(m.affixes[0]);
    }
  }
  assert.deepEqual([...seen].sort(), [...IDS].sort());
});

test('world: seeded area spawns carry the same elite affixes for the same seed', () => {
  const sig = (seed) => {
    const w = RPG.sim.createWorld({ seed, mapId: 'test-map' });
    return JSON.stringify(RPG.world.encounters.compose(
      { spawnTable: [{ monsterId: 'husk-grunt', weight: 1, min: 3, max: 4 }], budget: 30, eliteChance: 0.5 },
      [{ x: 100, y: 100, r: 30 }, { x: 300, y: 100, r: 30 }, { x: 500, y: 100, r: 30 }], 2, RPG.data.difficulties.hard, w.areaRng('x')));
  };
  assert.equal(sig(99), sig(99));
});

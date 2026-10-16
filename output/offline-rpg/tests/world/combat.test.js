'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/rng.js', 'world/stats.js', 'world/combat.js']);
const { StatBlock, combat } = RPG.world;
const { Rng } = RPG.core;

function ent(id, x, y, base) {
  const stats = new StatBlock(base);
  return { id, x, y, vx: 0, vy: 0, hp: stats.get('maxHp'), maxHp: stats.get('maxHp'), dead: false, flash: 0, stats, status: {}, cooldowns: {} };
}
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} !== ${b}`);

test('resolveHit is deterministic for a fixed seed', () => {
  const run = () => {
    const a = ent(1, 0, 0, { critChance: 0.5 }), t = ent(2, 10, 0);
    const rng = Rng.create(1234), out = [];
    for (let i = 0; i < 20; i++) out.push(JSON.stringify(combat.resolveHit(a, t, { amount: 10 }, rng)));
    return out;
  };
  assert.deepEqual(run(), run());
  const crits = run().map((s) => JSON.parse(s).crit);
  assert.ok(crits.includes(true) && crits.includes(false));
});

test('resolveHit does not mutate entities', () => {
  const a = ent(1, 0, 0), t = ent(2, 10, 0);
  combat.resolveHit(a, t, { amount: 50, knockback: 100, status: [{ id: 'burn', duration: 2, dps: 3 }] }, Rng.create(1));
  assert.equal(t.hp, 100);
  assert.equal(t.vx, 0);
  assert.deepEqual(t.status, {});
});

test('armor mitigates physical damage only; ignoreArmor bypasses it', () => {
  const a = ent(1, 0, 0, { critChance: 0 }), t = ent(2, 5, 0, { armor: 100 });
  const rng = Rng.create(1);
  near(combat.resolveHit(a, t, { amount: 40 }, rng).dealt, 20);
  near(combat.resolveHit(a, t, { amount: 40, type: 'fire' }, rng).dealt, 40);
  near(combat.resolveHit(a, t, { amount: 40, ignoreArmor: true }, rng).dealt, 40);
  near(combat.armorMitigation(0), 0);
});

test('resistance reduces matching element only', () => {
  const a = ent(1, 0, 0, { critChance: 0 }), t = ent(2, 5, 0, { resFire: 0.5, armor: 0 });
  const rng = Rng.create(1);
  near(combat.resolveHit(a, t, { amount: 40, type: 'fire' }, rng).dealt, 20);
  near(combat.resolveHit(a, t, { amount: 40, type: 'cold' }, rng).dealt, 40);
});

test('crit multiplies by critDamage; critChance 1 always crits, 0 never', () => {
  const t = ent(2, 5, 0, { armor: 0 }), rng = Rng.create(9);
  const always = ent(1, 0, 0, { critChance: 0.75, critDamage: 2 });
  always.stats.addModifier({ source: 'x', stat: 'critChance', kind: 'flat', value: 1 }); // clamps to the 0.75 cap
  assert.equal(always.stats.get('critChance'), 0.75);
  const never = ent(1, 0, 0, { critChance: 0, critDamage: 2 });
  for (let i = 0; i < 30; i++) assert.equal(combat.resolveHit(never, t, { amount: 10 }, rng).crit, false);
  const noCrit = combat.resolveHit(never, t, { amount: 10, canCrit: true }, rng);
  near(noCrit.dealt, 10);
  // find a crit with the 0.75 attacker and check the multiplier
  let res;
  for (let i = 0; i < 50 && !(res && res.crit); i++) res = combat.resolveHit(always, t, { amount: 10 }, rng);
  assert.equal(res.crit, true);
  near(res.dealt, 20);
  assert.equal(combat.resolveHit(always, t, { amount: 10, canCrit: false }, rng).crit, false);
});

test('life steal heals the attacker by a fraction of damage dealt, capped at maxHp', () => {
  const a = ent(1, 0, 0, { critChance: 0, lifeSteal: 0.2 }), t = ent(2, 5, 0, { armor: 0 });
  a.hp = 50;
  const r = combat.resolveHit(a, t, { amount: 30 }, Rng.create(1));
  near(r.lifeSteal, 6);
  combat.applyHit(a, t, r);
  near(a.hp, 56);
  near(t.hp, 70);
  a.hp = 99.5;
  combat.applyHit(a, t, combat.resolveHit(a, t, { amount: 30 }, Rng.create(1)));
  assert.equal(a.hp, 100);
});

test('knockback pushes the target away from the attacker', () => {
  const t = ent(2, 0, 10);
  const r = combat.resolveHit(ent(1, 0, 0), t, { amount: 1, knockback: 200 }, Rng.create(1));
  near(r.knockback.vx, 0);
  near(r.knockback.vy, 200);
  combat.applyHit(null, t, r);
  near(t.vy, 200);
  const left = combat.resolveHit(ent(1, 10, 0), ent(2, 0, 0), { amount: 1, knockback: 50 }, Rng.create(1));
  assert.ok(left.knockback.vx < 0);
  const same = combat.resolveHit(ent(1, 3, 3), ent(2, 3, 3), { amount: 1, knockback: 50 }, Rng.create(1));
  assert.deepEqual([same.knockback.vx, same.knockback.vy], [50, 0]);
});

test('hit emits flash and damage-number events; kill sets dead and clamps hp', () => {
  const a = ent(1, 0, 0, { critChance: 0 }), t = ent(2, 5, 7, { armor: 0 });
  t.hp = 10;
  const r = combat.resolveHit(a, t, { amount: 50 }, Rng.create(1));
  assert.equal(r.killed, true);
  near(r.dealt, 10);
  assert.deepEqual([...r.events.map((e) => e.type)], ['hitFlash', 'damageNumber']);
  assert.equal(r.events[1].amount, 10);
  combat.applyHit(a, t, r);
  assert.equal(t.dead, true);
  assert.equal(t.hp, 0);
  assert.ok(t.flash > 0);
});

test('burn ticks damage over time then expires', () => {
  const t = ent(2, 0, 0, { resFire: 0 });
  combat.applyStatus(t, { id: 'burn', duration: 2, dps: 10 });
  assert.ok(combat.hasStatus(t, 'burn'));
  let events = [];
  for (let i = 0; i < 8; i++) events = events.concat(combat.tickStatuses(t, 0.25));
  near(t.hp, 80);                       // 2 s * 10 dps
  assert.equal(events.length, 4);       // one tick per 0.5 s
  assert.equal(combat.hasStatus(t, 'burn'), false);
  combat.tickStatuses(t, 5);
  near(t.hp, 80);
});

test('burn respects fire resistance and can kill', () => {
  const t = ent(2, 0, 0, { resFire: 0.5 });
  combat.applyStatus(t, { id: 'burn', duration: 1, dps: 20 });
  combat.tickStatuses(t, 1);
  near(t.hp, 90);
  const w = ent(3, 0, 0); w.hp = 3;
  combat.applyStatus(w, { id: 'burn', duration: 4, dps: 20 });
  combat.tickStatuses(w, 1);
  assert.equal(w.dead, true);
  assert.equal(w.hp, 0);
});

test('slow scales move speed and expires; refresh keeps the stronger value', () => {
  const t = ent(2, 0, 0);
  assert.equal(combat.moveSpeedFactor(t), 1);
  combat.applyStatus(t, { id: 'slow', duration: 2, strength: 0.4 });
  near(combat.moveSpeedFactor(t), 0.6);
  combat.tickStatuses(t, 1);
  combat.applyStatus(t, { id: 'slow', duration: 2, strength: 0.2 });
  near(t.status.slow.remaining, 2);
  near(combat.moveSpeedFactor(t), 0.6);
  combat.tickStatuses(t, 2);
  assert.equal(combat.moveSpeedFactor(t), 1);
  assert.equal(combat.hasStatus(t, 'slow'), false);
});

test('stack policy accumulates dps; statuses with chance are rolled by the seeded RNG', () => {
  const t = ent(2, 0, 0);
  combat.applyStatus(t, { id: 'burn', duration: 3, dps: 5, stacks: 'stack' });
  combat.applyStatus(t, { id: 'burn', duration: 3, dps: 5, stacks: 'stack' });
  assert.equal(t.status.burn.dps, 10);
  assert.equal(t.status.burn.stacks, 2);
  const a = ent(1, 0, 0, { critChance: 0 });
  const spec = [{ id: 'burn', chance: 0.5, duration: 3, dps: 5 }];
  const roll = (seed) => combat.resolveHit(a, t, { amount: 1, status: spec }, Rng.create(seed)).statuses.length;
  assert.equal(roll(5), roll(5));
  const results = new Set(); for (let s = 0; s < 40; s++) results.add(roll(s));
  assert.deepEqual([...results].sort(), [0, 1]);
  const applied = combat.resolveHit(a, ent(3, 5, 0), { amount: 1, status: [{ id: 'slow', duration: 1, strength: 0.3 }] }, Rng.create(1));
  assert.equal(applied.statuses.length, 1);
});

test('cooldowns count down, are shortened by cooldownReduction, and reopen when ready', () => {
  const plain = ent(1, 0, 0), reduced = ent(2, 0, 0, { cooldownReduction: 0.25 });
  near(combat.startCooldown(plain, 'fireball', 4), 4);
  near(combat.startCooldown(reduced, 'fireball', 4), 3);
  assert.equal(combat.isReady(reduced, 'fireball'), false);
  combat.tickCooldowns(reduced, 2.9);
  assert.equal(combat.isReady(reduced, 'fireball'), false);
  combat.tickCooldowns(reduced, 0.2);
  assert.equal(combat.isReady(reduced, 'fireball'), true);
  assert.equal(combat.isReady(plain, 'unknown'), true);
  const capped = ent(3, 0, 0, { cooldownReduction: 0.9 });   // StatBlock caps at 0.5
  near(combat.startCooldown(capped, 'x', 10), 5);
});

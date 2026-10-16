'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');

// Own loader (tests/sim/load.js belongs to another unit): the combat modules in build order, no DOM.
const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/pool.js', 'core/entities.js', 'core/state.js', 'core/events.js',
  'data/skills.js', 'data/monsters.js', 'data/weapons.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/world.js', 'world/combat.js',
  'world/weapons.js', 'world/skills.js', 'world/skill-effects.js', 'sim/combat-step.js'
]);
const { HIT } = RPG.core.events.NAMES;
const { StatBlock } = RPG.world;
const combat = RPG.world.combat;
const stage = RPG.sim.stage;
const DT = 1 / 60;

const hits = [];
RPG.core.events.on(HIT, (h) => hits.push(h));

// 20x20 walled room, 32 px tiles, so the player at (320, 320) has open ground all around.
const ROOM = Array.from({ length: 20 }, (_, y) => (y === 0 || y === 19 ? '#'.repeat(20) : '#' + '.'.repeat(18) + '#'));

function mkWorld() {
  const w = RPG.core.state.createWorldState({ seed: 7 });
  w.area = { map: RPG.world.tilemap.fromRows(ROOM, 32) };
  const p = RPG.core.entities.baseEntity('player', 320, 320, 10, 0);
  p.id = w.nextEntityId++;
  p.stats = new StatBlock().snapshot(); // what sim/player.js stores: a plain object, no get()
  p.hp = p.maxHp = p.stats.maxHp;
  p.cooldowns = {};
  w.player = p;
  // Same ctx shape sim.step() provides (begin stage clears the arrays each tick).
  w.scratch = { intent: null, dt: DT, events: [], hits: [], killed: [], spatial: null, bossWorld: null, actions: null };
  return w;
}

/** Runs the three combat stages in pipeline order for `secs` of game time. */
function run(w, secs) {
  for (let t = 0; t < secs - 1e-9; t += DT) {
    w.scratch.dt = DT;
    w.scratch.hits.length = 0;
    stage.projectiles(w, w.scratch);
    stage.statuses(w, w.scratch);
    stage.resolve(w, w.scratch);
  }
}

function mon(w, defId, x, y) {
  return RPG.world.spawnMonster(w, defId, x, y);
}

function enemyShot(w, x, y, vx, vy, damage, ownerId) {
  const s = { id: w.nextEntityId++, kind: 'projectile', team: 1, x, y, vx, vy, r: 4, damage, ownerId, lifetime: 2, age: 0 };
  w.enemyProjectiles.push(s);
  return s;
}

test('the combat stages are registered under the pipeline names', () => {
  for (const name of ['projectiles', 'statuses', 'resolve']) assert.equal(typeof stage[name], 'function', name);
  assert.equal(typeof RPG.sim.hurtPlayer, 'function');
});

test('hurtPlayer applies armor to physical damage and emits HIT for the player', () => {
  const w = mkWorld();
  hits.length = 0;
  const armor = w.player.stats.armor;
  const r = RPG.sim.hurtPlayer(w, 20, { type: 'phys' });
  const expected = 20 * (1 - armor / (armor + combat.ARMOR_K));
  assert.ok(Math.abs(r.dealt - expected) < 1e-9, `dealt ${r.dealt} vs ${expected}`);
  assert.ok(Math.abs(w.player.hp - (100 - expected)) < 1e-9);
  // HIT is queued on the tick ctx and published by the resolve stage.
  stage.resolve(w, w.scratch);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].targetId, w.player.id);
  assert.equal(hits[0].team, 0);
  assert.equal(hits[0].type, 'phys');
  assert.ok(Math.abs(hits[0].dealt - expected) < 1e-9);
});

test('hurtPlayer honours elemental resistance, and armor only applies to phys', () => {
  const fire = mkWorld();
  fire.player.stats.resFire = 0.5;
  fire.player.stats.armor = 500; // huge armor must not reduce fire damage
  assert.equal(RPG.sim.hurtPlayer(fire, 10, { type: 'fire' }).dealt, 5);

  const phys = mkWorld();
  phys.player.stats.armor = 500;
  phys.player.stats.resFire = 0.5; // resistance must not reduce phys damage
  const armored = RPG.sim.hurtPlayer(phys, 10, { type: 'phys' }).dealt;
  assert.ok(armored < 10 * 0.5, `armor 500 should mitigate most phys damage, got ${armored}`);

  const bypass = mkWorld();
  bypass.player.stats.armor = 500;
  assert.equal(RPG.sim.hurtPlayer(bypass, 10, { type: 'phys', ignoreArmor: true }).dealt, 10);
});

test('i-frames block a second hit until the window expires', () => {
  const w = mkWorld();
  const first = RPG.sim.hurtPlayer(w, 10, { type: 'phys', ignoreArmor: true });
  assert.equal(first.blocked, null);
  const hpAfter = w.player.hp;
  const second = RPG.sim.hurtPlayer(w, 10, { type: 'phys', ignoreArmor: true });
  assert.equal(second.blocked, 'iframe');
  assert.equal(w.player.hp, hpAfter, 'no damage while in i-frames');
  run(w, 0.5); // longer than IFRAME_SECONDS (0.4)
  assert.equal(RPG.sim.hurtPlayer(w, 10, { type: 'phys', ignoreArmor: true }).blocked, null);
});

test('invuln (dash) blocks damage without consuming it', () => {
  const w = mkWorld();
  combat.applyStatus(w.player, { id: 'invuln', duration: 0.15 });
  const r = RPG.sim.hurtPlayer(w, 30, { type: 'phys', ignoreArmor: true });
  assert.equal(r.blocked, 'invuln');
  assert.equal(w.player.hp, 100);
});

test('a shield absorbs damage before armor and is removed when spent', () => {
  const w = mkWorld();
  combat.applyStatus(w.player, { id: 'shield', duration: 6, strength: 4, stacks: 'max' });
  const r = RPG.sim.hurtPlayer(w, 10, { type: 'phys', ignoreArmor: true });
  assert.equal(r.absorbed, 4);
  assert.equal(r.dealt, 6);
  assert.equal(w.player.hp, 94);
  assert.equal(w.player.status.shield, undefined, 'shield spent');
});

test('healOnExpire heals when the shield runs out on a hit', () => {
  const w = mkWorld();
  w.player.hp = 50;
  combat.applyStatus(w.player, { id: 'shield', duration: 6, strength: 5, stacks: 'max' }).healOnExpire = 15;
  RPG.sim.hurtPlayer(w, 5, { type: 'phys', ignoreArmor: true }); // fully absorbed, shield gone
  assert.equal(w.player.status.shield, undefined);
  assert.equal(w.player.hp, 65);
});

test('healOnExpire heals when the shield times out', () => {
  const w = mkWorld();
  w.player.hp = 50;
  combat.applyStatus(w.player, { id: 'shield', duration: 0.5, strength: 99, stacks: 'max' }).healOnExpire = 15;
  run(w, 0.6);
  assert.equal(w.player.status.shield, undefined);
  assert.equal(w.player.hp, 65);
});

test('reflect returns a share of the damage that got through to the monster, only while shielded', () => {
  const w = mkWorld();
  const m = mon(w, 'husk-grunt', 340, 320);
  const before = m.hp;
  combat.applyStatus(w.player, { id: 'shield', duration: 6, strength: 4, stacks: 'max' });
  combat.applyStatus(w.player, { id: 'reflect', duration: 6, strength: 0.5, stacks: 'refresh' });
  // Shield soaks 4 of 10; 6 gets through; 50% of that (3) comes back at the attacker.
  const r = RPG.sim.hurtPlayer(w, 10, { type: 'phys', ignoreArmor: true, source: m });
  assert.equal(r.dealt, 6);
  assert.ok(Math.abs(r.reflected - 3) < 1e-9);
  assert.ok(Math.abs(before - m.hp - 3) < 1e-9, `monster lost ${before - m.hp}`);

  const later = mkWorld();
  const m2 = mon(later, 'husk-grunt', 340, 320);
  const before2 = m2.hp;
  RPG.sim.hurtPlayer(later, 10, { type: 'phys', ignoreArmor: true, source: m2 });
  assert.equal(m2.hp, before2, 'no shield, no reflect (thorns is 0 by default)');
});

test('thorns reflect flat damage to a monster source', () => {
  const w = mkWorld();
  w.player.stats.thorns = 3;
  const m = mon(w, 'husk-grunt', 340, 320);
  const before = m.hp;
  RPG.sim.hurtPlayer(w, 10, { type: 'phys', ignoreArmor: true, source: m });
  assert.ok(Math.abs(before - m.hp - 3) < 1e-9, `monster lost ${before - m.hp}`);
});

test('an enemy projectile flies, hits the player through hurtPlayer, emits HIT and is consumed', () => {
  const w = mkWorld();
  hits.length = 0;
  const shot = enemyShot(w, 280, 320, 300, 0, 12, null);
  run(w, 0.2);
  assert.equal(w.enemyProjectiles.length, 0, 'consumed on contact');
  assert.ok(w.player.hp < 100, 'player took damage');
  assert.ok(hits.some((h) => h.targetId === w.player.id && h.team === 0), 'HIT published for the player');
  assert.ok(shot.x > 280, 'the shot moved before it hit');
});

test('an enemy projectile that hits a wall is removed without damage', () => {
  const w = mkWorld();
  // Room interior starts at x = 32; from x = 40 a leftward shot reaches the west wall within 0.1 s, away from the player.
  enemyShot(w, 40, 320, -600, 0, 12, null);
  run(w, 0.1);
  assert.equal(w.enemyProjectiles.length, 0);
  assert.equal(w.player.hp, 100);
});

test('a player fireball damages the monster in its path and emits HIT for it', () => {
  const w = mkWorld();
  hits.length = 0;
  const m = mon(w, 'husk-grunt', 420, 320);
  const before = m.hp;
  const aim = { x: 520, y: 320 };
  const r = RPG.world.skillEffects.execute(w, w.player, RPG.data.skills.fireball, 1, aim,
    { damageMult: 1, count: 1, speed: 500, radius: 6, pierce: 0, aoeRadius: 0 });
  assert.equal(r.ok, true);
  run(w, 0.6);
  assert.ok(m.hp < before, 'monster took fire damage');
  const h = hits.find((x) => x.targetId === m.id);
  assert.ok(h, 'HIT published for the monster');
  assert.equal(h.team, 1);
  assert.ok(h.dealt > 0);
});

test('burn ticks damage in steps and expires; slow expires on time', () => {
  const w = mkWorld();
  const m = mon(w, 'husk-grunt', 500, 500);
  combat.applyStatus(m, { id: 'burn', duration: 1, dps: 10, stacks: 'refresh' });
  combat.applyStatus(m, { id: 'slow', duration: 0.3, strength: 0.5, stacks: 'max' });
  assert.equal(combat.moveSpeedFactor(m), 0.5);
  const before = m.hp;
  run(w, 0.4);
  assert.equal(m.status.slow, undefined, 'slow expired after 0.3 s');
  run(w, 0.7);
  assert.equal(m.status.burn, undefined, 'burn expired after its duration');
  const drop = before - m.hp;
  const tick = 10 * combat.STATUS_TICK;
  assert.ok(drop > 0 && Math.abs(drop / tick - Math.round(drop / tick)) < 1e-9, `drop ${drop} is whole ticks of ${tick}`);
});

test('player statuses tick through the snapshot stats view (burn on the player)', () => {
  const w = mkWorld();
  combat.applyStatus(w.player, { id: 'burn', duration: 1, dps: 10, stacks: 'refresh' });
  run(w, 1.1);
  assert.ok(w.player.hp < 100, 'burn damaged the player');
  assert.equal(w.player.status.burn, undefined);
});

test('a monster zone hits the player once after its delay, and only inside its radius', () => {
  const w = mkWorld();
  hits.length = 0;
  RPG.world.spawnEffect(w, { type: 'aoe', x: 320, y: 320, r: 40, duration: 0.5, delayDamageAt: 0.2,
    damage: { amount: 20, type: 'phys', ignoreArmor: true } });
  run(w, 0.4);
  assert.equal(w.player.hp, 80, 'one hit of 20');
  run(w, 0.5);
  assert.equal(w.player.hp, 80, 'no second hit');
  assert.equal(hits.filter((h) => h.targetId === w.player.id).length, 1);

  const far = mkWorld();
  RPG.world.spawnEffect(far, { type: 'aoe', x: 60, y: 60, r: 10, duration: 0.5, delayDamageAt: 0, damage: { amount: 20, type: 'phys' } });
  run(far, 0.2);
  assert.equal(far.player.hp, 100, 'outside the radius');
});

test('a monster zone with tickEvery keeps hitting while the player stands in it, paced by i-frames', () => {
  const w = mkWorld();
  RPG.world.spawnEffect(w, { type: 'zone', x: 320, y: 320, r: 40, duration: 1.2, delayDamageAt: 0, tickEvery: 0.5,
    damage: { amount: 5, type: 'phys', ignoreArmor: true } });
  run(w, 1.1);
  // Ticks at 0, 0.5 and 1.0 s. Each is past the 0.4 s i-frame window of the previous hit, so all three land.
  assert.equal(w.player.hp, 100 - 5 * 3);
  // Faster ticks (0.25 s) fall inside the i-frame window and are blocked, so they do not stack.
  const fast = mkWorld();
  RPG.world.spawnEffect(fast, { type: 'zone', x: 320, y: 320, r: 40, duration: 1, delayDamageAt: 0, tickEvery: 0.25,
    damage: { amount: 5, type: 'phys', ignoreArmor: true } });
  run(fast, 0.9);
  assert.equal(fast.player.hp, 100 - 5 * 2, 'hits at 0 and 0.5 s only');
});

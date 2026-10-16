'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/pool.js', 'core/entities.js', 'core/state.js',
  'data/skills.js', 'data/monsters.js', 'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/world.js',
  'world/combat.js', 'world/skills.js', 'world/skill-effects.js'
]);
const sk = RPG.world.skills;
const fx = RPG.world.skillEffects;
const ACTIVE = Object.keys(RPG.data.skills).filter((id) => RPG.data.skills[id].type === 'active');

function mkWorld(rows) {
  const w = RPG.core.state.createWorldState({ seed: 11 });
  if (rows) w.area = { map: RPG.world.tilemap.fromRows(rows, 32) };
  const p = RPG.core.entities.baseEntity('player', 100, 100, 10, 0);
  p.id = w.nextEntityId++;
  p.level = 10; p.resource = 200; p.gold = 0; p.hp = 50; p.maxHp = 100;
  p.stats = new RPG.world.StatBlock(); p.cooldowns = {};
  p.skills = sk.createSkillState(0);
  w.player = p;
  return w;
}

/** Learns `id` (bypassing the point economy) and puts it in slot 0, then casts it toward `aim`. */
function cast(w, id, aim, rank = 1) {
  const p = w.player;
  p.skills.learned[id] = rank;
  p.skills.bar[0] = id;
  return sk.tryCastSkill(w, p, 0, aim);
}
const mon = (w, x, y) => { const m = RPG.world.spawnMonster(w, 'husk-grunt', x, y); m.stats = new RPG.world.StatBlock({ maxHp: m.maxHp }); return m; };
const tick = (w, secs, dt = 0.05) => { for (let t = 0; t < secs - 1e-9; t += dt) { fx.updateProjectiles(w, dt); fx.updateEffects(w, dt); RPG.world.combat.tickStatuses(w.monsters[0] || {}, 0); } };

test('every active skill has an implemented effect and executes', () => {
  assert.ok(ACTIVE.length >= 7);
  for (const id of ACTIVE) {
    assert.ok(typeof RPG.data.skills[id].effect === 'string' && RPG.data.skills[id].effect.length > 0, `${id} -> ${RPG.data.skills[id].effect}`);
    const w = mkWorld();
    const r = cast(w, id, { x: 200, y: 100 });
    assert.equal(r.ok, true, id);
    assert.equal(r.applied.ok, true, id);
    assert.equal(r.applied.skillId, id);
  }
  // passives need no execute
  assert.equal(fx.execute(mkWorld(), mkWorld().player, RPG.data.skills.vitality, 1, null).ok, false);
});

test('fireball: projectile flies, explodes in an area, damages and burns nearby monsters', () => {
  const w = mkWorld();
  const near = mon(w, 300, 100), side = mon(w, 300, 140), far = mon(w, 300, 400);
  const r = cast(w, 'fireball', { x: 400, y: 100 });
  assert.equal(w.projectiles.length, 1);
  assert.equal(w.projectiles[0].skillId, 'fireball');
  assert.ok(w.projectiles[0].vx > 0);
  tick(w, 0.6);
  assert.equal(w.projectiles.length, 0, 'consumed on impact');
  assert.ok(near.hp < near.maxHp, 'direct target damaged');
  assert.ok(side.hp < side.maxHp, 'splash damaged neighbour inside aoeRadius');
  assert.equal(far.hp, far.maxHp);
  assert.ok(w.effects.some((e) => e.kind === 'aoe' && e.skillId === 'fireball'));
  assert.ok(r.applied.projectiles.length === 1);
});

test('fireball: stops at a wall and multiple-projectiles upgrade fans out', () => {
  const w = mkWorld(['#########', '#.......#', '#.......#', '#########']);
  w.player.x = 60; w.player.y = 60;
  const m = mon(w, 64, 100); // beside the wall, outside fireball path
  cast(w, 'fireball', { x: 260, y: 60 });
  tick(w, 1.5);
  assert.equal(w.projectiles.length, 0, 'removed at the wall');
  assert.equal(m.hp, m.maxHp);
  const w2 = mkWorld();
  w2.player.skills.upgrades.fireball = { 'multiple-projectiles': 2 };
  cast(w2, 'fireball', { x: 400, y: 100 }, 3);
  assert.equal(w2.projectiles.length, 3);
});

test('whirlwind: ticks damage to monsters in radius over its duration, not outside', () => {
  const w = mkWorld();
  const inside = mon(w, 140, 100), outside = mon(w, 600, 100);
  const r = cast(w, 'whirlwind', null);
  assert.equal(r.applied.effect, 'whirl');
  const afterCast = inside.hp;
  assert.ok(afterCast < inside.maxHp, 'first tick lands on cast');
  tick(w, 1.0);
  assert.ok(inside.hp < afterCast, 'later ticks keep hitting');
  assert.equal(outside.hp, outside.maxHp);
  tick(w, 2.0);
  assert.equal(w.effects.filter((e) => e.kind === 'whirl').length, 0, 'zone expires');
});

test('ground slam: damages and stuns monsters at the aimed spot', () => {
  const w = mkWorld();
  const hit = mon(w, 230, 100), safe = mon(w, 100, 400);
  cast(w, 'ground-slam', { x: 220, y: 100 });
  assert.ok(hit.hp < hit.maxHp);
  assert.ok(hit.status.stun && hit.status.stun.remaining > 0, 'stunned');
  assert.equal(safe.hp, safe.maxHp);
  assert.equal(safe.status && safe.status.stun, undefined);
});

test('frost nova: damages and slows everything around the caster', () => {
  const w = mkWorld();
  const a = mon(w, 160, 100), far = mon(w, 500, 100);
  cast(w, 'frost-nova', null);
  assert.ok(a.hp < a.maxHp);
  assert.ok(a.status.slow && a.status.slow.strength > 0 && a.status.slow.remaining > 0);
  assert.ok(RPG.world.combat.moveSpeedFactor(a) < 1);
  assert.equal(far.hp, far.maxHp);
  assert.equal(far.status && far.status.slow, undefined);
});

test('shield: applies an absorb status that soaks damage until spent', () => {
  const w = mkWorld();
  cast(w, 'shield', null);
  const st = w.player.status.shield;
  assert.ok(st && st.strength === 40 && st.remaining > 0);
  assert.equal(fx.absorbDamage(w.player, 25), 0);
  assert.equal(fx.absorbDamage(w.player, 25), 10);
  assert.equal(w.player.status.shield, undefined);
  assert.equal(fx.absorbDamage(w.player, 5), 5);
});

test('mend: heals the player over time, capped at max HP', () => {
  const w = mkWorld();
  w.player.hp = 20;
  cast(w, 'mend', null);
  assert.equal(w.player.hp, 20, 'heal-over-time, not instant');
  tick(w, 2);
  assert.ok(w.player.hp > 20 && w.player.hp < 80);
  tick(w, 4);
  assert.ok(Math.abs(w.player.hp - 80) < 1e-6, 'total heal of 60 delivered');
  w.player.hp = 99;
  cast(w, 'mend', null); // cooldown blocks a recast, so apply directly
  fx.execute(w, w.player, RPG.data.skills.mend, 1, null);
  tick(w, 6);
  assert.equal(w.player.hp, 100);
});

test('blink strike: moves the player toward the aim and hits monsters on the path', () => {
  const w = mkWorld();
  const onPath = mon(w, 180, 100), off = mon(w, 180, 300);
  const r = cast(w, 'blink-strike', { x: 500, y: 100 });
  assert.ok(Math.abs(w.player.x - 260) < 1e-6 && Math.abs(w.player.y - 100) < 1e-6, 'moved exactly distance 160');
  assert.equal(r.applied.hitWall, false);
  assert.ok(onPath.hp < onPath.maxHp);
  assert.equal(off.hp, off.maxHp);
});

test('blink strike respects collision: stops at a wall and never ends inside a solid tile', () => {
  const w = mkWorld(['##########', '#........#', '#...#....#', '#........#', '##########']);
  w.player.x = 48; w.player.y = 80; // row 2 has a wall at tile x=4 (px 128..160)
  const r = cast(w, 'blink-strike', { x: 400, y: 80 }, 5); // distance 200 > room to the wall
  assert.equal(r.applied.hitWall, true);
  assert.ok(w.player.x < 128 - w.player.r + 1e-6, 'stopped before the wall, x=' + w.player.x);
  assert.equal(RPG.world.collision.circleOverlapsSolid(w.area.map, w.player.x, w.player.y, w.player.r - 0.5), false);
  assert.ok(r.applied.moved < 200);
});

test('tryCastSkill without an executor behaves as before; unsupported casts do not execute', () => {
  const w = mkWorld();
  sk.setExecutor(null);
  const r = cast(w, 'fireball', { x: 300, y: 100 });
  assert.equal(r.ok, true);
  assert.equal('applied' in r, false);
  assert.equal(w.projectiles.length, 0);
  sk.setExecutor(fx.execute);
  assert.equal(fx.execute(w, w.player, { id: 'x', type: 'active', effect: 'teleport' }, 1, null, {}).reason, 'unsupported-effect');
});

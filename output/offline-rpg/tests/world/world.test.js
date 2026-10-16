'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/pool.js', 'core/entities.js', 'core/state.js',
  'data/monsters.js', 'world/stats.js', 'world/world.js', 'world/ai.js'
]);
const W = RPG.world;
const mk = (seed = 7) => RPG.core.state.createWorldState({ seed });
const defIds = () => Object.keys(RPG.data.monsters);

test('every shipped monster definition spawns with sane fields', () => {
  const w = mk();
  assert.ok(defIds().length >= 7);
  for (const id of defIds()) {
    const def = RPG.data.monsters[id];
    const m = W.spawnMonster(w, id, 10, 20);
    assert.equal(m.kind, 'monster');
    assert.equal(m.defId, id);
    assert.equal(m.team, 1);
    assert.equal(m.hp, def.baseStats.hp);
    assert.equal(m.maxHp, m.hp);
    assert.equal(m.r, def.radius);
    assert.equal(m.stats.get('maxHp'), m.maxHp);
    assert.equal(m.home.x, 10); assert.equal(m.home.y, 20);
    assert.equal(m.dead, false);
  }
  assert.equal(w.monsters.length, defIds().length);
});

test('spawned monsters can think() via ai.js', () => {
  const w = mk();
  const m = W.spawnMonster(w, 'husk-grunt', 0, 0);
  const it = RPG.world.ai.think(m, { target: { x: 50, y: 0, dead: false } }, w.rng, 0.05);
  assert.ok(it && Array.isArray(it.actions));
});

test('level scaling, elite and custom scaleFn', () => {
  const w = mk();
  const base = W.spawnMonster(w, 'husk-grunt', 0, 0, { level: 1 });
  const lv5 = W.spawnMonster(w, 'husk-grunt', 0, 0, { level: 5 });
  assert.ok(lv5.maxHp > base.maxHp && lv5.level === 5);
  const elite = W.spawnMonster(w, 'husk-grunt', 0, 0, { elite: true, affixes: ['swift'] });
  assert.ok(elite.maxHp > base.maxHp && elite.elite && elite.affixes[0] === 'swift');
  let seen = null;
  const c = W.spawnMonster(w, 'husk-grunt', 0, 0, { level: 3, scaleFn: (d, l) => { seen = [d.id, l]; return { hp: 999, damage: 1, speed: 10, armor: 2 }; } });
  assert.deepEqual([...seen], ["husk-grunt", 3]);
  assert.equal(c.maxHp, 999);
  assert.throws(() => W.spawnMonster(w, 'nope', 0, 0), /unknown monster/);
});

test('ids are unique, increasing and deterministic for the same call order', () => {
  const run = () => {
    const w = mk(42);
    const ids = [];
    for (const id of defIds()) ids.push(W.spawnMonster(w, id, 0, 0).id);
    ids.push(W.spawnProjectile(w, { x: 0, y: 0, angle: 0, speed: 10, team: 0 }).id);
    ids.push(W.spawnEffect(w, { type: 'ring', x: 0, y: 0, r: 5, duration: 1 }).id);
    ids.push(W.spawnItemDrop(w, { id: 'x' }, 1, 1).id);
    ids.push(W.nextId(w));
    return ids;
  };
  const a = run(), b = run();
  assert.deepEqual(a, b);
  assert.equal(new Set(a).size, a.length);
  for (let i = 1; i < a.length; i++) assert.ok(a[i] > a[i - 1]);
});

test('nextId continues from a restored counter', () => {
  const w = mk();
  w.nextEntityId = 500;
  assert.equal(W.spawnMonster(w, 'husk-grunt', 0, 0).id, 500);
  assert.equal(W.nextId(w), 501);
});

test('projectile objects are reused after release, with fresh ids and state', () => {
  const w = mk();
  const p1 = W.spawnProjectile(w, { x: 1, y: 2, angle: 0, speed: 100, team: 0, pierce: 2, damage: { amount: 3 } });
  assert.equal(p1.vx, 100);
  assert.equal(p1.pierce, 2);
  const id1 = p1.id;
  p1.dead = true;
  assert.equal(W.despawnDead(w), 1);
  assert.equal(w.projectiles.length, 0);
  assert.equal(W.poolStats(w).projectile.free, 1);
  const p2 = W.spawnProjectile(w, { x: 5, y: 5, angle: Math.PI / 2, speed: 10, team: 1 });
  assert.equal(p2, p1, 'same object recycled');
  assert.notEqual(p2.id, id1);
  assert.equal(p2.pierce, 0);
  assert.equal(p2.dead, false);
  assert.ok(Math.abs(p2.vx) < 1e-9 && p2.vy === 10);
  assert.equal(W.poolStats(w).projectile.free, 0);
});

test('effects are pooled and recycled too', () => {
  const w = mk();
  const e1 = W.spawnEffect(w, { type: 'aoe', x: 0, y: 0, r: 30, duration: 0.5 });
  e1.dead = true;
  W.despawnDead(w);
  const e2 = W.spawnEffect(w, { type: 'text', x: 1, y: 1, text: '5' });
  assert.equal(e2, e1);
  assert.equal(e2.type, 'text');
  assert.equal(e2.dead, false);
});

test('despawnDead removes dead monsters without leaking pool slots', () => {
  const w = mk();
  const ms = [];
  for (let i = 0; i < 20; i++) ms.push(W.spawnMonster(w, defIds()[i % defIds().length], i, i));
  ms.filter((_, i) => i % 2 === 0).forEach((m) => { m.dead = true; });
  assert.equal(W.despawnDead(w), 10);
  assert.equal(w.monsters.length, 10);
  assert.ok(w.monsters.every((m) => !m.dead));
  let st = W.poolStats(w).monster;
  assert.equal(st.active, 10);
  assert.equal(st.free, 10);
  assert.equal(W.despawnDead(w), 0, 'second sweep is a no-op');
  // churn: pool never grows past peak
  for (let round = 0; round < 50; round++) {
    const batch = [];
    for (let i = 0; i < 10; i++) batch.push(W.spawnMonster(w, 'husk-grunt', 0, 0));
    batch.forEach((m) => { m.dead = true; });
    W.despawnDead(w);
  }
  st = W.poolStats(w).monster;
  assert.equal(st.active, 10);
  assert.equal(st.active + st.free, 20);
  const ids = w.monsters.map((m) => m.id);
  assert.equal(new Set(ids).size, ids.length);
});

test('drops and iteration helpers', () => {
  const w = mk();
  w.player = { id: W.nextId(w), kind: 'player' };
  const a = W.spawnMonster(w, 'husk-grunt', 0, 0), b = W.spawnMonster(w, 'skitter', 0, 0);
  const d = W.spawnItemDrop(w, { defId: 'potion' }, 3, 4);
  assert.equal(d.kind, 'drop');
  assert.equal(d.item.defId, 'potion');
  b.dead = true;
  assert.deepEqual([...W.livingMonsters(w).map((m) => m.id)], [a.id]);
  const seen = [];
  W.forEachMonster(w, (m) => seen.push(m.id));
  assert.deepEqual([...seen], [a.id]);
  let n = 0;
  W.forEachEntity(w, () => n++);
  assert.equal(n, 4);
  assert.equal(W.findEntity(w, d.id), d);
  assert.equal(W.findEntity(w, 99999), null);
  d.dead = true;
  assert.equal(W.despawnDead(w), 2);
  assert.equal(w.drops.length, 0);
});

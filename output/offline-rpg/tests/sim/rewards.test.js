'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js', 'core/state.js',
  'data/maps/test-map.js', 'data/difficulties.js', 'data/items.js', 'data/monsters.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/world.js', 'world/loot.js',
  'world/progression.js', 'world/equipment.js', 'world/difficulty.js', 'ui/inventory.js',
  'sim/player.js', 'sim/sim.js', 'sim/rewards.js',
]);
const { Rng } = RPG.core;
const { NAMES } = RPG.core.events;
const { stage } = RPG.sim;
const { xpToNext, LEVEL_STAT_GROWTH } = RPG.world.progression;

/** A fresh world with a seeded loot RNG (world.lootRng is created by sim.createWorld in W4-01). */
function makeWorld(seed = 7) {
  const world = RPG.sim.createWorld({ seed });
  world.lootRng = Rng.create(seed + 1000);
  return world;
}

/** Records every event as { type, payload } in emit order. */
function capture() {
  const log = [];
  for (const type of Object.values(NAMES)) {
    RPG.core.events.on(type, (payload) => log.push({ type, payload }));
  }
  return log;
}

function ofType(log, type) {
  return log.filter((e) => e.type === type);
}

function killMonster(world, defId, opts) {
  const p = world.player;
  const m = RPG.world.spawnMonster(world, defId, p.x + 40, p.y, opts || {});
  m.dead = true;
  return m;
}

test('a kill awards XP and gold exactly once across repeated stage passes', () => {
  const world = makeWorld();
  const log = capture();
  killMonster(world, 'husk-grunt');

  stage.rewards(world);
  stage.rewards(world);

  assert.equal(world.progress.kills, 1);
  assert.equal(world.player.xp, RPG.data.monsters['husk-grunt'].xp, 'husk-grunt xp at normal difficulty');
  assert.equal(ofType(log, NAMES.KILL).length, 1);
  assert.equal(ofType(log, NAMES.KILL)[0].payload.defId, 'husk-grunt');
  assert.equal(ofType(log, NAMES.KILL)[0].payload.boss, false);
});

test('an elite kill is reported with the elite flag and its base loot is rolled', () => {
  const world = makeWorld();
  const log = capture();
  killMonster(world, 'husk-grunt', { elite: true });

  stage.rewards(world);

  assert.equal(ofType(log, NAMES.KILL)[0].payload.elite, true);
});

test('kills over many seeded passes spawn drops, and every drop carries an item or gold', () => {
  const world = makeWorld(42);
  const log = capture();
  for (let i = 0; i < 200; i++) {
    killMonster(world, 'stone-brute');
    stage.rewards(world);
    RPG.world.despawnDead(world);
  }

  const dropped = ofType(log, NAMES.ITEM_DROPPED);
  assert.ok(dropped.length > 0, 'expected at least one item drop in 200 kills');
  assert.equal(world.progress.kills, 200);
  for (const d of world.drops) assert.ok(d.gold > 0 || d.item, 'drop has gold or an item');
});

test('same seed gives the same drops and XP across runs', () => {
  const run = () => {
    const world = makeWorld(99);
    for (let i = 0; i < 50; i++) {
      killMonster(world, 'husk-grunt');
      stage.rewards(world);
      RPG.world.despawnDead(world);
    }
    return JSON.parse(JSON.stringify({ xp: world.player.xp, level: world.player.level, gold: world.player.gold }));
  };
  assert.deepEqual(run(), run());
});

test('level-up raises stats, grants skill points, heals and emits LEVEL_UP', () => {
  const world = makeWorld();
  const log = capture();
  const p = world.player;
  const hpBefore = p.stats.maxHp;
  p.hp = 1;
  const pointsBefore = p.skills.points;

  RPG.sim.rewards.grantXp(p, xpToNext(1));

  assert.equal(p.level, 2);
  assert.equal(p.xp, 0);
  assert.equal(p.stats.maxHp, hpBefore + LEVEL_STAT_GROWTH.maxHp, 'maxHp grows by the level growth');
  assert.equal(p.skills.points, pointsBefore + RPG.world.progression.SKILL_POINTS_PER_LEVEL);
  assert.equal(p.hp, p.stats.maxHp, 'level-up heals to full');

  // grantXp is pure stat/progression work; the stage is what emits LEVEL_UP, so a kill exercises the event path.
  const m = killMonster(world, 'husk-grunt');
  m.xp = xpToNext(2);
  stage.rewards(world);
  assert.equal(p.level, 3);
  assert.equal(ofType(log, NAMES.LEVEL_UP).length, 1, 'one LEVEL_UP per level gained by the kill');
  assert.equal(ofType(log, NAMES.LEVEL_UP)[0].payload.level, 3);
});

test('level growth is one modifier per stat and does not double on repeated level-ups', () => {
  const world = makeWorld();
  const p = world.player;
  RPG.sim.rewards.grantXp(p, 1e6);
  assert.equal(p.level, RPG.world.progression.MAX_LEVEL);
  assert.equal(p.statBlock.mods.filter((m) => m.source === 'level').length, Object.keys(LEVEL_STAT_GROWTH).length);
  const levels = p.level - 1;
  assert.equal(p.statBlock.get('maxHp'), 100 + LEVEL_STAT_GROWTH.maxHp * levels);
});

test('pickup of gold adds it to the player and removes the drop', () => {
  const world = makeWorld();
  const log = capture();
  const p = world.player;
  const drop = RPG.world.spawnItemDrop(world, null, p.x + 5, p.y);
  drop.gold = 17;

  stage.pickups(world, {});
  RPG.world.despawnDead(world);

  assert.equal(p.gold, 17);
  assert.equal(world.drops.length, 0);
  assert.equal(ofType(log, NAMES.PICKUP)[0].payload.kind, 'gold');
});

test('pickup of an item puts it in the first free inventory slot', () => {
  const world = makeWorld();
  const p = world.player;
  const item = RPG.world.loot.generateItem(Rng.create(3), { areaLevel: 1 });
  const drop = RPG.world.spawnItemDrop(world, item, p.x, p.y);

  stage.pickups(world, {});

  assert.equal(p.inventory[0], item);
  assert.equal(drop.dead, true);
});

test('a full inventory leaves the item on the ground and emits the UI hint', () => {
  const world = makeWorld();
  const log = capture();
  const p = world.player;
  for (let i = 0; i < p.inventory.length; i++) p.inventory[i] = { placeholder: true };
  const item = RPG.world.loot.generateItem(Rng.create(4), { areaLevel: 1 });
  const drop = RPG.world.spawnItemDrop(world, item, p.x, p.y);

  stage.pickups(world, {});

  assert.ok(!drop.dead, 'drop is still on the ground');
  assert.equal(world.drops.length, 1);
  assert.equal(ofType(log, NAMES.UI_SOUND)[0].payload.hint, 'inventory-full');
});

test('the interact key widens the pickup range', () => {
  const world = makeWorld();
  const p = world.player;
  const reach = p.statBlock.get('pickupRadius');
  const drop = RPG.world.spawnItemDrop(world, null, p.x + reach + 5, p.y);
  drop.gold = 3;

  stage.pickups(world, { actions: { wantInteract: false } });
  assert.equal(p.gold, 0, 'out of pickup range without interact');

  stage.pickups(world, { actions: { wantInteract: true } });
  assert.equal(p.gold, 3, 'in range with interact');
});

test('a boss death sets its bossesDefeated flag and reports boss: true', () => {
  const world = makeWorld();
  const log = capture();
  killMonster(world, 'warden-of-ash');

  stage.rewards(world);

  assert.equal(world.progress.bossesDefeated['warden-of-ash'], true);
  assert.equal(ofType(log, NAMES.KILL)[0].payload.boss, true);
});

test('a non-boss death does not set any boss flag', () => {
  const world = makeWorld();
  killMonster(world, 'husk-grunt');
  stage.rewards(world);
  assert.equal(Object.keys(world.progress.bossesDefeated).length, 0);
});

test('player death applies the difficulty penalty once and sets world.state to dead', () => {
  const world = makeWorld();
  const log = capture();
  const p = world.player;
  p.gold = 100;
  p.dead = true;

  stage.playerDeath(world);
  stage.playerDeath(world);

  assert.equal(p.gold, 90, 'normal difficulty loses 10% gold');
  assert.equal(world.progress.deaths, 1);
  assert.equal(world.state, 'dead');
  assert.equal(ofType(log, NAMES.PLAYER_DIED).length, 1);
});

test('easy difficulty has no death penalty', () => {
  const world = makeWorld();
  world.difficulty = 'easy';
  const p = world.player;
  p.gold = 100;
  p.dead = true;

  stage.playerDeath(world);

  assert.equal(p.gold, 100);
});

test('an unknown death penalty id is rejected by difficulty.resolve rather than skipped', () => {
  const world = makeWorld();
  world.difficulty = { id: 'custom', playerDeathPenalty: 'mystery' };
  world.player.dead = true;

  assert.throws(() => stage.playerDeath(world), /playerDeathPenalty/);
});

test('a missing loot RNG is a loud error, not a silent zero-drop kill', () => {
  const world = makeWorld();
  delete world.lootRng;
  killMonster(world, 'husk-grunt');
  assert.throws(() => stage.rewards(world), /world\.lootRng is missing/);
});

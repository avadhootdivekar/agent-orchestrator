'use strict';
// createWorld + step on a real generated area: full stage order, starting weapons, kill rewards (INTEGRATION-PLAN §2).

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine } = require('../integration/helpers.js');

const DT = 1 / 60;
const idle = () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });

test('step runs every plan stage in ORDER, once per tick', async () => {
  const RPG = await loadEngine();
  const w = RPG.sim.createWorld('forest');
  const calls = [];
  const saved = {};
  // Stages 1-4 (input, player, weapons, skills) are one function, stage.player (sim/player.js), so the three
  // sub-stage names in ORDER have no function of their own and are skipped by step.
  const merged = ['input', 'weapons', 'skills'];
  for (const name of merged) assert.equal(RPG.sim.stage[name], undefined);
  const expected = Array.from(RPG.sim.ORDER).filter((n) => !merged.includes(n));
  for (const name of expected) {
    assert.equal(typeof RPG.sim.stage[name], 'function', 'stage not registered: ' + name);
    saved[name] = RPG.sim.stage[name];
    RPG.sim.stage[name] = (world, ctx) => { calls.push(name); saved[name](world, ctx); };
  }
  RPG.sim.step(w, idle(), DT);
  assert.deepEqual(calls, expected);
  const idx = (n) => RPG.sim.ORDER.indexOf(n);
  assert.ok(idx('spatial') < idx('monsters') && idx('monsters') < idx('movement'));
  assert.ok(idx('resolve') < idx('rewards') && idx('rewards') < idx('pickups') && idx('pickups') < idx('playerDeath'));
  assert.equal(RPG.sim.ORDER[RPG.sim.ORDER.length - 3], 'cleanup');
  assert.equal(RPG.sim.ORDER[RPG.sim.ORDER.length - 2], 'areas');
  assert.equal(w.tick, 1);
});

test('createWorld(forest): populated area, loot rng, difficulty, starting weapons', async () => {
  const RPG = await loadEngine();
  const w = RPG.sim.createWorld({ seed: 7, areaId: 'forest', difficulty: 'hard' });
  assert.equal(w.area.id, 'forest');
  assert.ok(w.monsters.length > 0);
  assert.ok(w.area.level >= 1);
  assert.equal(w.difficulty.id, 'hard');
  assert.ok(w.lootRng && typeof w.lootRng.getState === 'function');
  assert.equal(w.player.weaponSlots.filter(Boolean).length, 3);
  const defs = w.player.weaponSlots.map((s) => RPG.world.weapons.currentWeapon({ weaponSlots: [s], activeWeapon: 0 }).def.id);
  assert.deepEqual(JSON.parse(JSON.stringify(defs)), ['sword', 'hunting-bow', 'arc-staff']);
  for (const m of w.monsters) assert.equal(typeof m.xp, 'number');
});

test('600 ticks on a forest world do not throw', async () => {
  const RPG = await loadEngine();
  const w = RPG.sim.createWorld({ seed: 3, areaId: 'forest' });
  for (let i = 0; i < 600; i++) RPG.sim.step(w, idle(), DT);
  assert.equal(w.tick, 600);
});

test('fresh player attacks, a kill awards XP, drops come from lootRng and the slot is recorded cleared', async () => {
  const RPG = await loadEngine();
  const w = RPG.sim.createWorld({ seed: 11, areaId: 'forest' });
  const p = w.player;
  const m = w.monsters.find((x) => !x.dead);
  m.x = p.x + 30; m.y = p.y; m.vx = 0; m.vy = 0;
  const slot = w.area.slotOf[m.id];
  const lootBefore = JSON.stringify(w.lootRng.getState());
  const xp0 = p.xp, lvl0 = p.level;
  const targetId = m.id, tx = m, hp0 = m.hp;
  let damaged = false;
  const kills = [];
  RPG.core.events.on(RPG.core.events.NAMES.KILL, (e) => kills.push(e));
  // The monster object is pooled and reset once dead, so progress is read inside the loop and from events.
  for (let i = 0; i < 1800 && kills.length === 0; i++) {
    if (tx.id === targetId && !tx.dead) { tx.x = p.x + 30; tx.y = p.y; if (tx.hp < hp0) damaged = true; }
    RPG.sim.step(w, { move: { x: 0, y: 0 }, aim: { x: p.x + 30, y: p.y }, attack: true, skills: [], dash: false, toggles: {} }, DT);
  }
  assert.ok(damaged, 'the fresh player never damaged the monster');
  assert.ok(kills.length >= 1, 'no monster was killed');
  assert.ok(kills[0].xp > 0);
  assert.ok(p.xp > xp0 || p.level > lvl0, 'no XP awarded');
  assert.notEqual(JSON.stringify(w.lootRng.getState()), lootBefore, 'loot did not consume world.lootRng');
  assert.ok(w.progress.kills >= 1);
  if (slot !== undefined) assert.equal(w.area.slotOf[targetId], undefined, 'areas.onMonsterDead was not called');
});

test('killing the area boss marks it defeated through areas', async () => {
  const RPG = await loadEngine();
  const w = RPG.sim.createWorld({ seed: 5, areaId: 'arena' });
  const boss = w.monsters.find((m) => m.defId === w.area.def.boss.bossId);
  assert.ok(boss, 'arena has no boss');
  const bossId = boss.defId;
  assert.equal(w.progress.bossesDefeated[bossId], undefined);
  boss.hp = 0; boss.dead = true;
  RPG.sim.step(w, idle(), DT); // the corpse is despawned (pooled and reset) the same tick, so only progress is checked
  assert.equal(w.progress.bossesDefeated[bossId], true);
  assert.equal(w.progress.kills, 1);
});

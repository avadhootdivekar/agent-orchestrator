'use strict';
// Headless end-to-end runs through core/game.js and the real sim (docs/INTEGRATION-PLAN.md §8 steps 2-12, W4-14).
// No DOM, no real clock: every run is a fixed tick count with a seeded bot, so assertions are exact and replayable.

const test = require('node:test');
const assert = require('node:assert/strict');
const { makeGame, makeBot, fingerprint, DT, BOSS_ID } = require('./helpers.js');

const MINUTE_TICKS = 60 / DT; // 3600 fixed steps = 60 s of world time

test('scripted 60 s run: no exceptions, monsters die, XP grows and a level is gained', async () => {
  const g = await makeGame();
  const w0 = g.newGame('run-60s');
  const { NAMES } = g;
  const kills = [], levelUps = [];
  g.game.on(NAMES.KILL, (e) => kills.push(e.defId));
  g.game.on(NAMES.LEVEL_UP, (e) => levelUps.push(e.level));
  g.RPG.world.areas.enter(g.game.world, 'forest');
  const xpBefore = g.game.world.player.xp;
  assert.equal(w0.player.level, 1);

  const run = g.drive(MINUTE_TICKS, makeBot('run-60s'));

  const w = g.game.world;
  assert.equal(run.stepErrors, 0, 'the sim threw during the run');
  assert.ok(kills.length > 0, 'the bot killed no monsters in 60 s');
  assert.equal(w.progress.kills, kills.length, 'progress.kills disagrees with the KILL events');
  assert.ok(w.player.xp > xpBefore, 'XP did not grow');
  assert.ok(levelUps.length >= 1, 'no LEVEL_UP event in 60 s');
  assert.ok(w.player.level > 1, 'player level did not rise');
  assert.equal(w.player.level, 1 + levelUps.length, 'level and LEVEL_UP count disagree');
});

test('determinism: two runs with the same seed and intents are identical after N ticks', async () => {
  const runOnce = async (seed) => {
    const g = await makeGame();
    g.newGame(seed);
    g.RPG.world.areas.enter(g.game.world, 'forest');
    g.drive(MINUTE_TICKS, makeBot(seed));
    return { fp: fingerprint(g.game.world), kills: g.game.world.progress.kills, tick: g.game.world.tick };
  };

  const a = await runOnce('determinism');
  const b = await runOnce('determinism');
  assert.ok(a.kills > 0, 'the run did nothing, so equality would prove nothing');
  assert.equal(a.tick, MINUTE_TICKS);
  assert.equal(b.fp, a.fp, 'same seed diverged');

  // Sanity check that the fingerprint is sensitive to the seed, so the equality above is meaningful.
  const c = await runOnce('determinism-other');
  assert.notEqual(c.fp, a.fp, 'a different seed produced identical state');
});

test('boss fight: god-mode player kills warden-of-ash, sets bossesDefeated and rolls its loot table', async () => {
  const g = await makeGame();
  g.newGame('boss-fight');
  const { NAMES } = g;
  g.RPG.world.areas.enter(g.game.world, 'arena');
  const w = g.game.world;
  const boss = w.monsters.find((m) => m.defId === BOSS_ID);
  assert.ok(boss, 'the arena did not spawn the boss');

  // Drop events and boss kill are recorded with the tick they happened on, so only this kill's loot is checked.
  const drops = [];
  let bossKillTick = null;
  g.game.on(NAMES.ITEM_DROPPED, (e) => drops.push({ tick: w.tick, rarity: e.rarity }));
  g.game.on(NAMES.KILL, (e) => { if (e.boss) bossKillTick = w.tick; });

  // God mode is a test-side rule: HP is topped up before every step, so the fight is decided by damage dealt alone.
  const bot = makeBot('boss-fight');
  const godBot = (game) => {
    const p = game.world.player;
    p.hp = p.stats.maxHp;
    p.dead = false;
    return bot(game);
  };
  const CAP_TICKS = 20 * MINUTE_TICKS; // 20 minutes of world time is far more than the fight needs
  let t = 0;
  for (; t < CAP_TICKS && !w.progress.bossesDefeated[BOSS_ID]; t++) g.drive(1, godBot);

  assert.equal(g.game.stepErrors, 0, 'the sim threw during the fight');
  assert.ok(w.progress.bossesDefeated[BOSS_ID], `boss not defeated within ${t} ticks`);
  // The dead boss was swept back into the pool (its fields cleared), so check the world, not the old reference.
  assert.equal(w.monsters.some((m) => m.defId === BOSS_ID && !m.dead), false, 'a living boss is still in the arena');
  assert.notEqual(bossKillTick, null, 'no KILL event flagged boss: true');
  // dropChance 1 and quality 1 mean the boss table rolls exactly `rolls` items. The rarity of any one run is a random
  // draw (about 1 run in 30 gives three commons), so the epic-or-better rate is checked over many rolls in the next test.
  const bossDrops = drops.filter((d) => d.tick === bossKillTick);
  assert.equal(bossDrops.length, g.RPG.data.monsters[BOSS_ID].lootTable.rolls, 'boss kill did not roll its table');
});

test('boss loot table yields epic-or-better items at its declared rarity boost', async () => {
  const g = await makeGame();
  const RPG = g.RPG;
  const boss = RPG.data.monsters[BOSS_ID];
  const diff = RPG.world.difficulty.resolve('normal');
  const ARENA_LEVEL = 8; // data/areas.js: the arena's top level
  let rolled = 0, highRank = 0;
  for (let seed = 1; seed <= 300; seed++) {
    const items = RPG.world.loot.rollMonsterDrops(RPG.core.Rng.create(seed), boss, { areaLevel: ARENA_LEVEL, difficulty: diff });
    for (const it of items) {
      rolled++;
      if (RPG.data.rarities[it.rarity].rank >= RPG.data.rarities.epic.rank) highRank++;
    }
  }
  assert.equal(rolled, 300 * boss.lootTable.rolls);
  assert.ok(highRank / rolled >= 0.2, `epic-or-better rate ${(highRank / rolled).toFixed(3)} is below 0.20 (${highRank}/${rolled})`);
});

test('area transitions: town opens the forest, the caves stay locked until level 4, then the exit enters them', async () => {
  const g = await makeGame();
  g.newGame('transitions');
  const { NAMES, RPG } = g;
  const w = g.game.world;
  const changes = [];
  g.game.on(NAMES.AREA_CHANGED, (e) => changes.push(e));

  assert.equal(w.area.id, 'town');
  assert.equal(RPG.world.areas.isUnlocked(w, 'forest'), true, 'forest is open from town');
  assert.equal(RPG.world.areas.isUnlocked(w, 'caves'), false, 'caves are locked at level 1');

  // Stand on the town -> forest exit and step once: the sim's areas stage must perform the transition.
  const toForest = w.area.exits.find((ex) => ex.toArea === 'forest');
  assert.ok(toForest, 'town has no exit to the forest');
  w.player.x = toForest.x; w.player.y = toForest.y;
  g.drive(1, () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} }));
  assert.equal(w.area.id, 'forest');
  assert.deepEqual(changes.map((e) => [e.from, e.to]), [['town', 'forest']]);

  // Forest -> caves stays shut below level 4: standing on the exit does nothing.
  const toCaves = w.area.exits.find((ex) => ex.toArea === 'caves');
  assert.ok(toCaves, 'forest has no exit to the caves');
  w.player.x = toCaves.x; w.player.y = toCaves.y;
  g.drive(1, () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} }));
  assert.equal(w.area.id, 'forest', 'the caves opened below the level requirement');

  // At level 4 the same exit works. (Level is set directly: the requirement is data, the gate is what is under test.)
  w.player.level = 4;
  assert.equal(RPG.world.areas.isUnlocked(w, 'caves'), true);
  w.player.x = toCaves.x; w.player.y = toCaves.y;
  g.drive(1, () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} }));
  assert.equal(w.area.id, 'caves');
  assert.deepEqual(changes.map((e) => e.to), ['forest', 'caves']);
});

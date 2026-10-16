'use strict';
// Boss correctness in the live combat path (W5-08): damageTakenMult in resolve, enemy projectile pooling, and a seeded
// full boss fight through areas.enter whose telegraphs are avoidable and only damage after their warning.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');
const { makeGame, makeBot, DT, BOSS_ID } = require('../integration/helpers.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/pool.js', 'core/entities.js', 'core/state.js', 'core/events.js',
  'data/skills.js', 'data/monsters.js', 'data/weapons.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/world.js', 'world/combat.js',
  'world/weapons.js', 'world/skills.js', 'world/skill-effects.js', 'world/boss.js', 'sim/combat-step.js'
]);
const stage = RPG.sim.stage;
const ROOM = Array.from({ length: 20 }, (_, y) => (y === 0 || y === 19 ? '#'.repeat(20) : '#' + '.'.repeat(18) + '#'));

function mkWorld() {
  const w = RPG.core.state.createWorldState({ seed: 7 });
  w.area = { map: RPG.world.tilemap.fromRows(ROOM, 32) };
  const p = RPG.core.entities.baseEntity('player', 320, 320, 10, 0);
  p.id = w.nextEntityId++;
  p.stats = new RPG.world.StatBlock().snapshot();
  p.hp = p.maxHp = p.stats.maxHp;
  p.cooldowns = {};
  w.player = p;
  w.scratch = { intent: null, dt: DT, events: [], hits: [], killed: [], spatial: null, bossWorld: null, actions: null };
  return w;
}

function run(w, ticks) {
  for (let i = 0; i < ticks; i++) {
    w.scratch.hits.length = 0;
    stage.projectiles(w, w.scratch);
    stage.statuses(w, w.scratch);
    stage.resolve(w, w.scratch);
  }
}

function hitBoss(w, boss, amount) {
  RPG.world.combat.applyHit(null, boss, { dealt: amount, knockback: null, statuses: [], lifeSteal: 0 });
}

test('resolve applies boss.damageTakenMult: a shielded phase takes reduced damage', () => {
  const w = mkWorld();
  const boss = RPG.world.spawnMonster(w, BOSS_ID, 200, 200);
  const def = RPG.data.monsters[BOSS_ID];
  boss.def = Object.assign({}, def, { phases: def.phases.map((ph, i) => (i === 0 ? Object.assign({}, ph, { damageTakenMult: 0.5 }) : ph)) });
  boss.ai = { phase: 0 };
  run(w, 1);                                   // records the boss's HP baseline
  const full = boss.hp;
  hitBoss(w, boss, 100);
  run(w, 1);
  assert.equal(boss.hp, full - 50, 'shield phase halves the damage');
  boss.fight = { vulnerableT: 2 };             // vulnerable window stacks on the shield: 0.5 * 1.5
  const before = boss.hp;
  hitBoss(w, boss, 100);
  run(w, 1);
  assert.equal(boss.hp, before - 75);
});

test('resolve: a hit that scaling shrinks below lethal does not kill the boss; damageTakenMult 0 is immunity', () => {
  const w = mkWorld();
  const boss = RPG.world.spawnMonster(w, BOSS_ID, 200, 200);
  const def = RPG.data.monsters[BOSS_ID];
  boss.def = Object.assign({}, def, { phases: def.phases.map((ph) => Object.assign({}, ph, { damageTakenMult: 0 })) });
  boss.ai = { phase: 0 };
  run(w, 1);
  const full = boss.hp;
  hitBoss(w, boss, full * 10);                 // would be lethal, applyHit marks it dead
  assert.equal(boss.dead, true);
  run(w, 1);
  assert.equal(boss.hp, full);
  assert.equal(boss.dead, false, 'immune boss is revived from the pre-scaling death');
});

test('unmodified boss damage is untouched by resolve', () => {
  const w = mkWorld();
  const boss = RPG.world.spawnMonster(w, BOSS_ID, 200, 200);
  run(w, 1);
  const full = boss.hp;
  hitBoss(w, boss, 40);
  run(w, 1);
  assert.equal(boss.hp, full - 40);
});

test('dead enemy projectiles go back to the projectile pool (poolStats)', () => {
  const w = mkWorld();
  const mk = () => {
    const s = RPG.world.spawnProjectile(w, { x: 100, y: 100, angle: 0, speed: 0, radius: 4, lifetime: 0.1, team: 1,
      damage: { amount: 1, type: 'phys' } });
    w.projectiles.splice(w.projectiles.lastIndexOf(s), 1);
    w.enemyProjectiles.push(s);
    return s;
  };
  mk(); mk(); mk();
  assert.equal(RPG.world.poolStats(w).projectile.active, 3);
  run(w, 10);                                  // lifetime 0.1 s = 6 ticks
  assert.equal(w.enemyProjectiles.length, 0);
  const st = RPG.world.poolStats(w).projectile;
  assert.equal(st.active, 0, 'expired shots left the pool\'s active list');
  assert.equal(st.free, 3, 'and are reusable');
});

// ---- full fight through areas.enter -------------------------------------------------------------------------------

const MINUTE_TICKS = 60 / DT;
const MIN_WARNING_TICKS = Math.round(0.8 / DT);   // data/monsters.js: every boss attack telegraphs for at least 0.8 s

function wrapBossDamage(g) {
  const bossHits = [];
  const orig = g.RPG.sim.hurtPlayer;
  g.RPG.sim.hurtPlayer = (world, amount, info) => {
    if (info && info.source === 'boss') bossHits.push({ tick: world.tick, attackId: info.attackId, zone: info.zone });
    return orig(world, amount, info);
  };
  return bossHits;
}

// Moves the player (test-side "player dodged") to the nearest spot outside every live boss warning and ember zone.
const IDLE = { move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} };

const LOOKAHEAD = 8;   // px: a few ticks of player movement

function isSafe(g, w, boss, x, y) {
  return !w.telegraphs.some((e) => e.owner === boss && !e.done && g.RPG.world.boss.inShape(e, x, y));
}

// Returns true while any warning or zone of the boss is live.
function dodge(g, w, boss) {
  const p = w.player, shapeOf = g.RPG.world.boss.inShape, col = g.RPG.world.collision;
  const live = () => w.telegraphs.filter((e) => e.owner === boss && !e.done);
  const unsafe = (x, y) => live().some((e) => shapeOf(e, x, y));
  if (!live().length) return false;
  if (!unsafe(p.x, p.y)) return true;
  for (let r = 24; r <= 400; r += 24) {
    for (let k = 0; k < 16; k++) {
      const a = (k / 16) * Math.PI * 2, x = p.x + Math.cos(a) * r, y = p.y + Math.sin(a) * r;
      if (unsafe(x, y) || col.circleOverlapsSolid(w.area.map, x, y, p.r + 2)) continue;
      p.x = x; p.y = y;
      return true;
    }
  }
  return true;
}

test('seeded boss fight via areas.enter: every phase, 2+ telegraphed patterns, an avoided telegraph never hurts, loot awarded', async () => {
  const g = await makeGame();
  g.newGame('boss-combat');
  g.RPG.world.areas.enter(g.game.world, 'arena');
  const w = g.game.world;
  const boss = w.monsters.find((m) => m.defId === BOSS_ID);
  assert.ok(boss, 'arena spawned the boss');
  const bossHits = wrapBossDamage(g);
  const phases = [], warned = new Set(), drops = [];
  let killTick = null;
  g.game.on(g.NAMES.BOSS_PHASE, (e) => phases.push(e.to));
  g.game.on(g.NAMES.BOSS_WARN, (e) => warned.add(e.attackId));
  g.game.on(g.NAMES.ITEM_DROPPED, () => drops.push(w.tick));
  g.game.on(g.NAMES.KILL, (e) => { if (e.boss) killTick = w.tick; });

  const bot = makeBot('boss-combat');
  const intent = (game) => {
    const p = game.world.player;
    p.hp = p.stats.maxHp; p.dead = false;           // survive adds so the fight is decided by the boss mechanics
    p.xp = 0;                                       // adds pay XP; keep the player at level 1 so the fight stays the seeded one
    const danger = dodge(g, w, boss);
    const it = bot(game);
    // While a warning is live the player may only step to ground that is still outside every shape.
    if (danger && !isSafe(g, w, boss, game.world.player.x + it.move.x * LOOKAHEAD, game.world.player.y + it.move.y * LOOKAHEAD)) {
      it.move = { x: 0, y: 0 };
    }
    return it;
  };
  let t = 0;
  for (; t < 30 * MINUTE_TICKS && !w.progress.bossesDefeated[BOSS_ID]; t++) g.drive(1, intent);

  assert.equal(g.game.stepErrors, 0);
  assert.ok(w.progress.bossesDefeated[BOSS_ID], `boss not defeated within ${t} ticks (hp ${boss.hp}/${boss.maxHp}, phases ${phases}, player ${w.player.x|0},${w.player.y|0} boss ${boss.x|0},${boss.y|0})`);
  assert.deepEqual(phases, [1, 2], 'passed through every phase once');
  assert.ok(warned.size >= 2, 'used at least two telegraphed patterns: ' + [...warned]);
  assert.deepEqual(bossHits, [], 'a player outside the warning shape took boss damage');
  const lootRolls = g.RPG.data.monsters[BOSS_ID].lootTable.rolls;
  assert.equal(drops.filter((d) => d === killTick).length, lootRolls, 'boss loot table rolled on the kill tick');
  assert.equal(g.RPG.world.poolStats(w).projectile.active, w.projectiles.length + w.enemyProjectiles.length);
});

test('a player who stands still in a boss telegraph is hit only after the warning has run its full length', async () => {
  const g = await makeGame();
  g.newGame('boss-combat-stand');
  g.RPG.world.areas.enter(g.game.world, 'arena');
  const w = g.game.world;
  const boss = w.monsters.find((m) => m.defId === BOSS_ID);
  const bossHits = wrapBossDamage(g);
  const warnTicks = [];
  g.game.on(g.NAMES.BOSS_WARN, () => warnTicks.push(w.tick));
  const idle = IDLE;
  w.player.x = boss.x - 120; w.player.y = boss.y;   // inside the boss's aggro range, close enough for any pattern to reach
  for (let i = 0; i < 20 * 60 && bossHits.length === 0; i++) {
    w.player.hp = w.player.stats.maxHp; w.player.dead = false;
    g.drive(1, () => idle);
  }
  assert.ok(warnTicks.length >= 1 && bossHits.length >= 1, 'the standing player was hit by a telegraphed attack');
  const lastWarn = Math.max(...warnTicks.filter((tk) => tk <= bossHits[0].tick));
  assert.ok(bossHits[0].tick - lastWarn >= MIN_WARNING_TICKS - 1, `damage landed ${bossHits[0].tick - lastWarn} ticks after the warning`);
});

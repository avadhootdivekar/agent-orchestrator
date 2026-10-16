'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');

// Own context (tests/sim/load.js belongs to the sim.js unit): everything the monster stages touch, in build order.
const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js', 'core/state.js',
  'data/monsters.js', 'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/spatial.js', 'world/combat.js',
  'world/ai.js', 'world/boss.js', 'world/world.js', 'data/maps/test-map.js', 'sim/player.js', 'sim/sim.js', 'sim/monsters.js',
]);
const { NAMES } = RPG.core.events;
const stage = RPG.sim.stage;
const DT = 1 / 60;
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

// 40x30 open room, so walls never interfere with the behaviour under test.
const OPEN_ROWS = Array.from({ length: 30 }, (_, y) => (y === 0 || y === 29 ? '#'.repeat(40) : '#' + '.'.repeat(38) + '#'));

function makeWorld(seed = 7) {
  const w = RPG.sim.createWorld({ seed });
  w.area.map = RPG.world.tilemap.fromRows(OPEN_ROWS, 32);
  w.player.x = 600; w.player.y = 400;
  w.player.hp = w.player.maxHp = 100000;     // behaviour tests: the player must survive the whole scenario
  w.scratch = { dt: DT };
  return w;
}

function spawn(w, defId, x, y, opts) {
  return RPG.world.spawnMonster(w, defId, x, y, opts);
}

// Records every hurtPlayer call (and applies it), standing in for combat-step's single damage entry point.
function recordHurt(w) {
  const calls = [];
  RPG.sim.hurtPlayer = (world, amount, info) => {
    calls.push({ amount, info });
    world.player.hp -= amount;
    return amount;
  };
  return calls;
}
test.afterEach(() => { delete RPG.sim.hurtPlayer; });

function tick(w, n = 1, each) {
  for (let i = 0; i < n; i++) {
    stage.spatial(w, w.scratch);
    stage.monsters(w, w.scratch);
    stage.movement(w, w.scratch);
    RPG.world.despawnDead(w);
    w.tick++;
    if (each) each(w, i);
  }
}

function listen(type) {
  const seen = [];
  const off = RPG.core.events.on(type, (p) => seen.push(p));
  return { seen, off };
}

test('stages are registered on RPG.sim.stage', () => {
  for (const name of ['spatial', 'monsters', 'movement']) assert.equal(typeof stage[name], 'function', name);
});

test('spatial stage indexes the player and living monsters only', () => {
  const w = makeWorld();
  const a = spawn(w, 'husk-grunt', 700, 400);
  const dead = spawn(w, 'husk-grunt', 710, 400);
  dead.dead = true;
  stage.spatial(w, w.scratch);
  assert.equal(w.grid.count, 2);
  const out = [];
  RPG.world.spatial.queryCircle(w.grid, 700, 400, 30, out);
  assert.deepEqual(out.map((e) => e.id), [a.id]);
});

// ---- the seven archetypes ----------------------------------------------------------------------------------------

test('melee (husk-grunt): chases, telegraphs, then hits the player in its cone', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  const m = spawn(w, 'husk-grunt', 600 - 150, 400);
  let telegraphTick = -1, hitTick = -1;
  tick(w, 60 * 8, (_, i) => {
    if (telegraphTick < 0 && w.effects.some((e) => e.type === 'telegraph')) telegraphTick = i;
    if (hitTick < 0 && calls.length) hitTick = i;
  });
  assert.ok(telegraphTick >= 0, 'telegraph effect spawned');
  assert.ok(hitTick > telegraphTick, 'damage lands after the warning');
  assert.ok(hitTick - telegraphTick >= 0.3 / DT - 1, 'warning lasts roughly the windup');
  assert.equal(calls[0].info.source, 'monster');
  assert.equal(calls[0].info.attackerId, m.id);
  assert.ok(Math.abs(calls[0].amount - m.damage) < 1e-9);
  assert.ok(dist(m, w.player) < 36);
});

test('melee: a player who steps out of the windup is not hit', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  spawn(w, 'husk-grunt', 600 - 60, 400);
  // Run until the telegraph appears, then teleport the player out of reach before the strike resolves.
  tick(w, 200, () => {
    if (w.effects.some((e) => e.type === 'telegraph') && w.player.x === 600) { w.player.x = 900; }
  });
  assert.equal(calls.length, 0);
});

test('telegraph effects expire and are cleaned up', () => {
  const w = makeWorld();
  recordHurt(w);
  spawn(w, 'husk-grunt', 600 - 40, 400);
  tick(w, 14);                             // closes the last few px, then winds up (0.35 s)
  assert.ok(w.effects.some((e) => e.type === 'telegraph'));
  w.player.x = 1100;                       // beyond leash range: no new telegraphs
  tick(w, 60);
  assert.equal(w.effects.filter((e) => e.type === 'telegraph').length, 0);
});

test('flanker (skitter): approaches on a curved path and bites', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  const m = spawn(w, 'skitter', 600 - 200, 400);
  let maxOffAxis = 0;
  tick(w, 60 * 6, () => { maxOffAxis = Math.max(maxOffAxis, Math.abs(m.y - 400)); });
  assert.ok(maxOffAxis > 5, `flanks off the straight line (${maxOffAxis})`);
  assert.ok(calls.length >= 1, 'bites');
});

test('ranged (bone-archer): holds its distance and fires into world.enemyProjectiles, not world.projectiles', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  const m = spawn(w, 'bone-archer', 600 - 290, 400);
  let shot = null;
  tick(w, 60 * 6, () => {
    if (!shot && w.enemyProjectiles.length) shot = w.enemyProjectiles[0];
  });
  assert.ok(shot, 'arrow spawned');
  assert.equal(shot.team, 1);
  assert.equal(shot.ownerId, m.id);
  assert.equal(shot.shape, 'arrow');
  assert.ok(shot.damage.amount > 0);
  assert.equal(w.projectiles.length, 0, 'player projectile list untouched');
  const d = dist(m, w.player);
  assert.ok(d < 290 && d > 120, `kiting distance ${d}`);
  assert.equal(calls.length, 0, 'the sim stage only spawns the shot; combat-step resolves it');
});

test('tank (stone-brute): advances slower than its listed speed and hits harder', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  const m = spawn(w, 'stone-brute', 600 - 170, 400);
  const x0 = m.x;
  tick(w, 60);
  const def = RPG.data.monsters['stone-brute'];
  const moved = m.x - x0;
  assert.ok(moved > 0 && moved <= def.baseStats.speed * RPG.world.ai.TANK_SPEED_MULT * 1 + 1e-6, `moved ${moved}`);
  tick(w, 60 * 8);
  assert.ok(calls.length >= 1);
  assert.ok(Math.abs(calls[0].amount - m.damage * 1.3) < 1e-9, 'damageMult 1.3 applied');
});

test('exploder (cinder-bomb): fuses, blasts once, dies without granting rewards', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  const m = spawn(w, 'cinder-bomb', 600 - 150, 400);
  const damage = m.damage;                          // the pooled object is wiped when it despawns
  tick(w, 60 * 8);
  assert.equal(calls.length, 1, 'one blast, not an attack plus an explosion');
  assert.ok(Math.abs(calls[0].amount - damage * 1.5) < 1e-9);
  assert.equal(w.monsters.includes(m), false, 'despawned after exploding');
  assert.equal(m.selfDestructed, undefined, 'pooled object was reset on release');
});

test('exploder: marks the monster dead + rewarded so the rewards stage skips it', () => {
  const w = makeWorld();
  recordHurt(w);
  const m = spawn(w, 'cinder-bomb', 600 - 40, 400);
  let seen = null;
  for (let i = 0; i < 60 * 4 && !seen; i++) {
    stage.spatial(w, w.scratch); stage.monsters(w, w.scratch); stage.movement(w, w.scratch);
    if (m.dead) seen = { dead: m.dead, rewarded: m.rewarded, selfDestructed: m.selfDestructed };
  }
  assert.deepEqual(seen, { dead: true, rewarded: true, selfDestructed: true });
});

test('exploder: a player outside the blast radius is not hurt', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  spawn(w, 'cinder-bomb', 600 - 40, 400);
  tick(w, 30, () => { if (w.player.x === 600 && w.effects.some((e) => e.type === 'telegraph')) w.player.x = 1100; });
  tick(w, 120);
  assert.equal(calls.length, 0);
});

test('summoner (hexcaller): calls minions and never exceeds summonMax living adds', () => {
  const w = makeWorld();
  recordHurt(w);
  const m = spawn(w, 'hexcaller', 600 - 190, 400);
  const max = RPG.data.monsters.hexcaller.aiParams.summonMax;
  let peak = 0;
  tick(w, 60 * 60, () => {
    // keep the minions from wandering off or dying so the cap is what stops further summons
    const adds = w.monsters.filter((x) => !x.dead && x.summonerId === m.id);
    peak = Math.max(peak, adds.length);
    assert.ok(adds.length <= max, `adds ${adds.length} > ${max}`);
    adds.forEach((a) => { a.hp = a.maxHp; });
  });
  assert.equal(peak, max, 'summoned up to the cap');
  const add = w.monsters.find((x) => x.summonerId === m.id);
  assert.equal(add.defId, 'skitter');
  assert.equal(add.level, m.level);
});

test('summoner: also fires its bolt as an enemy projectile', () => {
  const w = makeWorld();
  recordHurt(w);
  spawn(w, 'hexcaller', 600 - 190, 400);
  let bolts = 0;
  tick(w, 60 * 20, () => { bolts = Math.max(bolts, w.enemyProjectiles.filter((p) => p.shape === 'orb').length); });
  assert.ok(bolts >= 1);
});

test('boss (warden-of-ash): warnings go to world.telegraphs; world.effects keeps its identity', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  const warn = listen(NAMES.BOSS_WARN);
  const boss = spawn(w, 'warden-of-ash', 600 - 250, 400);
  const effectsBefore = w.effects;
  let sawWarning = false;
  tick(w, 60 * 12, () => {
    assert.strictEqual(w.effects, effectsBefore, 'world.effects was reassigned');
    if (w.telegraphs.some((e) => e.kind === 'warning' && e.owner === boss)) sawWarning = true;
  });
  warn.off();
  assert.ok(sawWarning, 'boss warning pushed on the telegraph list');
  assert.ok(warn.seen.length >= 1, 'BOSS_WARN emitted');
  assert.equal(warn.seen[0].bossId, boss.id);
  assert.ok(calls.some((c) => c.info.source === 'boss'), 'boss damage reaches the player through hurtPlayer');
  assert.equal(w.effects.some((e) => e.kind === 'warning'), false, 'nothing from boss.js leaked into world.effects');
});

test('boss: world.effects identity is unchanged after a single boss tick that has live telegraph effects', () => {
  const w = makeWorld();
  recordHurt(w);
  const boss = spawn(w, 'warden-of-ash', 600 - 250, 400);
  tick(w, 5);                                       // first attack telegraphs, boss.update filters its effect list
  const effects = w.effects;
  // A pooled effect (as skills/monsters create) sits in world.effects; the boss tick must neither drop nor replace it.
  const e = RPG.world.spawnEffect(w, { type: 'aoe', x: 1, y: 1, r: 5, team: 0, duration: 5 });
  tick(w, 1);
  assert.strictEqual(w.effects, effects);
  assert.ok(w.effects.includes(e));
  assert.ok(boss.fight, 'boss fight state exists');
});

test('boss: BOSS_PHASE is emitted once per phase change with from/to/special', () => {
  const w = makeWorld();
  recordHurt(w);
  const phase = listen(NAMES.BOSS_PHASE);
  const boss = spawn(w, 'warden-of-ash', 600 - 250, 400);
  tick(w, 60);
  assert.equal(phase.seen.length, 0);
  // The AI only re-evaluates its phase between attacks, so wait for each change to be announced.
  const until = (n) => { for (let i = 0; i < 60 * 10 && phase.seen.length < n; i++) tick(w, 1); };
  boss.hp = boss.maxHp * 0.5;                      // below 0.66 -> phase 1
  until(1);
  boss.hp = boss.maxHp * 0.2;                      // below 0.33 -> phase 2 (enrage)
  until(2);
  tick(w, 120);                                    // no repeats
  phase.off();
  assert.deepEqual(phase.seen.map((p) => [p.from, p.to, p.special]), [[0, 1, 'adds'], [1, 2, 'enrage']]);
  assert.equal(phase.seen[0].bossId, boss.id);
  assert.equal(phase.seen[0].defId, 'warden-of-ash');
});

test('boss: add wave spawns through the adapter, capped, tagged with the boss as summoner', () => {
  const w = makeWorld();
  recordHurt(w);
  const boss = spawn(w, 'warden-of-ash', 600 - 250, 400);
  boss.hp = boss.maxHp * 0.5;
  let peak = 0;
  tick(w, 60 * 90, () => {
    const adds = w.monsters.filter((x) => !x.dead && x.summonerId === boss.id);
    peak = Math.max(peak, adds.length);
    assert.ok(adds.length <= RPG.sim.monsters.BOSS_ADD_CAP);
    adds.forEach((a) => { a.hp = a.maxHp; });
    boss.hp = Math.max(boss.hp, boss.maxHp * 0.5);   // stay in phase 1 so the add wave keeps being picked
  });
  assert.ok(peak >= 2, `adds spawned (${peak})`);
  assert.equal(w.monsters.find((x) => x.summonerId === boss.id).defId, 'husk-grunt');
});

test('boss: warnings of a dead boss are purged from world.telegraphs', () => {
  const w = makeWorld();
  recordHurt(w);
  const boss = spawn(w, 'warden-of-ash', 600 - 250, 400);
  tick(w, 30);
  assert.ok(w.telegraphs.length > 0);
  boss.dead = true;
  tick(w, 2);
  assert.equal(w.telegraphs.length, 0);
});

// ---- movement / status handling ----------------------------------------------------------------------------------

test('stunned and frozen monsters neither move nor attack', () => {
  for (const id of ['stun', 'freeze']) {
    const w = makeWorld();
    const calls = recordHurt(w);
    const m = spawn(w, 'husk-grunt', 600 - 40, 400);
    RPG.world.combat.applyStatus(m, { id, duration: 5 });
    const x0 = m.x;
    tick(w, 120);
    assert.equal(m.x, x0, id);
    assert.equal(calls.length, 0, id);
  }
});

test('slow scales movement speed', () => {
  const run = (slow) => {
    const w = makeWorld();
    const m = spawn(w, 'husk-grunt', 450, 400);
    if (slow) RPG.world.combat.applyStatus(m, { id: 'slow', duration: 10, strength: 0.5 });
    tick(w, 30);
    return m.x - 450;
  };
  assert.ok(Math.abs(run(true) / run(false) - 0.5) < 0.02);
});

test('knockback velocity pushes through the tilemap and decays', () => {
  const w = makeWorld();
  const m = spawn(w, 'husk-grunt', 300, 400);
  w.player.x = 1100;                                // out of aggro: only knockback moves it
  m.vx = 300;
  tick(w, 1);
  assert.ok(m.x > 300 && m.vx < 300 && m.vx > 0);
  tick(w, 120);
  assert.equal(m.vx, 0);
  const rest = m.x;
  tick(w, 10);
  assert.equal(m.x, rest);
});

test('movement keeps monsters out of walls', () => {
  const w = makeWorld();
  const m = spawn(w, 'husk-grunt', 50, 400);
  w.player.x = 1100;
  m.vx = -2000;
  tick(w, 10);
  assert.ok(m.x >= 32 + m.r - 1e-6, `x ${m.x}`);
});

test('separation pushes stacked packs apart without moving anything else', () => {
  const w = makeWorld();
  w.player.x = 1100;
  const a = spawn(w, 'husk-grunt', 300, 400);
  const b = spawn(w, 'husk-grunt', 302, 400);
  tick(w, 60);
  assert.ok(dist(a, b) >= a.r + b.r - 0.5, `distance ${dist(a, b)}`);
  const c = spawn(w, 'husk-grunt', 700, 700);
  tick(w, 10);
  assert.equal(c.x, 700);
});

test('exactly stacked monsters separate (no NaN)', () => {
  const w = makeWorld();
  w.player.x = 1100;
  const a = spawn(w, 'husk-grunt', 300, 400);
  const b = spawn(w, 'husk-grunt', 300, 400);
  tick(w, 30);
  assert.ok(Number.isFinite(a.x) && Number.isFinite(b.x));
  assert.ok(dist(a, b) > 1);
});

test('a dead player is no longer a target: monsters idle', () => {
  const w = makeWorld();
  const calls = recordHurt(w);
  const m = spawn(w, 'husk-grunt', 600 - 100, 400);
  w.player.dead = true;
  tick(w, 120);
  assert.equal(m.x, 500);
  assert.equal(calls.length, 0);
});

test('works without a ctx argument (falls back to world.scratch) and with dt 0', () => {
  const w = makeWorld();
  const m = spawn(w, 'husk-grunt', 400, 400);
  w.scratch.dt = DT;
  stage.spatial(w);
  stage.monsters(w);
  stage.movement(w);
  assert.ok(m.x > 400);
  w.scratch.dt = 0;
  const x = m.x;
  stage.monsters(w); stage.movement(w);
  assert.equal(m.x, x);
});

test('deterministic: same seed and setup give identical monster state', () => {
  const run = () => {
    const w = makeWorld(11);
    recordHurt(w);
    spawn(w, 'skitter', 300, 400); spawn(w, 'husk-grunt', 900, 380); spawn(w, 'hexcaller', 400, 600);
    tick(w, 600);
    return JSON.stringify(w.monsters.map((m) => [m.id, m.defId, +m.x.toFixed(6), +m.y.toFixed(6), m.hp]));
  };
  assert.equal(run(), run());
});

test('fallback hurt (no combat-step loaded) applies armor and kills the player', () => {
  const w = makeWorld();
  w.player.hp = 5; w.player.maxHp = 5;
  spawn(w, 'husk-grunt', 600 - 40, 400, { level: 10 });
  tick(w, 200);
  assert.equal(w.player.dead, true);
  assert.equal(w.player.hp, 0);
});

test('a monster with an unknown definition is skipped, not thrown on', () => {
  const w = makeWorld();
  const m = spawn(w, 'husk-grunt', 400, 400);
  m.defId = 'does-not-exist';
  assert.doesNotThrow(() => tick(w, 3));
});

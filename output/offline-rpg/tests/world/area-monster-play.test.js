'use strict';
// Headless play-through of every area through the real sim pipeline (w06-04): areas.enter populates, RPG.sim.step runs
// the full fixed-order stage list, a scripted player walks and fights. Behaviour assertions run on a real world whose
// map is swapped for an open room, so walls never mask the AI under test.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine } = require('../integration/helpers.js');

const DT = 1 / 60;
const AREAS = ['town', 'forest', 'caves', 'arena'];
const OPEN_ROWS = Array.from({ length: 30 }, (_, y) => (y === 0 || y === 29 ? '#'.repeat(40) : '#' + '.'.repeat(38) + '#'));
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

function idle() { return { move: { x: 0, y: 0 }, aim: { x: 1, y: 0 }, attack: false, skills: [], dash: false, toggles: {} }; }

// Walks toward the nearest living monster and attacks it; wanders when none is left. There is no pathfinding, so when
// a wall pins the player for a second it picks a fresh deterministic heading for a while (cave walls do this a lot).
function scripted(world, n) {
  const p = world.player;
  const bot = world.__bot || (world.__bot = { x: p.x, y: p.y, since: n, heading: 0, until: -1, turns: 0 });
  if (n - bot.since >= 60) {
    if (Math.hypot(p.x - bot.x, p.y - bot.y) < 4) { bot.heading = (bot.turns++ * 2.399963) % (Math.PI * 2); bot.until = n + 90; }
    bot.x = p.x; bot.y = p.y; bot.since = n;
  }
  let best = null, bd = Infinity;
  for (const m of world.monsters) {
    if (m.dead) continue;
    const d = dist(m, p);
    if (d < bd) { bd = d; best = m; }
  }
  const move = { x: 0, y: 0 };
  if (n < bot.until || !best) { move.x = Math.cos(bot.heading + n * 0.002); move.y = Math.sin(bot.heading + n * 0.002); }
  else if (bd > 40) { move.x = (best.x - p.x) / bd; move.y = (best.y - p.y) / bd; }
  const aim = best ? { x: best.x, y: best.y } : { x: p.x + move.x, y: p.y + move.y };
  return { move, aim, attack: !!best && bd < 260, skills: [], dash: false, toggles: {} };
}

// The play-through is about monsters, not player survival.
// Ordinary attacks warn through pooled 'telegraph' effects, boss attacks through world.telegraphs.
function telegraphUp(w) {
  return (w.telegraphs && w.telegraphs.length > 0) || w.effects.some((e) => !e.dead && e.type === 'telegraph');
}

function godMode(world) { world.player.hp = world.player.maxHp = 1e9; world.player.stats.maxHp = 1e9; }

function composition(world) {
  return world.monsters.filter((m) => !m.dead).map((m) => `${m.defId}${m.elite ? '*' : ''}@${Math.round(m.x)},${Math.round(m.y)}`).sort().join('|');
}

function openWorld(RPG, seed) {
  const w = RPG.sim.createWorld({ seed });
  w.area.map = RPG.world.tilemap.fromRows(OPEN_ROWS, 32);
  w.player.x = 600; w.player.y = 400;
  godMode(w);
  return w;
}

function runTicks(RPG, w, n, intentFn, each) {
  for (let i = 0; i < n; i++) {
    godMode(w);
    RPG.sim.step(w, intentFn(w, i), DT);
    if (each) each(w, i);
  }
}

test('every area is entered and simulated for thousands of ticks without throwing', async () => {
  const RPG = await loadEngine();
  for (const areaId of AREAS) {
    const w = RPG.sim.createWorld({ seed: 100 + AREAS.indexOf(areaId), areaId });
    godMode(w);
    assert.equal(w.area.id, areaId);
    assert.ok(w.area.map && w.area.spawn, areaId + ': no map/spawn');
    const t0 = w.tick;
    runTicks(RPG, w, 4000, scripted);
    assert.equal(w.tick - t0, 4000, areaId + ': tick count');
    assert.ok(Number.isFinite(w.player.x) && Number.isFinite(w.player.y), areaId + ': player position went non-finite');
    for (const m of w.monsters) assert.ok(Number.isFinite(m.x) && Number.isFinite(m.y), areaId + ': monster position non-finite');
    if (areaId !== 'town') assert.ok(w.progress.kills > 0, areaId + ': scripted player killed nothing in 4000 ticks');
  }
});

test('town is a safe zone: no monsters on entry or after play, for many seeds', async () => {
  const RPG = await loadEngine();
  assert.equal(RPG.data.areas.town.safe, true);
  for (let s = 0; s < 25; s++) {
    const w = RPG.sim.createWorld({ seed: 200 + s, areaId: 'town' });
    assert.equal(w.monsters.filter((m) => !m.dead).length, 0, 'town seed ' + s + ' spawned monsters on entry');
    runTicks(RPG, w, 300, scripted);
    assert.equal(w.monsters.filter((m) => !m.dead).length, 0, 'town seed ' + s + ' spawned monsters during play');
  }
  // Coming back to town from a hostile area must not carry monsters over either.
  const w = RPG.sim.createWorld({ seed: 299, areaId: 'forest' });
  assert.ok(w.monsters.length > 0);
  RPG.world.areas.enter(w, 'town', 'forest');
  assert.equal(w.monsters.filter((m) => !m.dead).length, 0);
});

test('all 7 archetypes are spawned by the areas (6 from spawn tables, the boss from the arena)', async () => {
  const RPG = await loadEngine();
  const seen = {};
  for (const areaId of AREAS) {
    for (let s = 0; s < 30; s++) {
      const w = RPG.sim.createWorld({ seed: 300 + s, areaId });
      for (const m of w.monsters) {
        const a = RPG.data.monsters[m.defId].archetype;
        seen[a] = (seen[a] || 0) + 1;
      }
    }
  }
  for (const a of ['melee', 'fast', 'ranged', 'tank', 'exploder', 'summoner', 'boss']) {
    assert.ok(seen[a] > 0, 'archetype never spawned by any area: ' + a);
  }
});

test('archetype behaviours are distinct in the real sim', async () => {
  const RPG = await loadEngine();
  const E = RPG.world.spawnMonster;

  // melee (husk-grunt): straight line to the player, telegraphs before it strikes
  {
    const w = openWorld(RPG, 11);
    const m = E(w, 'husk-grunt', 400, 400, { level: 1 });
    const start = dist(m, w.player);
    let maxPerp = 0, sawTelegraph = false;
    runTicks(RPG, w, 240, idle, () => {
      maxPerp = Math.max(maxPerp, Math.abs(m.y - 400));
      if (telegraphUp(w)) sawTelegraph = true;
    });
    assert.ok(dist(m, w.player) < start - 100, 'melee did not close in');
    assert.ok(maxPerp < 8, 'melee should approach in a straight line, drifted ' + maxPerp);
    assert.ok(sawTelegraph, 'melee attack was not telegraphed');
  }

  // flanker (skitter): curved approach, faster than melee
  {
    const w = openWorld(RPG, 12);
    const m = E(w, 'skitter', 360, 400, { level: 1 });
    let maxPerp = 0;
    runTicks(RPG, w, 40, idle, () => { maxPerp = Math.max(maxPerp, Math.abs(m.y - 400)); });
    assert.ok(maxPerp > 15, 'flanker should arc around, lateral drift was only ' + maxPerp);
    assert.ok(RPG.data.monsters.skitter.baseStats.speed > RPG.data.monsters['husk-grunt'].baseStats.speed, 'flanker not faster than melee');
  }

  // ranged (bone-archer): holds a preferred-distance band and fires enemy projectiles
  {
    const w = openWorld(RPG, 13);
    const pd = RPG.data.monsters['bone-archer'].aiParams.preferredDistance;
    const m = E(w, 'bone-archer', 320, 400, { level: 1 });
    let shots = 0, closest = Infinity;
    runTicks(RPG, w, 420, idle, () => {
      shots = Math.max(shots, (w.enemyProjectiles || []).length);
      closest = Math.min(closest, dist(m, w.player));
    });
    assert.ok(shots > 0, 'archer never fired a projectile');
    const d = dist(m, w.player);
    assert.ok(d > pd * 0.7 && d < pd * 1.4, `archer should hold ~${pd}px, ended at ${Math.round(d)}`);
    assert.ok(closest > 30, 'archer walked into melee range');
  }

  // tank (stone-brute): advances at no more than 0.6x its listed speed
  {
    const w = openWorld(RPG, 14);
    const def = RPG.data.monsters['stone-brute'];
    const m = E(w, 'stone-brute', 440, 400, { level: 1 });
    const x0 = m.x;
    runTicks(RPG, w, 60, idle);
    const moved = m.x - x0;
    assert.ok(moved > 0, 'tank did not advance');
    assert.ok(moved <= def.baseStats.speed * 0.6 * 1.05, 'tank moves faster than 0.6x its speed: ' + moved);
  }

  // exploder (cinder-bomb): runs in, shows a fuse telegraph, then detonates
  {
    const w = openWorld(RPG, 15);
    const m = E(w, 'cinder-bomb', 400, 400, { level: 1 });
    let telegraphed = false;
    let gone = false;
    for (let i = 0; i < 600 && !gone; i++) {
      godMode(w);
      RPG.sim.step(w, idle(), DT);
      if (telegraphUp(w)) telegraphed = true;
      gone = w.monsters.indexOf(m) < 0;   // a detonated bomb is despawned and its pooled object recycled
    }
    assert.ok(telegraphed, 'exploder gave no fuse telegraph');
    assert.ok(gone, 'exploder never detonated');
  }

  // summoner (hexcaller): calls skitters, capped at summonMax
  {
    const w = openWorld(RPG, 16);
    const def = RPG.data.monsters.hexcaller;
    const h = E(w, 'hexcaller', 420, 400, { level: 1 });
    let maxAdds = 0;
    runTicks(RPG, w, 1200, idle, () => {
      const adds = w.monsters.filter((m) => !m.dead && m.summonerId === h.id);
      maxAdds = Math.max(maxAdds, adds.length);
      for (const a of adds) assert.equal(a.defId, def.aiParams.summonDefId);
    });
    assert.ok(maxAdds >= 1, 'summoner never summoned');
    assert.ok(maxAdds <= def.aiParams.summonMax, 'summon cap exceeded: ' + maxAdds);
  }

  // boss (warden-of-ash): arena only, rolled modifiers, telegraphs, advances phases as hp drops
  {
    const w = RPG.sim.createWorld({ seed: 401, areaId: 'arena' });
    const boss = w.monsters.find((m) => m.defId === 'warden-of-ash');
    assert.ok(boss, 'arena has no boss');
    assert.ok(boss.modifiers && boss.modifiers.length >= 1, 'boss has no modifiers');
    boss.x = w.player.x; boss.y = w.player.y - 150;
    let sawTelegraph = false, maxPhase = 0;
    const track = () => {
      if (telegraphUp(w)) sawTelegraph = true;
      maxPhase = Math.max(maxPhase, (boss.ai && boss.ai.phase) || 0);
    };
    runTicks(RPG, w, 1500, idle, track);
    assert.ok(sawTelegraph, 'boss never telegraphed an attack');
    boss.hp = Math.floor(boss.maxHp * 0.1);
    runTicks(RPG, w, 30, idle, track);
    assert.ok(maxPhase >= 1, 'boss never advanced a phase at low hp');
  }
});

test('elites and group composition vary across seeds and are stable for one seed', async () => {
  const RPG = await loadEngine();
  for (const areaId of ['forest', 'caves']) {
    const comps = new Set(), eliteCounts = new Set(), sizes = new Set();
    for (let s = 0; s < 20; s++) {
      const w = RPG.sim.createWorld({ seed: 500 + s, areaId });
      comps.add(composition(w));
      eliteCounts.add(w.monsters.filter((m) => m.elite).length);
      sizes.add(w.monsters.length);
      const again = RPG.sim.createWorld({ seed: 500 + s, areaId });
      assert.equal(composition(again), composition(w), areaId + ': same seed gave a different layout');
    }
    assert.ok(comps.size >= 15, `${areaId}: only ${comps.size}/20 distinct compositions`);
    assert.ok(eliteCounts.size >= 2, areaId + ': elite count never varied across seeds');
    assert.ok(sizes.size >= 2, areaId + ': monster count never varied across seeds');
  }
  let checked = 0;
  for (let s = 0; s < 40 && checked < 3; s++) {
    const w = RPG.sim.createWorld({ seed: 700 + s, areaId: 'caves' });
    for (const e of w.monsters.filter((m) => m.elite)) {
      assert.ok(e.affixes.length >= 1, 'elite without affix');
      const peer = w.monsters.find((m) => !m.elite && m.defId === e.defId && m.level === e.level);
      if (peer) { assert.ok(e.maxHp > peer.maxHp, 'elite not tougher than peer'); checked++; }
    }
  }
  assert.ok(checked > 0, 'never found an elite with a non-elite peer to compare');
});

test('area transitions: portal walk, gating, boss lock and re-entry', async () => {
  const RPG = await loadEngine();
  const areas = RPG.world.areas;
  const w = RPG.sim.createWorld({ seed: 801, areaId: 'town' });
  godMode(w);
  assert.equal(areas.isUnlocked(w, 'forest'), true);
  assert.equal(areas.isUnlocked(w, 'arena'), false, 'arena must be gated');

  const changed = [];
  const off = RPG.core.events.on(RPG.core.events.NAMES.AREA_CHANGED, (e) => changed.push(e.from + '>' + e.to));
  const exit = w.area.exits.find((e) => e.toArea === 'forest');
  assert.ok(exit, 'town has no exit to the forest');
  w.player.x = exit.x; w.player.y = exit.y;
  RPG.sim.step(w, idle(), DT);
  assert.equal(w.area.id, 'forest', 'standing on the exit did not enter the forest');
  assert.ok(w.monsters.some((m) => !m.dead), 'forest empty after transition');
  assert.deepEqual(changed, ['town>forest']);

  assert.equal(areas.isUnlocked(w, 'caves'), false);
  w.player.level = 4;
  assert.equal(areas.isUnlocked(w, 'caves'), true);
  areas.enter(w, 'caves', 'forest');
  assert.equal(w.area.id, 'caves');

  w.player.level = 6;
  areas.enter(w, 'arena', 'caves');
  assert.ok(w.monsters.find((m) => m.defId === 'warden-of-ash'));
  const back = w.area.exits.find((e) => e.toArea === 'caves');
  assert.ok(back, 'arena has no exit back to the caves');
  w.player.x = back.x; w.player.y = back.y;
  RPG.sim.step(w, idle(), DT);
  assert.equal(w.area.id, 'arena', 'exit must be locked while the boss lives');
  areas.markBossDefeated(w, 'warden-of-ash');
  areas.enter(w, 'arena', 'caves');
  assert.equal(w.monsters.filter((m) => m.defId === 'warden-of-ash' && !m.dead).length, 0, 'defeated boss respawned');
  off();
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG, AREAS_FILES } = require('./load.js');

const RPG = loadRPG(AREAS_FILES);
const { areas } = RPG.world;
const { TILE } = RPG.world.tilemap;

// A world with a player, optionally at a level; `bosses` marks bosses as already defeated.
function mkWorld(seed = 7, level = 1, difficulty = 'normal') {
  const w = RPG.core.state.createWorldState({ seed, difficulty });
  w.player = RPG.sim.player.create(w, 0, 0);
  w.player.level = level;
  return w;
}

// Values built inside the vm realm fail deepEqual against host literals; round-trip to plain data.
const plain = (v) => JSON.parse(JSON.stringify(v));

// Identity of what enter() produced, as plain data, for determinism checks.
function fingerprint(w) {
  return JSON.parse(JSON.stringify({
    tiles: Array.from(w.area.map.tiles), spawn: w.area.spawn, exits: w.area.exits,
    player: [w.player.x, w.player.y],
    monsters: w.monsters.map((m) => [m.defId, m.x, m.y, m.level, m.hp, m.damage, m.elite, m.affixes, m.xp, m.modifiers]),
  }));
}

test('enter is deterministic for the same seed and area, and differs across seeds', () => {
  for (const id of ['town', 'forest', 'caves', 'arena']) {
    const a = mkWorld(11), b = mkWorld(11), c = mkWorld(12);
    areas.enter(a, id); areas.enter(b, id); areas.enter(c, id);
    assert.deepEqual(fingerprint(a), fingerprint(b), id);
    if (id === 'forest' || id === 'caves') assert.notDeepEqual(fingerprint(a), fingerprint(c), id);
  }
});

test('enter does not depend on what area came before it', () => {
  const a = mkWorld(5, 8), b = mkWorld(5, 8);
  areas.enter(a, 'caves');
  areas.enter(b, 'town'); areas.enter(b, 'forest'); areas.enter(b, 'caves', 'forest');
  assert.deepEqual(Array.from(a.area.map.tiles), Array.from(b.area.map.tiles));
  assert.deepEqual(a.monsters.map((m) => [m.defId, m.x, m.y]), b.monsters.map((m) => [m.defId, m.x, m.y]));
});

test('town is safe, other areas spawn monsters whose levels stay in the area range', () => {
  const w = mkWorld(3);
  areas.enter(w, 'town');
  assert.equal(w.monsters.length, 0);
  assert.deepEqual([w.player.x, w.player.y], [w.area.spawn.x, w.area.spawn.y]);
  for (const id of ['forest', 'caves']) {
    areas.enter(w, id);
    const [lo, hi] = RPG.data.areas[id].level;
    assert.ok(w.monsters.length > 0, id);
    for (const m of w.monsters) {
      assert.ok(m.level >= lo && m.level <= hi, `${id} ${m.defId} level ${m.level}`);
      assert.ok(m.xp > 0);
      assert.equal(m.areaLevel, m.level);
    }
  }
});

test('monster levels and counts scale along area order and with difficulty', () => {
  const meanLevel = (id) => {
    let sum = 0, n = 0;
    for (let seed = 1; seed <= 20; seed++) {
      const w = mkWorld(seed);
      areas.enter(w, id);
      for (const m of w.monsters) { sum += m.level; n++; }
    }
    return sum / n;
  };
  assert.ok(meanLevel('forest') < meanLevel('caves'));
  assert.ok(meanLevel('caves') < RPG.data.areas.arena.level[1]);

  // Same monster def gets tougher in a deeper area: skitter lives in forest and caves.
  const hpOf = (areaLevel) => {
    const w = mkWorld(1);
    areas.enter(w, 'forest');
    const def = RPG.data.monsters.skitter;
    const f = RPG.world.difficulty.scaleMonster(def, 1, RPG.world.difficulty.resolve('normal'), areaLevel);
    return def.baseStats.hp * f.hp;
  };
  assert.ok(hpOf(5) >= hpOf(2));

  const countAt = (diff) => {
    let n = 0;
    for (let seed = 1; seed <= 20; seed++) {
      const w = mkWorld(seed, 1, diff);
      areas.enter(w, 'caves');
      n += w.monsters.length;
    }
    return n;
  };
  assert.ok(countAt('hard') > countAt('easy'), 'harder difficulty spawns more');

  // Counts scale along order: the caves budget is larger than the forest budget.
  const total = (id) => {
    let n = 0;
    for (let seed = 1; seed <= 20; seed++) { const w = mkWorld(seed); areas.enter(w, id); n += w.monsters.length; }
    return n;
  };
  assert.ok(total('caves') > total('forest') * 0.9);

  // Player level raises monster hp (difficulty.scaleMonster playerScale).
  const lowW = mkWorld(4, 1), highW = mkWorld(4, 10);
  areas.enter(lowW, 'forest'); areas.enter(highW, 'forest');
  assert.equal(lowW.monsters.length, highW.monsters.length);
  assert.ok(highW.monsters[0].maxHp > lowW.monsters[0].maxHp);
});

test('the arena holds one boss with modifiers and none once it is defeated', () => {
  const w = mkWorld(9, 8);
  areas.enter(w, 'arena');
  const bosses = w.monsters.filter((m) => m.defId === 'warden-of-ash');
  assert.equal(bosses.length, 1);
  assert.ok(bosses[0].modifiers.length >= 1);
  assert.deepEqual([bosses[0].x, bosses[0].y], [w.area.bossSlot.x, w.area.bossSlot.y]);
  assert.equal(bosses[0].level, RPG.data.areas.arena.level[1]);

  areas.markBossDefeated(w, 'warden-of-ash');
  assert.equal(w.progress.bossesDefeated['warden-of-ash'], true);
  areas.enter(w, 'arena');
  assert.equal(w.monsters.length, 0);
});

test('entering clears old entity lists, emits AREA_CHANGED and marks the area visited', () => {
  const w = mkWorld(2, 8);
  areas.enter(w, 'forest');
  const seen = [];
  const off = RPG.core.events.on('areaChanged', (e) => seen.push(e));
  RPG.world.spawnProjectile(w, { x: 1, y: 1, angle: 0, speed: 1, team: 0 });
  RPG.world.spawnEffect(w, { kind: 'ring', x: 1, y: 1, life: 1 });
  RPG.world.spawnItemDrop(w, { id: 'x' }, 1, 1);
  w.telegraphs = [{}]; w.enemyProjectiles = [{}];
  const oldIds = new Set(w.monsters.map((m) => m.id));
  areas.enter(w, 'caves', 'forest');
  off();
  assert.deepEqual(plain(seen), [{ from: 'forest', to: 'caves' }]);
  assert.equal(w.projectiles.length, 0);
  assert.equal(w.effects.length, 0);
  assert.equal(w.drops.length, 0);
  assert.equal(w.telegraphs.length, 0);
  assert.equal(w.enemyProjectiles.length, 0);
  assert.ok(w.monsters.every((m) => !oldIds.has(m.id)));
  assert.equal(w.area.id, 'caves');
  assert.equal(w.area.visited.forest, true);
  assert.equal(w.area.visited.caves, true);
});

test('arrival is one tile off the portal back, on a walkable tile that does not re-trigger the exit', () => {
  const w = mkWorld(6, 8);
  areas.enter(w, 'town');
  areas.enter(w, 'forest', 'town');
  const back = w.area.exits.find((e) => e.toArea === 'town');
  const ts = w.area.map.tileSize;
  const dist = Math.hypot(w.player.x - back.x, w.player.y - back.y);
  assert.ok(dist >= ts * 0.9 && dist <= ts * 1.5, `distance ${dist}`);
  assert.equal(RPG.world.tilemap.solidAtPx(w.area.map, w.player.x, w.player.y), false);
  assert.equal(areas.checkExits(w), null);
  assert.equal(w.area.id, 'forest');
});

test('checkExits respects isUnlocked and the boss lock', () => {
  const w = mkWorld(8, 1);
  areas.enter(w, 'town');
  const toForest = () => w.area.exits.find((e) => e.toArea === 'forest');
  w.player.x = toForest().x; w.player.y = toForest().y;
  assert.equal(areas.checkExits(w), 'forest');
  assert.equal(w.area.id, 'forest');

  // Level 1 cannot use the forest -> caves exit (requires level 4).
  const caves = w.area.exits.find((e) => e.toArea === 'caves');
  assert.equal(areas.isUnlocked(w, 'caves'), false);
  w.player.x = caves.x; w.player.y = caves.y;
  assert.equal(areas.checkExits(w), null);
  assert.equal(w.area.id, 'forest');
  w.player.level = 4;
  assert.equal(areas.isUnlocked(w, 'caves'), true);
  assert.equal(areas.checkExits(w), 'caves');

  // Arena needs level 6; the arena is not reachable by skipping caves.
  const fresh = mkWorld(8, 6);
  assert.equal(areas.isUnlocked(fresh, 'arena'), true);
  assert.equal(areas.isUnlocked(mkWorld(8, 5), 'arena'), false);
  assert.equal(areas.isUnlocked(fresh, 'nope'), false);

  // Boss alive locks the arena exit; defeating it opens it.
  const b = mkWorld(8, 8);
  areas.enter(b, 'arena', 'caves');
  const exit = b.area.exits[0];
  b.player.x = exit.x; b.player.y = exit.y;
  assert.equal(areas.checkExits(b), null);
  areas.markBossDefeated(b, 'warden-of-ash');
  assert.equal(areas.checkExits(b), 'caves');
});

test('restorePos keeps a walkable position and snaps a wall position to a walkable tile', () => {
  const w = mkWorld(4);
  areas.enter(w, 'forest');
  const map = w.area.map, ts = map.tileSize;
  const ok = { x: w.area.spawn.x, y: w.area.spawn.y };
  areas.enter(w, 'forest', null, { restorePos: ok });
  assert.deepEqual([w.player.x, w.player.y], [ok.x, ok.y]);

  const wall = { x: 0.5 * ts, y: 0.5 * ts }; // map corner is always wall
  assert.equal(RPG.world.tilemap.solidAtPx(map, wall.x, wall.y), true);
  areas.enter(w, 'forest', null, { restorePos: wall });
  const tx = Math.floor(w.player.x / ts), ty = Math.floor(w.player.y / ts);
  assert.equal(RPG.world.tilemap.isSolid(w.area.map, tx, ty), false);
  assert.notEqual(w.area.map.tiles[ty * map.w + tx], TILE.PORTAL);

  // Out of bounds also lands on a walkable tile; restore is silent (no AREA_CHANGED).
  const seen = [];
  const off = RPG.core.events.on('areaChanged', (e) => seen.push(e));
  areas.enter(w, 'forest', null, { restorePos: { x: -500, y: 99999 } });
  off();
  assert.equal(RPG.world.tilemap.solidAtPx(w.area.map, w.player.x, w.player.y), false);
  assert.equal(seen.length, 0);
  assert.equal(areas.restorePos(w, { x: ok.x, y: ok.y }).x, ok.x);
});

test('cleared slots stay empty on re-entry and the other slots keep their monsters', () => {
  const w = mkWorld(13);
  areas.enter(w, 'forest');
  const before = w.monsters.map((m) => ({ id: m.id, slot: w.area.slotOf[m.id], defId: m.defId, x: m.x, y: m.y }));
  const slot = before[0].slot;
  const inSlot = w.monsters.filter((m) => w.area.slotOf[m.id] === slot);
  let cleared = false;
  for (const m of inSlot) { m.dead = true; cleared = areas.onMonsterDead(w, m); }
  assert.equal(cleared, true);
  assert.deepEqual(plain(w.cleared.forest), [slot]);
  assert.equal(areas.onMonsterDead(w, inSlot[0]), false, 'a second report is ignored');

  areas.enter(w, 'forest', null, { silent: true });
  const expected = before.filter((b) => b.slot !== slot).map((b) => [b.defId, b.x, b.y]);
  assert.deepEqual(w.monsters.map((m) => [m.defId, m.x, m.y]), expected);
});

test('areas.enter accepts the snapshot call shape and rejects unknown areas', () => {
  const w = mkWorld(1);
  areas.enter(w, 'town');
  areas.enter(w, 'town');
  assert.equal(w.area.id, 'town');
  assert.throws(() => areas.enter(w, 'nowhere'), /unknown area/);
});

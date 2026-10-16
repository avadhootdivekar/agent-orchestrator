'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
// Uses the shared world loader directly, as the monsters and items tests do; tests/data/load.js only re-exports it.
const { loadRPG } = require('../world/load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'data/monsters.js', 'data/areas.js']);
const AREAS = RPG.data.areas;
const MONSTERS = RPG.data.monsters;
const ORDERED = Object.values(AREAS).sort((a, b) => a.order - b.order);

// Objects built inside the vm realm fail deepStrictEqual against host literals; round-trip to plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));
// A fresh, mutable copy of the shipped areas for negative tests.
const copyAreas = () => plain(AREAS);
const validate = (areas) => plain(areas ? AREAS.validate(areas, MONSTERS) : AREAS.validate());

test('four v1 areas exist in order: town, forest, caves, arena', () => {
  assert.deepEqual(ORDERED.map((a) => a.id), ['town', 'forest', 'caves', 'arena']);
  assert.deepEqual(ORDERED.map((a) => a.kind), ['authored', 'open', 'cave', 'authored']);
});

test('level ranges escalate strictly along the area order', () => {
  for (let i = 1; i < ORDERED.length; i++) {
    const prev = ORDERED[i - 1];
    const cur = ORDERED[i];
    assert.ok(cur.level[0] > prev.level[0], `${cur.id} min level beats ${prev.id}`);
    assert.ok(cur.level[1] > prev.level[1], `${cur.id} max level beats ${prev.id}`);
  }
});

test('shipped area data validates with no problems', () => {
  assert.deepEqual(validate(), []);
});

test('town is safe and spawns nothing; the other areas have spawn tables', () => {
  assert.equal(AREAS.town.safe, true);
  assert.deepEqual(plain(AREAS.town.spawnTable), []);
  for (const id of ['forest', 'caves']) {
    assert.equal(AREAS[id].safe, false);
    assert.ok(AREAS[id].spawnTable.length > 0, `${id} has spawns`);
  }
});

test('boss arena holds the existing Warden of Ash boss in its centre slot', () => {
  assert.deepEqual(plain(AREAS.arena.boss), { bossId: 'warden-of-ash', slot: 'arena-center' });
  assert.equal(MONSTERS['warden-of-ash'].archetype, 'boss');
});

test('every spawn entry names a real monster that is allowed in that area', () => {
  for (const area of ORDERED) {
    for (const entry of area.spawnTable) {
      assert.ok(MONSTERS[entry.monsterId], `${area.id} -> ${entry.monsterId} exists`);
      assert.ok(MONSTERS[entry.monsterId].areas.includes(area.id), `${entry.monsterId} allowed in ${area.id}`);
    }
  }
});

test('authored layouts are rectangular, match size and contain a spawn tile', () => {
  for (const area of ORDERED.filter((a) => a.kind === 'authored')) {
    const widths = new Set(area.layout.map((row) => row.length));
    assert.deepEqual([...widths], [area.size[0]], `${area.id} row width`);
    assert.equal(area.layout.length, area.size[1], `${area.id} row count`);
    assert.ok(area.layout.some((row) => row.includes('S')), `${area.id} has a spawn tile`);
  }
});

test('validate() exposes no enumerable helpers, so iterating areas yields definitions only', () => {
  assert.deepEqual(Object.keys(AREAS).sort(), ['arena', 'caves', 'forest', 'town']);
  assert.equal(typeof AREAS.validate, 'function');
});

test('definitions and the registry are frozen', () => {
  assert.ok(Object.isFrozen(AREAS));
  assert.ok(Object.isFrozen(AREAS.forest));
  assert.ok(Object.isFrozen(AREAS.forest.spawnTable));
});

test('validate() rejects a spawn entry naming an unknown monster', () => {
  const bad = copyAreas();
  bad.forest.spawnTable.push({ monsterId: 'no-such-monster', weight: 1, min: 1, max: 1 });
  const errors = validate(bad);
  assert.ok(errors.some((e) => e.includes("unknown monster 'no-such-monster'")), errors.join('\n'));
});

test('validate() rejects a monster placed in an area it is not allowed in', () => {
  const bad = copyAreas();
  bad.forest.spawnTable.push({ monsterId: 'stone-brute', weight: 1, min: 1, max: 1 });
  const errors = validate(bad);
  assert.ok(errors.some((e) => e.includes("'stone-brute' is not allowed in this area")), errors.join('\n'));
});

test('validate() rejects an exit to an area that does not exist', () => {
  const bad = copyAreas();
  bad.town.exits.push({ to: 'nowhere' });
  const errors = validate(bad);
  assert.ok(errors.some((e) => e.includes("exit to unknown area 'nowhere'")), errors.join('\n'));
});

test('validate() rejects an exit that points back to its own area', () => {
  const bad = copyAreas();
  bad.forest.exits.push({ to: 'forest' });
  assert.ok(validate(bad).some((e) => e.includes('exit points back to itself')));
});

test('validate() rejects an exit requiring a boss that is not a boss', () => {
  const bad = copyAreas();
  bad.caves.exits[1].requires = { bossesDefeated: ['husk-grunt'] };
  assert.ok(validate(bad).some((e) => e.includes("exit requires unknown boss 'husk-grunt'")));
});

test('validate() rejects a non-rectangular authored layout', () => {
  const bad = copyAreas();
  bad.town.layout[3] = bad.town.layout[3] + '.';
  const errors = validate(bad);
  assert.ok(errors.some((e) => e.includes('layout row 3 has')), errors.join('\n'));
});

test('validate() rejects an authored layout with no spawn tile', () => {
  const bad = copyAreas();
  bad.arena.layout = bad.arena.layout.map((row) => row.replace('S', '.'));
  assert.ok(validate(bad).some((e) => e.includes("layout has no spawn tile 'S'")));
});

test('validate() rejects a layout whose size disagrees with its rows', () => {
  const bad = copyAreas();
  bad.arena.size = [24, 17];
  assert.ok(validate(bad).some((e) => e.includes('does not match layout')));
});

test('validate() rejects a layout glyph outside the tilemap set', () => {
  const bad = copyAreas();
  bad.arena.layout[5] = bad.arena.layout[5].slice(0, 5) + '?' + bad.arena.layout[5].slice(6);
  assert.ok(validate(bad).some((e) => e.includes("layout glyph '?'")));
});

test('validate() rejects a boss arena whose centre tile is a wall', () => {
  const bad = copyAreas();
  const mid = Math.floor(bad.arena.layout.length / 2);
  const row = bad.arena.layout[mid];
  const cx = Math.floor(row.length / 2);
  bad.arena.layout[mid] = row.slice(0, cx) + '#' + row.slice(cx + 1);
  assert.ok(validate(bad).some((e) => e.includes('boss centre tile is a wall')));
});

test('validate() rejects a boss id that is not a boss monster', () => {
  const bad = copyAreas();
  bad.arena.boss.bossId = 'husk-grunt';
  assert.ok(validate(bad).some((e) => e.includes("boss 'husk-grunt' is not a boss monster")));
});

test('validate() rejects a level range that does not escalate', () => {
  const bad = copyAreas();
  bad.caves.level = [2, 6];
  assert.ok(validate(bad).some((e) => e.includes('does not escalate from forest')));
});

test('validate() rejects a safe area that carries spawns', () => {
  const bad = copyAreas();
  bad.town.spawnTable.push({ monsterId: 'husk-grunt', weight: 1, min: 1, max: 1 });
  assert.ok(validate(bad).some((e) => e.includes('safe area must not spawn monsters')));
});

test('validate() rejects a key that disagrees with the definition id', () => {
  const bad = copyAreas();
  bad.forest.id = 'woods';
  assert.ok(validate(bad).some((e) => e.includes("id field is 'woods'")));
});

test('validate() rejects malformed palette colours and decor density', () => {
  const badPalette = copyAreas();
  badPalette.caves.palette.wall = 'grey';
  assert.ok(validate(badPalette).some((e) => e.includes('palette needs hex colours')));

  const badDecor = copyAreas();
  badDecor.forest.decor[0].density = 2;
  assert.ok(validate(badDecor).some((e) => e.includes('decor entry')));
});

test('validate() rejects a non-authored area that carries a layout', () => {
  const bad = copyAreas();
  bad.forest.layout = ['#.#'];
  assert.ok(validate(bad).some((e) => e.includes('only authored areas carry a layout')));
});

'use strict';
// Difficulty enemySpeed must change how far monsters actually walk (A4.3), at spawn and after a runtime change.
// Measured through the real sim pipeline: distance covered by a lone chaser per second of simulated time.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine } = require('../integration/helpers.js');

const DT = 1 / 60;
const TICKS = 30;                       // half a second: a chaser starting 150 px away is still closing in at the end
const START_GAP = 150;
const OPEN_ROWS = Array.from({ length: 30 }, (_, y) => (y === 0 || y === 29 ? '#'.repeat(40) : '#' + '.'.repeat(38) + '#'));
const IDLE = { move: { x: 0, y: 0 }, aim: { x: 1, y: 0 }, attack: false, skills: [], dash: false, toggles: {} };

// A world in an open room holding exactly one non-elite monster of the given ai, 150 px from a stationary player.
function loneChaser(RPG, difficulty, ai, seed) {
  for (let s = seed; s < seed + 200; s++) {
    const w = RPG.sim.createWorld({ seed: s, areaId: ['forest', 'caves'][s % 2], difficulty });
    const pick = w.monsters.find((m) => !m.elite && RPG.data.monsters[m.defId].ai === ai);
    if (!pick) continue;
    for (const m of w.monsters) if (m !== pick) m.dead = true;
    w.area.map = RPG.world.tilemap.fromRows(OPEN_ROWS, 32);
    w.player.x = 600; w.player.y = 400;
    w.player.hp = w.player.maxHp = 1e9; w.player.stats.maxHp = 1e9;
    pick.x = w.player.x - START_GAP; pick.y = w.player.y;
    pick.prevX = pick.x; pick.prevY = pick.y;
    return { w, m: pick };
  }
  throw new Error('no ' + ai + ' monster found');
}

function travelled(RPG, w, m) {
  const x0 = m.x, y0 = m.y;
  for (let i = 0; i < TICKS; i++) RPG.sim.step(w, IDLE, DT);
  return Math.hypot(m.x - x0, m.y - y0) / (TICKS * DT);
}

test('a monster covers measurably more ground per second on Nightmare than on Easy', async () => {
  const RPG = await loadEngine();
  const d = RPG.data.difficulties;
  for (const ai of ['melee', 'flanker', 'tank']) {
    const easy = loneChaser(RPG, 'easy', ai, 1), night = loneChaser(RPG, 'nightmare', ai, 1);
    assert.equal(easy.m.defId, night.m.defId);
    const base = RPG.data.monsters[easy.m.defId].baseStats.speed;
    const vEasy = travelled(RPG, easy.w, easy.m), vNight = travelled(RPG, night.w, night.m);
    assert.ok(vNight > vEasy * 1.2, `${ai}: nightmare ${vNight.toFixed(1)} px/s vs easy ${vEasy.toFixed(1)} px/s`);
    // Walking speed follows the multiplier: ratio of distances equals the ratio of enemySpeed (within a tick of slack).
    assert.ok(Math.abs(vNight / vEasy - d.nightmare.enemySpeed / d.easy.enemySpeed) < 0.03, `${ai}: ratio ${vNight / vEasy}`);
    assert.ok(vEasy <= base * d.easy.enemySpeed * 1.01, `${ai}: easy speed exceeds scaled def speed`);
  }
});

test('normal difficulty leaves movement at the def speed', async () => {
  const RPG = await loadEngine();
  const { w, m } = loneChaser(RPG, 'normal', 'melee', 1);
  const base = RPG.data.monsters[m.defId].baseStats.speed;
  assert.ok(Math.abs(travelled(RPG, w, m) - base) < base * 0.05);
});

test('a runtime difficulty change speeds up or slows down monsters that are already alive', async () => {
  const RPG = await loadEngine();
  const a = loneChaser(RPG, 'normal', 'melee', 1);
  const b = loneChaser(RPG, 'normal', 'melee', 1);
  RPG.world.areas.rescaleMonsters(b.w, 'nightmare');
  b.w.difficulty = RPG.world.difficulty.resolve('nightmare');
  const vNormal = travelled(RPG, a.w, a.m), vNight = travelled(RPG, b.w, b.m);
  assert.ok(Math.abs(vNight / vNormal - RPG.data.difficulties.nightmare.enemySpeed) < 0.03, `ratio ${vNight / vNormal}`);
  RPG.world.areas.rescaleMonsters(b.w, 'easy');
  const c = loneChaser(RPG, 'normal', 'melee', 1);
  const vEasy = travelled(RPG, b.w, b.m), vNormal2 = travelled(RPG, c.w, c.m);
  assert.ok(vEasy < vNormal2, `easy ${vEasy} should be slower than normal ${vNormal2}`);
});

test('same seed and difficulty give the same movement (determinism)', async () => {
  const RPG = await loadEngine();
  const run = () => {
    const { w, m } = loneChaser(RPG, 'hard', 'flanker', 5);
    for (let i = 0; i < 120; i++) RPG.sim.step(w, IDLE, DT);
    return [m.x, m.y];
  };
  assert.deepEqual(run(), run());
});

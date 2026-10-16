'use strict';

// w09-01 hardening: bounded world.effects, hit-flash rest window, explosive-death cue, boss damage floor.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('../world/load.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js', 'core/state.js',
  'data/difficulties.js', 'data/monsters.js', 'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/spatial.js',
  'world/combat.js', 'world/ai.js', 'world/boss.js', 'world/world.js', 'world/encounters.js', 'data/maps/test-map.js',
  'sim/player.js', 'sim/combat-step.js', 'sim/sim.js', 'sim/monsters.js',
]);
const DT = 1 / 60;
const ROWS = Array.from({ length: 30 }, (_, y) => (y === 0 || y === 29 ? '#'.repeat(40) : '#' + '.'.repeat(38) + '#'));

function makeWorld() {
  const w = RPG.sim.createWorld({ seed: 7 });
  w.area.map = RPG.world.tilemap.fromRows(ROWS, 32);
  w.player.x = 600; w.player.y = 400;
  w.player.hp = w.player.maxHp = 100000;
  w.scratch = { dt: DT };
  return w;
}

test('world.effects is capped: a flood of effects is trimmed to MAX_EFFECTS by cleanup, oldest first', () => {
  const w = makeWorld();
  const max = RPG.world.MAX_EFFECTS;
  assert.ok(max > 0 && max <= 128);
  const first = RPG.world.spawnEffect(w, { type: 'aoe', x: 1, y: 1, r: 5, duration: 99 });
  for (let i = 0; i < max * 2; i++) RPG.world.spawnEffect(w, { type: 'aoe', x: i, y: 1, r: 5, duration: 99 });
  const total = w.effects.length;
  assert.equal(total, max * 2 + 1);
  RPG.sim.stage.cleanup(w, w.scratch);
  assert.equal(w.effects.length, max);
  assert.equal(w.effects.includes(first), false, 'the oldest effect is the one retired');
  for (let i = 0; i < 600; i++) {
    RPG.world.spawnEffect(w, { type: 'aoe', x: i, y: 2, r: 5, duration: 99 });
    RPG.sim.stage.cleanup(w, w.scratch);
    assert.ok(w.effects.length <= max, `tick ${i}: ${w.effects.length}`);
  }
});

test('hit flash cannot strobe: rapid hits flash at most once per rest window', () => {
  const c = RPG.world.combat;
  const w = makeWorld();
  const m = RPG.world.spawnMonster(w, 'husk-grunt', 100, 400);
  let rises = 0, wasOn = false, secs = 3;
  const result = { dealt: 0, knockback: null, statuses: [], lifeSteal: 0 };
  for (let t = 0; t < secs; t += DT) {
    c.applyHit(null, m, result);            // an AoE hitting every single tick
    const on = m.flash > 0;
    if (on && !wasOn) rises++;
    wasOn = on;
    c.tickFlash(m, DT);
  }
  const hz = rises / secs;
  assert.ok(hz <= 3.05, `flash rate ${hz} Hz`);
  assert.ok(rises >= 2, 'the sprite still flashes');
  // After the rest window a fresh hit flashes again.
  for (let i = 0; i < 60; i++) c.tickFlash(m, DT);
  c.applyHit(null, m, result);
  assert.ok(m.flash > 0);
});

test('explosive-death blast spawns a ring cue, a sound, and the cue expires', () => {
  const w = makeWorld();
  const m = RPG.world.spawnMonster(w, 'husk-grunt', 580, 400, { elite: true, affixes: ['explosive-death'] });
  const sounds = [];
  const bus = RPG.core.events;
  const off = bus.on(bus.NAMES.UI_SOUND, (p) => sounds.push(p.name));
  m.dead = true;
  RPG.sim.stage.affixes(w, w.scratch);
  if (typeof off === 'function') off();
  const cue = w.effects.find((e) => e.type === 'blast');
  assert.ok(cue, 'blast effect exists');
  assert.equal(cue.r, RPG.data.eliteAffixes['explosive-death'].deathRadius);
  assert.deepEqual(sounds, ['hit']);
  RPG.sim.stage.affixes(w, w.scratch);
  assert.equal(w.effects.filter((e) => e.type === 'blast').length, 1, 'fires once');
  for (let i = 0; i < 60; i++) { RPG.sim.stage.projectiles(w, w.scratch); RPG.sim.stage.cleanup(w, w.scratch); }
  assert.equal(w.effects.some((e) => e.type === 'blast'), false, 'cue ages out');
});

test('boss damageTakenMult below 1 is floored; 0 stays an explicit immunity', () => {
  const boss = RPG.world.boss;
  const def = { phases: [{ damageTakenMult: 0.001 }, { damageTakenMult: 0 }, { damageTakenMult: 0.5 }, { damageTakenMult: 2 }] };
  const b = { def, ai: { phase: 0 } };
  const at = (phase) => { b.ai.phase = phase; return boss.damageTakenMult(b); };
  b.defId = undefined;
  assert.equal(at(0), boss.MIN_DAMAGE_TAKEN_MULT);
  assert.equal(at(1), 0);
  assert.equal(at(2), 0.5);
  assert.equal(at(3), 2);
});

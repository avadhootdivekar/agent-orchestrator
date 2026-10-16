'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/rng.js', 'data/monsters.js', 'world/ai.js', 'world/boss.js']);
const { ai, boss: bossSys } = RPG.world;
const { Rng } = RPG.core;
const DT = 0.05;

function makeBoss(hpFrac = 1) {
  const def = RPG.data.monsters['warden-of-ash'];
  return { defId: 'warden-of-ash', x: 0, y: 0, hp: def.baseStats.hp * hpFrac, maxHp: def.baseStats.hp, dead: false };
}
function makeWorld(players) {
  const spawned = [];
  return {
    effects: [], players, spawned, phases: [],
    spawnMonster(defId, x, y) { const m = { defId, x, y, hp: 1, maxHp: 1 }; spawned.push(m); return m; },
    onPhaseChange(b, from, to, special) { this.phases.push({ from, to, special }); },
  };
}
// Drives AI + boss system together. `onStep(t, intent)` lets a test move players. Returns every action with its time.
function sim(world, b, secs, opts = {}) {
  const rng = Rng.create(7), log = [];
  const view = { target: world.players[0] };
  for (let t = 0, i = 0; t < secs; t += DT, i++) {
    const it = ai.think(b, view, rng, DT);
    b.x += it.vx * DT; b.y += it.vy * DT;
    bossSys.update(world, b, it, DT);
    it.actions.forEach((a) => log.push({ t, ...a }));
    if (opts.onStep) opts.onStep(t, it, log);
    if (opts.until && opts.until(log)) break;
  }
  return log;
}
const first = (log, type) => log.find((a) => a.type === type);

test('circle damage lands only after the telegraph delay', () => {
  const p = { x: 200, y: 0, hp: 1000 };
  const w = makeWorld([p]), b = makeBoss();
  let hurtAt = null;
  w.damagePlayer = (pl, amt) => { pl.hp -= amt; if (hurtAt === null) hurtAt = t; };
  let t = 0;
  const log = sim(w, b, 10, {
    onStep(tt, it, lg) { t = tt; },
    until: () => hurtAt !== null,
  });
  const tg = first(log, 'telegraph');
  assert.ok(tg && hurtAt !== null);
  assert.ok(hurtAt - tg.t >= tg.duration - DT, 'damage came ' + (hurtAt - tg.t) + 's after telegraph, wanted ' + tg.duration);
  assert.ok(tg.duration >= 0.8);
});

test('a player standing outside the radius takes no damage; dodging works', () => {
  const def = RPG.data.monsters['warden-of-ash'];
  const stay = { x: 150, y: 0, hp: 1000 }, dodge = { x: 150, y: 0, hp: 1000 };
  // Same boss behaviour for both (same seed); the dodger leaves the circle once the warning is up.
  for (const [p, dodges] of [[stay, false], [dodge, true]]) {
    p.hp = 1000;
    const w = makeWorld([p]), b = makeBoss();
    sim(w, b, 3, {
      onStep(t, it, log) {
        const tg = first(log, 'telegraph');
        if (dodges && tg) { p.x = tg.x + tg.radius + 20; p.y = tg.y; }
      },
    });
  }
  assert.ok(stay.hp < 1000, 'standing player is hit');
  assert.equal(dodge.hp, 1000, 'dodging player is never hit');
  assert.ok(def.attacks.every((a) => a.windup >= 0.8));
});

test('circle hits inside the radius, not just outside it', () => {
  const w = makeWorld([{ x: 0, y: 0, hp: 100 }, { x: 0, y: 0, hp: 100 }]), b = makeBoss();
  w.players[1].x = 1000;
  const E = { type: 'telegraph', attackId: 'ember-circles', shape: 'circle', x: 300, y: 0, dirX: 1, dirY: 0, radius: 90, range: 0, duration: 1.2 };
  w.players[0].x = 389; w.players[1].x = 391;
  bossSys.update(w, b, { actions: [E] }, 0);
  bossSys.update(w, b, { actions: [] }, 1.2);
  bossSys.update(w, b, { actions: [{ type: 'attack', attackId: 'ember-circles', kind: 'aoe', x: 300, y: 0, radius: 90, range: 0, damageMult: 1.6 }] }, 0);
  assert.ok(w.players[0].hp < 100);
  assert.equal(w.players[1].hp, 100);
});

test('an attack with no telegraph never deals damage', () => {
  const p = { x: 0, y: 0, hp: 100 }, w = makeWorld([p]), b = makeBoss();
  bossSys.update(w, b, { actions: [{ type: 'attack', attackId: 'ember-circles', kind: 'aoe', x: 0, y: 0, radius: 90, range: 0, damageMult: 1.6 }] }, DT);
  assert.equal(p.hp, 100);
});

test('phase thresholds fire exactly once each, through hook and world callback', () => {
  const p = { x: 100, y: 0, hp: 1e9 }, w = makeWorld([p]), b = makeBoss();
  const seen = [];
  const off = bossSys.onPhaseChange((bb, from, to, special) => seen.push([from, to, special]));
  sim(w, b, 1);
  b.hp = b.maxHp * 0.6; sim(w, b, 3);
  b.hp = b.maxHp * 0.6; sim(w, b, 1);          // same HP again: nothing re-fires
  b.hp = b.maxHp * 0.3; sim(w, b, 3);
  b.hp = b.maxHp * 0.1; sim(w, b, 3);
  off();
  assert.deepEqual(seen, [[0, 1, 'adds'], [1, 2, 'enrage']]);
  assert.equal(w.phases.length, 2);
  // A duplicate phase intent is also ignored.
  bossSys.update(w, b, { actions: [{ type: 'phase', from: 0, to: 1, special: 'adds' }] }, DT);
  assert.equal(w.phases.length, 2);
});

test('summoned adds are real monster ids', () => {
  const p = { x: 100, y: 0, hp: 1e9 }, w = makeWorld([p]), b = makeBoss(0.5);
  sim(w, b, 40, { until: () => w.spawned.length > 0 });
  assert.ok(w.spawned.length > 0, 'the add wave was summoned');
  w.spawned.forEach((m) => assert.ok(RPG.data.monsters[m.defId], m.defId));
  bossSys.update(w, b, { actions: [{ type: 'summon', attackId: 'x', defId: 'not-a-monster', count: 2 }] }, DT);
  w.spawned.forEach((m) => assert.ok(RPG.data.monsters[m.defId]));
});

test('ember circles leave burning ground that ticks every tickEvery', () => {
  const p = { x: 0, y: 0, hp: 1e6 }, w = makeWorld([p]), b = makeBoss();
  const hits = [];
  w.damagePlayer = (pl, amt, info) => { if (info.zone) hits.push(amt); };
  const tg = { type: 'telegraph', attackId: 'ember-circles', shape: 'circle', x: 500, y: 500, dirX: 1, dirY: 0, radius: 90, range: 0, duration: 1.2 };
  const atk = { type: 'attack', attackId: 'ember-circles', kind: 'aoe', x: 500, y: 500, radius: 90, range: 0, damageMult: 1.6, ember: 4 };
  bossSys.update(w, b, { actions: [tg] }, DT);
  bossSys.update(w, b, { actions: [] }, 1.2);
  bossSys.update(w, b, { actions: [atk] }, 0);
  const zone = w.effects.find((e) => e.kind === 'zone');
  assert.ok(zone && zone.tickEvery > 0);
  for (let i = 0; i < 100; i++) bossSys.update(w, b, { actions: [] }, DT);   // 5s elapsed; player is away
  assert.equal(hits.length, 0);
  assert.equal(w.effects.some((e) => e.kind === 'zone'), false, 'zone expired');
  // A player standing in the fire is ticked ~ once per tickEvery for the duration.
  bossSys.update(w, b, { actions: [tg] }, DT);
  bossSys.update(w, b, { actions: [] }, 1.2);
  p.x = 500; p.y = 500;
  bossSys.update(w, b, { actions: [atk] }, 0);
  for (let i = 0; i < 100; i++) bossSys.update(w, b, { actions: [] }, DT);
  assert.ok(hits.length >= 3 && hits.length <= 4, 'ticks: ' + hits.length);
});

test('every third ember circle opens a vulnerable window that then closes', () => {
  const p = { x: 300, y: 0, hp: 1e9 }, w = makeWorld([p]), b = makeBoss();
  const log = sim(w, b, 60, { until: (l) => l.filter((a) => a.type === 'attack' && a.vulnerable).length >= 1 });
  const bright = log.filter((a) => a.type === 'attack' && a.vulnerable);
  assert.equal(bright.length, 1);
  assert.equal(bossSys.damageTakenMult(b), bossSys.VULNERABLE_MULT);
  for (let i = 0; i < 60; i++) bossSys.update(w, b, { actions: [] }, DT);   // 3s > 2s window
  assert.equal(bossSys.damageTakenMult(b), 1);
});

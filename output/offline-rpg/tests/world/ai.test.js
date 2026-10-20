'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/rng.js', 'data/monsters.js', 'world/ai.js']);
const { ai } = RPG.world;
const { Rng } = RPG.core;
const DT = 0.05;

function mon(defId, x, y, hpFrac = 1) {
  const def = RPG.data.monsters[defId];
  const maxHp = def.baseStats.hp;
  return { defId, x, y, hp: maxHp * hpFrac, maxHp, dead: false };
}
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

// Steps the AI, applying velocity, until `until` is true or `secs` elapse. Returns every action seen, tagged with time.
function run(m, view, rng, secs, opts = {}) {
  const log = [];
  for (let t = 0; t < secs; t += DT) {
    const it = ai.think(m, view, rng, DT);
    m.x += it.vx * DT; m.y += it.vy * DT;
    if (opts.onStep) opts.onStep(m, it, t);
    it.actions.forEach((a) => log.push({ t, ...a }));
    if (opts.until && opts.until(m, log)) break;
  }
  return log;
}
const ofType = (log, type) => log.filter((a) => a.type === type);

test('melee chases then telegraphs before striking', () => {
  const m = mon('husk-grunt', 0, 0), view = { target: { x: 150, y: 0 } };
  const log = run(m, view, Rng.create(1), 6);
  const atk = ofType(log, 'attack')[0], tel = ofType(log, 'telegraph')[0];
  assert.ok(atk && tel, 'attacks');
  assert.ok(tel.t < atk.t && atk.t - tel.t >= 0.3);
  assert.ok(dist(m, view.target) <= 36, 'closed to attack range');
});

test('melee ignores a target outside aggro range and drops it past leash', () => {
  const m = mon('husk-grunt', 0, 0), view = { target: { x: 300, y: 0 } };
  assert.equal(run(m, view, Rng.create(1), 2).length, 0);
  assert.equal(m.x, 0);
  view.target.x = 100; run(m, view, Rng.create(1), 0.2);
  assert.ok(m.x > 0, 'aggro latched');
  view.target.x = 5000; const x = m.x; run(m, view, Rng.create(1), 0.5);
  assert.equal(m.x, x, 'leashed');
});

test('flanker approaches on a curved path, not straight at the target', () => {
  const m = mon('skitter', 0, 0), view = { target: { x: 250, y: 0 } };
  let maxOff = 0;
  const log = run(m, view, Rng.create(3), 5, { onStep: (mm) => { maxOff = Math.max(maxOff, Math.abs(mm.y)); } });
  assert.ok(maxOff > 20, `flanked sideways (${maxOff})`);
  assert.ok(ofType(log, 'attack').length > 0, 'still lands attacks');
  assert.ok(ofType(log, 'telegraph')[0].t < ofType(log, 'attack')[0].t);
});

test('kiter holds its preferred range band against a closing target', () => {
  const def = RPG.data.monsters['bone-archer'], pd = def.aiParams.preferredDistance;
  const lo = pd * (1 - ai.BAND), hi = pd * (1 + ai.BAND);
  // The archer is rooted for its 0.6 s windup while the player closes at 40/s, so allow that much overshoot.
  const SLACK = 40 * def.aiParams.windup + 5;
  for (const startX of [60, 200, 300]) {
    const m = mon('bone-archer', 0, 0), tgt = { x: startX, y: 0 }, view = { target: tgt };
    const rng = Rng.create(5), samples = [];
    run(m, view, rng, 12, {
      onStep: (mm, it, t) => {
        // The player walks towards the archer when beyond the band's centre, away otherwise, slower than the archer.
        tgt.x += (tgt.x > mm.x ? -1 : 1) * 40 * DT;
        if (t > 6) samples.push(dist(mm, tgt));
      },
    });
    const worst = samples.filter((d) => d < lo - SLACK || d > hi + SLACK);
    assert.equal(worst.length, 0, `start ${startX}: left band ${lo}-${hi}`);
  }
  const m = mon('bone-archer', 0, 0), log = run(m, { target: { x: 200, y: 0 } }, Rng.create(2), 6);
  const arrow = ofType(log, 'attack')[0];
  assert.equal(arrow.kind, 'projectile');
  assert.ok(arrow.projectile.speed > 0);
});

test('tank advances slower than its listed speed and winds up a heavy slam', () => {
  const m = mon('stone-brute', 0, 0), view = { target: { x: 170, y: 0 } };
  let maxSpeed = 0;
  const log = run(m, view, Rng.create(1), 8, { onStep: (mm, it) => { maxSpeed = Math.max(maxSpeed, Math.hypot(it.vx, it.vy)); } });
  assert.ok(maxSpeed <= 40 * ai.TANK_SPEED_MULT + 1e-9 && maxSpeed > 0);
  const tel = ofType(log, 'telegraph')[0], atk = ofType(log, 'attack')[0];
  assert.ok(atk.t - tel.t >= 0.7 - 1e-9, 'long windup');
  assert.equal(atk.damageMult, 1.3);
});

test('exploder stops, fuses with a circle warning, then bursts once', () => {
  const m = mon('cinder-bomb', 0, 0), view = { target: { x: 150, y: 0 } };
  const log = run(m, view, Rng.create(1), 6);
  const tel = ofType(log, 'telegraph')[0], atk = ofType(log, 'attack')[0], boom = ofType(log, 'explode');
  assert.equal(tel.shape, 'circle');
  assert.equal(tel.radius, 70);
  assert.ok(atk.t - tel.t >= 1.0 - 1e-9, 'one second fuse');
  assert.equal(boom.length, 1);
  assert.equal(atk.x, tel.x);                       // centred on itself, where it stopped
  const x = m.x; run(m, view, Rng.create(1), 1);
  assert.equal(m.x, x, 'inert after bursting');
});

test('summoner summons on cadence and never exceeds its cap', () => {
  const m = mon('hexcaller', 0, 0), tgt = { x: 180, y: 0 };
  let alive = 0;
  const view = { target: tgt, summonCount: () => alive };
  const rng = Rng.create(7), summons = [];
  const log = run(m, view, rng, 60, {
    onStep: (mm, it, t) => {
      it.actions.forEach((a) => { if (a.type === 'summon') { alive += a.count; summons.push(t); } });
      assert.ok(alive <= 4 + 2, 'cap respected (one batch of overshoot at most)');
    },
  });
  assert.ok(summons.length >= 2, 'summoned repeatedly');
  assert.ok(summons[0] >= 6 - 1e-9, 'first summon waits for the interval');
  assert.equal(ofType(log, 'summon')[0].defId, 'skitter');
  // At the cap it stops summoning entirely.
  const capped = mon('hexcaller', 0, 0), cv = { target: tgt, summonCount: () => 4 };
  assert.equal(ofType(run(capped, cv, Rng.create(7), 40), 'summon').length, 0);
  // Cadence: with the count always zero, summons are at least one interval apart.
  const free = mon('hexcaller', 0, 0), fv = { target: tgt, summonCount: () => 0 }, ts = [];
  run(free, fv, Rng.create(7), 40, { onStep: (mm, it, t) => { if (it.actions.some((a) => a.type === 'summon')) ts.push(t); } });
  for (let i = 1; i < ts.length; i++) assert.ok(ts[i] - ts[i - 1] >= 6 - 1e-9);
});

test('boss: telegraph always precedes damage and lasts at least its windup', () => {
  const def = RPG.data.monsters['warden-of-ash'];
  for (const seed of [1, 2, 3, 4]) {
    const m = mon('warden-of-ash', 0, 0), view = { target: { x: 200, y: 50 } };
    const rng = Rng.create(seed), pending = new Map(), seen = [];
    run(m, view, rng, 90, {
      onStep: (mm, it, t) => {
        // Hit points drain so all three phases are exercised.
        mm.hp = Math.max(1, mm.hp - mm.maxHp * 0.01 * DT * 3);
        for (const a of it.actions) {
          if (a.type === 'telegraph') pending.set(a.attackId, t);
          if (a.type === 'attack') {
            assert.ok(pending.has(a.attackId), `${a.attackId} damage without telegraph`);
            const atk = def.attacks.find((x) => x.id === a.attackId);
            assert.ok(t - pending.get(a.attackId) >= atk.windup - 1e-9, 'warning lasted the windup');
            assert.ok(t - pending.get(a.attackId) >= 0.8);
            pending.delete(a.attackId); seen.push(a.attackId);
          }
        }
      },
    });
    assert.ok(seen.length > 5, 'boss attacked');
  }
});

test('boss: phases change at hp thresholds and unlock attacks', () => {
  const def = RPG.data.monsters['warden-of-ash'];
  assert.equal(ai.phaseIndex(def, 1), 0);
  assert.equal(ai.phaseIndex(def, 0.67), 0);
  assert.equal(ai.phaseIndex(def, 0.66), 1);
  assert.equal(ai.phaseIndex(def, 0.33), 2);
  const m = mon('warden-of-ash', 0, 0), view = { target: { x: 200, y: 0 } };
  const rng = Rng.create(9), phases = [], used = new Set();
  run(m, view, rng, 120, {
    onStep: (mm, it, t) => {
      it.actions.forEach((a) => { if (a.type === 'phase') phases.push(a); if (a.type === 'attack') used.add(a.attackId + '@' + mm.ai.phase); });
      if (t > 30) mm.hp = mm.maxHp * 0.3;
      else if (t > 10) mm.hp = mm.maxHp * 0.6;
    },
  });
  assert.deepEqual(phases.map((p) => [p.from, p.to]), [[0, 1], [1, 2]]);
  assert.equal(phases[1].special, 'enrage');
  const phase0 = [...used].filter((u) => u.endsWith('@0')).map((u) => u.split('@')[0]);
  assert.deepEqual([...new Set(phase0)], ['ember-circles'], 'only circles in phase 0');
  assert.ok(![...used].some((u) => u.startsWith('ring-of-ash@') && !u.endsWith('@2')), 'nova locked to phase 2');
  // Phases never regress when the boss heals.
  m.hp = m.maxHp; ai.think(m, view, rng, DT);
  assert.equal(m.ai.phase, 2);
});

test('boss: every third ember circle is bright and leaves it vulnerable', () => {
  const m = mon('warden-of-ash', 0, 0), view = { target: { x: 200, y: 0 } };
  const circles = ofType(run(m, view, Rng.create(1), 60), 'attack').filter((a) => a.attackId === 'ember-circles');
  assert.ok(circles.length >= 3);
  circles.forEach((c, i) => assert.equal(!!c.bright, (i + 1) % 3 === 0));
  assert.equal(circles[2].vulnerable, 2);
});

test('think is deterministic for a fixed seed and ignores dead monsters', () => {
  const go = () => {
    const m = mon('warden-of-ash', 0, 0);
    return JSON.stringify(run(m, { target: { x: 200, y: 0 } }, Rng.create(11), 30));
  };
  assert.equal(go(), go());
  const dead = mon('husk-grunt', 0, 0); dead.dead = true;
  assert.equal(ai.think(dead, { target: { x: 10, y: 0 } }, Rng.create(1), DT).actions.length, 0);
});

test('every ai id used in monsters.js has a behaviour', () => {
  for (const def of Object.values(RPG.data.monsters)) assert.ok(ai.BEHAVIOURS[def.ai], def.ai);
});

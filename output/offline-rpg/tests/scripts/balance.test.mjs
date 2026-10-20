// Balance checks (A4 balance pass): the analytic scaling ordering, XP pace and loot-quality ordering, plus a small
// seeded run of scripts/balance.mjs for the survivability and difficulty ordering. Every number here comes from the
// shipped data; nothing is stubbed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadEngine, threat, gateRoute, killsToLevel, rareShare, sweep, formatReport, DIFFICULTIES } from '../../scripts/balance.mjs';

const PRESETS = ['easy', 'normal', 'hard', 'nightmare'];
const AREAS = ['forest', 'caves', 'arena'];
const LEVELS = Array.from({ length: 15 }, (_, i) => i + 1);

const RPG = await loadEngine();

test('enemy strength rises with player level in every area on every difficulty', () => {
  for (const d of [...PRESETS, 'custom']) {
    for (const a of AREAS) {
      for (const l of LEVELS.slice(1)) {
        assert.ok(threat(RPG, a, l, d) > threat(RPG, a, l - 1, d), `${d}/${a}: threat did not rise from L${l - 1} to L${l}`);
      }
    }
  }
});

test('enemy strength rises with area depth at every player level and difficulty', () => {
  for (const d of [...PRESETS, 'custom']) {
    for (const l of LEVELS) {
      assert.ok(threat(RPG, 'caves', l, d) > threat(RPG, 'forest', l, d), `${d} L${l}: caves are not harder than the forest`);
      assert.ok(threat(RPG, 'arena', l, d) > threat(RPG, 'caves', l, d), `${d} L${l}: the arena is not harder than the caves`);
    }
  }
});

test('difficulty ordering holds: easy < normal < hard < nightmare, with nightmare clearly above hard', () => {
  for (const a of AREAS) {
    for (const l of LEVELS) {
      const t = PRESETS.map((d) => threat(RPG, a, l, d));
      for (let i = 1; i < t.length; i++) assert.ok(t[i] > t[i - 1], `${a} L${l}: ${PRESETS[i]} is not harder than ${PRESETS[i - 1]}`);
      assert.ok(t[3] / t[2] >= 1.8, `${a} L${l}: nightmare is only ${(t[3] / t[2]).toFixed(2)}x hard`);
      assert.ok(t[2] / t[1] >= 1.3, `${a} L${l}: hard is only ${(t[2] / t[1]).toFixed(2)}x normal`);
      assert.ok(Math.abs(threat(RPG, a, l, 'custom') - t[1]) < 1e-9, `${a} L${l}: untouched custom differs from normal`);
    }
  }
});

test('XP pace: kills per level rise smoothly, stay playable, and shrink on harder difficulties', () => {
  for (const d of PRESETS) {
    // Forest carries a player from 1 to the caves gate (level 4); the caves from 4 to the arena gate (level 6).
    for (let l = 1; l <= 3; l++) assert.ok(killsToLevel(RPG, 'forest', l, d) <= 70, `${d}: forest L${l} needs too many kills`);
    for (let l = 4; l <= 5; l++) assert.ok(killsToLevel(RPG, 'caves', l, d) <= 70, `${d}: caves L${l} needs too many kills`);
    // From the caves gate on: levels 1-3 never fight in the caves, and the first curve steps (100 -> 283) double by design.
    for (let l = 5; l <= 10; l++) {
      const prev = killsToLevel(RPG, 'caves', l - 1, d), cur = killsToLevel(RPG, 'caves', l, d);
      assert.ok(cur > prev && cur / prev < 2.5, `${d}: caves pace jumps from ${prev.toFixed(1)} to ${cur.toFixed(1)} kills at L${l}`);
    }
  }
  for (let i = 1; i < PRESETS.length; i++) {
    assert.ok(killsToLevel(RPG, 'caves', 5, PRESETS[i]) < killsToLevel(RPG, 'caves', 5, PRESETS[i - 1]), 'harder difficulty must level faster');
  }
});

test('gate route: normal play (clearing at most 80% of each area, no respawn) reaches every gate', () => {
  for (const run of gateRoute(RPG, { seeds: 6 })) {
    for (const st of run.steps) {
      assert.ok(st.level >= st.gateLevel, `seed ${run.seed}: clearing ${st.area} fully only reaches L${st.level}, gate needs L${st.gateLevel}`);
      assert.ok(st.clearedShare <= 0.8, `seed ${run.seed}: ${st.area} gate needs ${(st.clearedShare * 100).toFixed(0)}% of the area (grinding)`);
    }
  }
});

test('loot quality: the share of rare-or-better drops rises with difficulty and with area depth', () => {
  for (const a of AREAS) {
    const shares = PRESETS.map((d) => rareShare(RPG, a, d));
    for (let i = 1; i < shares.length; i++) assert.ok(shares[i] > shares[i - 1], `${a}: ${PRESETS[i]} does not drop better loot than ${PRESETS[i - 1]}`);
    // The arena only holds the boss, whose table has its own rarityBoost; the cap is for ordinary monster drops.
    if (a !== 'arena') assert.ok(shares[3] < 0.3, `${a}: nightmare rare-or-better share ${shares[3].toFixed(3)} is out of hand`);
  }
  for (const d of PRESETS) assert.ok(rareShare(RPG, 'arena', d) > rareShare(RPG, 'forest', d), `${d}: deeper areas must drop better loot`);
});

test('tuned data still passes the data-definition validators', () => {
  const problems = RPG.data.areas.validate();
  assert.equal(problems.length, 0, problems.join('; '));
  const ranges = RPG.data.difficulties.custom.ranges;
  for (const d of PRESETS) {
    for (const [field, [lo, hi]] of Object.entries(ranges)) {
      const v = RPG.data.difficulties[d][field];
      assert.ok(v >= lo && v <= hi, `${d}.${field}=${v} is outside the custom slider range ${lo}..${hi}`);
    }
  }
});

test('seeded simulation: easy is survivable, and deaths and damage never fall as difficulty rises', { timeout: 240000 }, async () => {
  const { rows } = await sweep({ levels: [4, 8, 12], areas: ['forest', 'caves'], difficulties: [...PRESETS], seeds: 2, seconds: 45 });
  assert.equal(rows.reduce((a, r) => a + r.stepErrors, 0), 0, 'the sim threw during a balance run');
  const by = (d) => rows.filter((r) => r.difficulty === d);
  const avg = (d, f) => by(d).reduce((a, r) => a + f(r), 0) / by(d).length;
  assert.equal(avg('easy', (r) => r.deaths), 0, 'easy must be survivable in the forest and caves');
  for (let i = 1; i < PRESETS.length; i++) {
    const lo = PRESETS[i - 1], hi = PRESETS[i];
    assert.ok(avg(hi, (r) => r.damageTakenPct) >= avg(lo, (r) => r.damageTakenPct), `${hi} takes less damage than ${lo}`);
    assert.ok(avg(hi, (r) => r.deaths) >= avg(lo, (r) => r.deaths), `${hi} kills the scripted player less often than ${lo}`);
  }
  assert.ok(avg('nightmare', (r) => r.damageTakenPct) > avg('hard', (r) => r.damageTakenPct), 'nightmare must hurt more than hard');
  assert.ok(avg('nightmare', (r) => r.deaths) > avg('hard', (r) => r.deaths), 'nightmare must kill more than hard');
});

test('the report prints per-difficulty and per-area tables, including custom', { timeout: 120000 }, async () => {
  const levels = [1, 5];
  const { rows } = await sweep({ levels, areas: ['forest', 'caves', 'arena'], difficulties: DIFFICULTIES, seeds: 1, seconds: 8 });
  const text = formatReport(RPG, rows, levels);
  for (const d of DIFFICULTIES) assert.ok(text.includes(`[${d}] TTK`), `no TTK table for ${d}`);
  for (const h of ['Damage taken', 'XP/min', 'Level progress', 'Per-difficulty summary', 'Per-area summary', 'XP pace', 'Loot rarity distribution']) {
    assert.ok(text.includes(h), `report is missing "${h}"`);
  }
  assert.ok(!text.includes('NaN'), 'report contains NaN');
});

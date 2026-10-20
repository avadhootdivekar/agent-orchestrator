'use strict';
// Smoke test for scripts/bench.mjs: a short run must complete and report sim/draw timings for 300+ entities.
// It never asserts on speed (machine dependent); the 16 ms budget is checked by hand with `npm run bench`.

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const BENCH = path.resolve(__dirname, '..', '..', 'scripts', 'bench.mjs');

test('bench --json reports finite sim and draw timings with 300+ monsters', () => {
  const out = execFileSync(process.execPath, [BENCH, '--json', '--frames', '30', '--warmup', '5'], { encoding: 'utf8' });
  const r = JSON.parse(out);
  assert.ok(r.peak.monsters >= 300, 'peak monsters ' + r.peak.monsters);
  for (const k of ['sim', 'draw', 'total']) {
    assert.ok(Number.isFinite(r[k].p95) && r[k].p95 >= 0, k + ' p95');
  }
  assert.ok(r.drawCallsPerFrame > 0);
});

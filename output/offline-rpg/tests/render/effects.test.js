'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');
const { recordingContext } = require('./fakectx.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/rng.js', 'render/palette.js', 'render/draw-shapes.js', 'render/draw-effects.js'
]);
const E = RPG.render.effects;
const COLOUR = '#ff00ff';
const TWO_PI = Math.PI * 2;

/** Recording context that throws on a negative or non-finite radius, like a real Canvas. */
function strictContext() {
  const rec = recordingContext();
  const checkRadius = (r) => {
    if (!(typeof r === 'number' && isFinite(r) && r >= 0)) throw new RangeError(`invalid radius ${r}`);
  };
  const { ctx } = rec;
  const arc = ctx.arc;
  ctx.arc = (cx, cy, r, a0, a1) => { checkRadius(r); return arc.call(ctx, cx, cy, r, a0, a1); };
  return rec;
}

/** Recording context whose fillText also keeps the position it was drawn at (the shared fixture keeps only the text). */
function textContext() {
  const rec = strictContext();
  const { ctx } = rec;
  ctx.fillText = (text, x, y) => { rec.calls.push({ op: 'fillText', text: String(text), x, y }); };
  return rec;
}

/** Asserts save/restore are balanced and never dipped below zero. */
function assertBalanced(rec) {
  assert.equal(rec.ctx.saveDepth, 0, 'save depth at end');
  assert.equal(rec.ctx.minSaveDepth, 0, 'save depth never negative');
}

const ops = (calls, name) => calls.filter((c) => c.op === name);

/** Calls a drawer makes for real, without the save/restore bookkeeping. */
const drawOps = (calls) => calls.filter((c) => c.op !== 'save' && c.op !== 'restore');

/** Fill ops in the given style, in order. */
const fillsIn = (calls, style) => calls.filter((c) => c.op === 'fill' && c.style === style);

test('telegraphFill tracks t/duration and clamps to [0, 1]', () => {
  assert.equal(E.telegraphFill(0, 1), 0);
  assert.equal(E.telegraphFill(0.25, 1), 0.25);
  assert.equal(E.telegraphFill(0.5, 1), 0.5);
  assert.equal(E.telegraphFill(1, 1), 1);
  assert.equal(E.telegraphFill(3, 1), 1, 'past the end stays full');
  assert.equal(E.telegraphFill(-1, 1), 0, 'before the start stays empty');
  assert.equal(E.telegraphFill(NaN, 1), 0, 'non-finite t is empty');
  assert.equal(E.telegraphFill(0.2, 0), 1, 'zero duration reads as charged');
  assert.equal(E.telegraphFill(0.2, undefined), 1, 'missing duration reads as charged');
});

test('circle telegraph: fill disc radius follows t/duration, outline keeps full radius', () => {
  const rec = strictContext();
  E.drawTelegraph(rec.ctx, { shape: 'circle', x: 10, y: 20, radius: 40, t: 0.5, duration: 1 }, COLOUR);
  const arcs = ops(rec.calls, 'arc').map((c) => c.r);
  assert.deepEqual(arcs, [20, 40], 'fill disc at half radius, then the full outline ring');
  assertBalanced(rec);
});

test('circle telegraph: no fill at t=0, full fill at t=duration', () => {
  const empty = strictContext();
  E.drawTelegraph(empty.ctx, { shape: 'circle', x: 0, y: 0, radius: 30, t: 0, duration: 1 }, COLOUR);
  assert.equal(fillsIn(empty.calls, COLOUR).length, 0, 'no fill pass at t=0');

  const full = strictContext();
  E.drawTelegraph(full.ctx, { shape: 'circle', x: 0, y: 0, radius: 30, t: 1, duration: 1 }, COLOUR);
  assert.deepEqual(ops(full.calls, 'arc').map((c) => c.r), [30, 30]);
});

test('cone telegraph: pie radius follows t/duration, outline spans full range and half arc', () => {
  const rec = strictContext();
  E.drawTelegraph(rec.ctx, { shape: 'cone', x: 0, y: 0, dirX: 1, dirY: 0, range: 50, halfArc: 0.5, t: 0.25, duration: 1 }, COLOUR);
  const arcs = ops(rec.calls, 'arc');
  assert.equal(arcs.length, 2, 'fill pie and outline pie');
  assert.equal(arcs[0].r, 12.5, 'fill pie at a quarter of the range');
  assert.equal(arcs[1].r, 50, 'outline pie at full range');
  assert.ok(Math.abs(arcs[1].a1 - arcs[1].a0 - 1) < 1e-9, 'spans 2 * halfArc');
  assertBalanced(rec);
});

test('line telegraph: fill strip length follows t/duration', () => {
  const rec = strictContext();
  E.drawTelegraph(rec.ctx, { shape: 'line', x: 0, y: 0, dirX: 0, dirY: 1, range: 80, radius: 10, t: 0.5, duration: 1 }, COLOUR);
  const fill = rec.calls.filter((c) => c.op === 'fill' && c.style === COLOUR);
  assert.equal(fill.length, 1, 'one fill pass');
  // The strip runs along +y: its far edge is at y = 40 (half of range 80), and it is 10 wide either side of x = 0.
  // Only the fill polygon counts here; the outline strip that follows it is full length.
  const fillAt = rec.calls.findIndex((c) => c.op === 'fill');
  const poly = rec.calls.slice(0, fillAt).filter((c) => c.op === 'moveTo' || c.op === 'lineTo');
  const maxY = Math.max(...poly.map((c) => c.y));
  assert.ok(Math.abs(maxY - 40) < 1e-9, `far edge at half range, got ${maxY}`);
  assertBalanced(rec);
});

test('telegraph ignores missing or degenerate input without throwing', () => {
  const rec = strictContext();
  E.drawTelegraph(rec.ctx, null, COLOUR);
  E.drawTelegraph(rec.ctx, { shape: 'circle', x: NaN, y: 0, radius: 10, t: 0, duration: 1 }, COLOUR);
  E.drawTelegraph(rec.ctx, { shape: 'circle', x: 0, y: 0, radius: -5, t: 0, duration: 1 }, COLOUR);
  E.drawTelegraph(rec.ctx, { shape: 'circle', x: 0, y: 0, radius: 10, t: 0, duration: 1 }, '');
  assert.equal(drawOps(rec.calls).length, 0, 'nothing drawn');
  assertBalanced(rec);
});

test('aoe ring and nova grow with age and vanish at ttl', () => {
  const mid = strictContext();
  E.drawAoeRing(mid.ctx, 0, 0, 60, 0.2, 0.4, COLOUR);
  const midR = ops(mid.calls, 'arc')[0].r;
  assert.ok(midR > 0 && midR < 60, `mid-life ring radius inside (0, 60), got ${midR}`);

  const done = strictContext();
  E.drawAoeRing(done.ctx, 0, 0, 60, 0.4, 0.4, COLOUR);
  E.drawNova(done.ctx, 0, 0, 60, 0.5, 0.4, COLOUR);
  assert.equal(done.calls.length, 0, 'nothing once the ttl has passed');

  const nova = strictContext();
  E.drawNova(nova.ctx, 0, 0, 60, 0.1, 0.4, COLOUR);
  assert.ok(ops(nova.calls, 'createRadialGradient').length === 1, 'nova fill uses a radial gradient');
  assertBalanced(nova);
});

test('whirl draws three blade arcs at the given radius', () => {
  const rec = strictContext();
  E.drawWhirl(rec.ctx, 5, 5, 30, 0.7, COLOUR);
  const arcs = ops(rec.calls, 'arc');
  assert.equal(arcs.length, 3);
  for (const a of arcs) assert.equal(a.r, 30);
  const starts = arcs.map((a) => ((a.a0 - 0.7) % TWO_PI + TWO_PI) % TWO_PI).sort((a, b) => a - b);
  assert.ok(Math.abs(starts[1] - starts[0] - TWO_PI / 3) < 1e-9, 'blades evenly spaced');
  assertBalanced(rec);
});

test('damage numbers: at most 40 drawn, newest kept, expired ones skipped', () => {
  const list = [];
  for (let i = 0; i < 100; i++) list.push({ x: i, y: 0, amount: i, crit: false, age: 0 });
  const rec = textContext();
  E.drawDamageNumbers(rec.ctx, list);
  const texts = ops(rec.calls, 'fillText').map((c) => c.text);
  assert.equal(texts.length, E.DAMAGE_NUMBER_CAP, 'capped at 40');
  assert.equal(texts[0], '60', 'the oldest of the drawn set is entry 60');
  assert.equal(texts[texts.length - 1], '99', 'the newest entry is always drawn');

  const expired = textContext();
  E.drawDamageNumbers(expired.ctx, [{ x: 0, y: 0, amount: 5, age: 1.2 }]);
  assert.equal(ops(expired.calls, 'fillText').length, 0, 'a number at its full life is not drawn');
  assertBalanced(expired);
});

test('damage numbers rise as they age', () => {
  const young = textContext();
  E.drawDamageNumbers(young.ctx, [{ x: 0, y: 100, amount: 7, age: 0 }]);
  const old = textContext();
  E.drawDamageNumbers(old.ctx, [{ x: 0, y: 100, amount: 7, age: 0.9 }]);
  const a = ops(young.calls, 'fillText')[0];
  const b = ops(old.calls, 'fillText')[0];
  assert.ok(b.y < a.y, 'older number sits higher');
  assert.ok(Math.abs(a.y - 100) < 1e-9, 'a fresh number sits at its spawn point');
});

test('particle bursts: same seed gives the same burst, and the quality budget caps the live count', () => {
  const make = (seed, count, quality) => {
    const rng = RPG.core.Rng.create(seed);
    const parts = [];
    E.spawnBurst(parts, rng, 10, 10, count, COLOUR, quality);
    return parts;
  };
  const a = make(42, 50, 'high');
  const b = make(42, 50, 'high');
  assert.equal(a.length, 50);
  assert.deepEqual(a.map((p) => [p.vx, p.vy, p.ttl]), b.map((p) => [p.vx, p.vy, p.ttl]), 'deterministic');

  assert.equal(make(1, 1000, 'high').length, E.PARTICLE_CAP, 'high caps at the particle cap');
  assert.equal(make(1, 1000, 'med').length, E.particleBudget('med'));
  assert.equal(make(1, 1000, 'low').length, E.particleBudget('low'));
  assert.ok(E.particleBudget('low') < E.particleBudget('med') && E.particleBudget('med') < E.particleBudget('high'));
  assert.equal(make(1, 1000, 'bogus').length, E.particleBudget('med'), 'unknown quality uses the default');
});

test('particle bursts respect the cap across repeated spawns', () => {
  const rng = RPG.core.Rng.create(7);
  const parts = [];
  for (let i = 0; i < 20; i++) E.spawnBurst(parts, rng, 0, 0, 40, COLOUR, 'high');
  assert.equal(parts.length, E.PARTICLE_CAP);
  assert.equal(E.spawnBurst(parts, rng, 0, 0, 40, COLOUR, 'high'), 0, 'a full pool adds nothing');
});

test('stepParticles ages, moves and removes dead particles in place', () => {
  const rng = RPG.core.Rng.create(3);
  const parts = [];
  E.spawnBurst(parts, rng, 0, 0, 30, COLOUR, 'high');
  const same = E.stepParticles(parts, 0.1);
  assert.equal(same, parts, 'returns the same array');
  for (const p of parts) assert.ok(p.age < p.ttl);
  E.stepParticles(parts, 10);
  assert.equal(parts.length, 0, 'all dead after a long step');
});

test('drawParticles draws live particles only, balanced', () => {
  const rng = RPG.core.Rng.create(5);
  const parts = [];
  E.spawnBurst(parts, rng, 0, 0, 10, COLOUR, 'high');
  const rec = strictContext();
  E.drawParticles(rec.ctx, parts);
  assert.equal(ops(rec.calls, 'arc').length, 10);
  assertBalanced(rec);
});

test('hurt flash alpha is bounded, starts at the peak and decays smoothly to zero', () => {
  assert.equal(E.hurtFlashAlpha(0), E.HURT_FLASH_PEAK);
  let prev = E.hurtFlashAlpha(0);
  for (let age = 0.01; age <= 0.6; age += 0.01) {
    const a = E.hurtFlashAlpha(age);
    assert.ok(a >= 0 && a <= E.HURT_FLASH_PEAK, `alpha in bounds at ${age}`);
    assert.ok(a <= prev + 1e-12, `never rises as it decays (age ${age})`);
    assert.ok(prev - a < 0.05, `no sudden jump at age ${age}`);
    prev = a;
  }
  assert.equal(E.hurtFlashAlpha(0.4), 0, 'gone at the end of the pulse');
  assert.equal(E.hurtFlashAlpha(10), 0);
  assert.equal(E.hurtFlashAlpha(-1), 0, 'negative age gives nothing');
  assert.equal(E.hurtFlashAlpha(NaN), 0, 'non-finite age gives nothing');
});

test('drawHurtFlash draws a bounded vignette and nothing once decayed', () => {
  const rec = strictContext();
  E.drawHurtFlash(rec.ctx, 800, 600, 0, COLOUR);
  const fill = ops(rec.calls, 'fillRect');
  assert.equal(fill.length, 1);
  assert.ok(fill[0].alpha <= E.HURT_FLASH_PEAK && fill[0].alpha > 0, 'alpha within the peak');
  assert.equal(ops(rec.calls, 'createRadialGradient').length, 1, 'vignette, not a full-screen wash');
  assertBalanced(rec);

  const done = strictContext();
  E.drawHurtFlash(done.ctx, 800, 600, 0.5, COLOUR);
  assert.equal(done.calls.length, 0, 'no draw after the flash has decayed');
});

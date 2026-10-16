'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');
const { recordingContext } = require('./fakectx.js');

// The real effects drawer wired into the real renderer and sim: nothing is stubbed, so a missing export or a changed
// signature shows up here (the stubbed renderer tests could not catch the original drawEffects/drawDamageNumbers gap).
const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js',
  'core/state.js', 'data/maps/test-map.js', 'data/monsters.js', 'data/weapons.js', 'data/skills.js',
  'data/difficulties.js', 'data/items.js', 'data/areas.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js',
  'render/palette.js', 'render/camera.js', 'render/draw-map.js', 'render/draw-shapes.js', 'render/draw-entities.js',
  'render/draw-effects.js', 'render/hud.js', 'render/renderer.js', 'sim/player.js', 'sim/sim.js'
]);
const pal = RPG.render.palette;
const fx = RPG.render.effects;

// Calls made in world space, before the HUD resets the transform to CSS px (the HUD also uses fillText).
function worldCalls(calls) {
  const sets = calls.map((c, i) => (c.op === 'setTransform' ? i : -1)).filter((i) => i >= 0);
  return calls.slice(0, sets[sets.length - 2]);
}

function ops(calls, op) { return calls.filter((c) => c.op === op); }

function worldWithCtx() {
  const world = RPG.sim.createWorld({ seed: 1 });
  const { ctx, calls } = recordingContext();
  return { world, ctx, calls };
}

test('effects exports drawEffects and stepWorldFx', () => {
  assert.equal(typeof fx.drawEffects, 'function');
  assert.equal(typeof fx.stepWorldFx, 'function');
});

test('below layer draws a pooled monster telegraph: outline stroke and a growing fill', () => {
  const { world, ctx, calls } = worldWithCtx();
  world.effects.push({ type: 'telegraph', shape: 'circle', x: 50, y: 60, r: 30, radius: 30, t: 0.2, duration: 0.8 });
  fx.drawEffects(ctx, world, 1, pal, 'below');
  assert.ok(ops(calls, 'arc').length >= 2, 'fill disc and outline ring');
  assert.ok(ops(calls, 'stroke').some((c) => c.style === pal.enemy));
  assert.equal(ctx.saveDepth, 0);
});

test('below layer draws a boss warning (line shape) and an ember zone from world.telegraphs', () => {
  const { world, ctx, calls } = worldWithCtx();
  world.telegraphs.push({ kind: 'warning', shape: 'line', x: 0, y: 0, dirX: 1, dirY: 0, radius: 10, range: 100, remaining: 0.5, duration: 1 });
  fx.drawEffects(ctx, world, 1, pal, 'below');
  assert.ok(ops(calls, 'lineTo').length >= 6, 'filled strip + outline strip');
  calls.length = 0;
  world.telegraphs.length = 0;
  world.telegraphs.push({ kind: 'zone', zone: 'ember', x: 5, y: 5, radius: 40, remaining: 3 });
  fx.drawEffects(ctx, world, 1, pal, 'below');
  assert.ok(ops(calls, 'arc').some((c) => c.r === 40), 'ember zone ring');
});

test('below layer skips finished warnings and does nothing for an unknown layer', () => {
  const { world, ctx, calls } = worldWithCtx();
  world.telegraphs.push({ kind: 'warning', shape: 'circle', x: 0, y: 0, radius: 10, remaining: 0, duration: 1, done: true });
  fx.drawEffects(ctx, world, 1, pal, 'below');
  fx.drawEffects(ctx, world, 1, pal, 'sideways');
  assert.equal(calls.length, 0);
});

test('above layer draws skill AoE nova, ring, whirl, aura and blink streak', () => {
  const kinds = {
    aoe: { kind: 'aoe', x: 1, y: 2, radius: 50, ttl: 0.2, life: 0.4, color: '#cfc6b0', skillId: 's' },
    ring: { kind: 'ring', x: 1, y: 2, radius: 60, ttl: 0.25, life: 0.5, color: '#8fd0e8', skillId: 's' },
    whirl: { kind: 'whirl', x: 1, y: 2, radius: 45, ttl: 1, life: 2, color: '#fff', skillId: 's' },
    aura: { kind: 'aura', x: 1, y: 2, radius: 12, ttl: 1, life: 2, color: '#9bb0c8', skillId: 's' },
    arc: { kind: 'arc', x: 1, y: 2, x2: 90, y2: 80, ttl: 0.1, life: 0.25, color: '#d8e6a8', skillId: 's' }
  };
  const expect = { aoe: 'arc', ring: 'arc', whirl: 'arc', aura: 'arc', arc: 'lineTo' };
  for (const [name, e] of Object.entries(kinds)) {
    const { world, ctx, calls } = worldWithCtx();
    world.effects.push(e);
    fx.drawEffects(ctx, world, 1, pal, 'above');
    assert.ok(ops(calls, expect[name]).length > 0, `${name} draws ${expect[name]}`);
    assert.equal(ctx.saveDepth, 0, `${name} balanced save/restore`);
  }
});

test('whirl draws three blade arcs and an expired aoe draws nothing', () => {
  const { world, ctx, calls } = worldWithCtx();
  world.effects.push({ kind: 'whirl', x: 0, y: 0, radius: 40, ttl: 1, life: 2, color: '#fff', skillId: 's' });
  fx.drawEffects(ctx, world, 1, pal, 'above');
  assert.equal(ops(calls, 'arc').length, 3);
  calls.length = 0;
  world.effects.length = 0;
  world.effects.push({ kind: 'aoe', x: 0, y: 0, radius: 40, ttl: 0, life: 0.4, color: '#fff', skillId: 's' });
  fx.drawEffects(ctx, world, 1, pal, 'above');
  assert.equal(calls.length, 0);
});

test('above layer draws live particles', () => {
  const { world, ctx, calls } = worldWithCtx();
  world.fx.particles.push({ x: 3, y: 4, vx: 0, vy: 0, age: 0.1, ttl: 0.5, r: 2, colour: 'enemy' });
  fx.drawEffects(ctx, world, 1, pal, 'above');
  assert.ok(ops(calls, 'arc').some((c) => c.cx === 3 && c.cy === 4));
});

test('stepWorldFx spawns hit bursts, one death burst per monster, and respects the quality budget', () => {
  const { world } = worldWithCtx();
  const ctx = { dt: 1 / 60, hits: [{ x: 10, y: 10, dealt: 5, team: 1 }, { x: 10, y: 10, dealt: 5, team: 1, dot: 'burn' }, { x: 0, y: 0, dealt: 0, team: 1 }] };
  fx.stepWorldFx(world, ctx);
  assert.equal(world.fx.particles.length, 3, 'one hit burst; dot and zero-damage hits are skipped');
  world.monsters.push({ x: 20, y: 20, dead: true });
  fx.stepWorldFx(world, { dt: 1 / 60, hits: [] });
  assert.equal(world.fx.particles.length, 15);
  fx.stepWorldFx(world, { dt: 1 / 60, hits: [] });
  assert.equal(world.fx.particles.length, 15, 'the same corpse bursts only once');
  world.fx.quality = 'low';
  for (let i = 0; i < 50; i++) fx.stepWorldFx(world, { dt: 0.001, hits: [{ x: 1, y: 1, dealt: 1, team: 1 }] });
  assert.ok(world.fx.particles.length <= fx.PARTICLE_CAP * 0.25 + 0, 'low quality budget caps particles');
});

test('particles expire as the sim ticks and are deterministic for a seed', () => {
  const run = () => {
    const world = RPG.sim.createWorld({ seed: 7 });
    fx.stepWorldFx(world, { dt: 0.01, hits: [{ x: 5, y: 5, dealt: 3, team: 1 }] });
    return JSON.stringify(world.fx.particles);
  };
  assert.equal(run(), run());
  const { world } = worldWithCtx();
  fx.stepWorldFx(world, { dt: 0.01, hits: [{ x: 5, y: 5, dealt: 3, team: 1 }] });
  for (let i = 0; i < 60; i++) fx.stepWorldFx(world, { dt: 0.05, hits: [] });
  assert.equal(world.fx.particles.length, 0);
});

test('sim.step runs stepWorldFx: a dead monster bursts and damage numbers age through cleanup', () => {
  const { world } = worldWithCtx();
  world.monsters.push({ id: 900, x: 30, y: 30, dead: true, hp: 0, team: 1, def: RPG.data.monsters[Object.keys(RPG.data.monsters)[0]], rewarded: true });
  world.fx.damageNumbers.push({ x: 1, y: 1, amount: 4, age: 0 });
  RPG.sim.step(world, null, 1 / 60);
  assert.ok(world.fx.particles.length > 0, 'death burst spawned by the sim step');
  assert.ok(world.fx.damageNumbers[0].age > 0);
});

function recordingCanvas() {
  const { ctx, calls } = recordingContext();
  ctx.setTransform = (a, b, c, d, e, f) => calls.push({ op: 'setTransform', m: [a, b, c, d, e, f] });
  ctx.fillText = function (text) { calls.push({ op: 'fillText', text: String(text), alpha: this.globalAlpha }); };
  return { canvas: { width: 0, height: 0, getContext: () => ctx }, ctx, calls };
}

function frame(mutate, settings) {
  const { canvas, calls } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(800, 600, 1);
  if (settings) r.settings = settings;
  const world = RPG.sim.createWorld({ seed: 1 });
  r.snapTo(world);
  mutate(world);
  calls.length = 0;
  r.draw(world, 1, 0);
  return { calls, world };
}

test('renderer draws damage numbers from world.fx.damageNumbers', () => {
  const { calls } = frame((w) => { w.fx.damageNumbers.push({ x: 40, y: 40, amount: 17.4, age: 0.1 }, { x: 50, y: 50, amount: 99, crit: true, age: 0.2 }); });
  const texts = ops(worldCalls(calls), 'fillText').map((c) => c.text);
  assert.deepEqual(texts, ['17', '99']);
});

test('renderer hides damage numbers when the setting is off', () => {
  const { calls } = frame((w) => { w.fx.damageNumbers.push({ x: 40, y: 40, amount: 17, age: 0.1 }); }, { showDamageNumbers: false });
  assert.equal(ops(worldCalls(calls), 'fillText').length, 0);
});

test('renderer draws telegraphs, skill effects and particles through the real effects module', () => {
  const { calls } = frame((w) => {
    w.effects.push({ type: 'telegraph', shape: 'circle', x: 60, y: 60, r: 33, radius: 33, t: 0.1, duration: 1 });
    w.effects.push({ kind: 'aoe', x: 70, y: 70, radius: 27, ttl: 0.1, life: 0.4, color: '#cfc6b0', skillId: 's' });
    w.fx.particles.push({ x: 80, y: 80, vx: 0, vy: 0, age: 0, ttl: 0.5, r: 2, colour: 'enemy' });
  });
  const arcs = ops(calls, 'arc');
  assert.ok(arcs.some((c) => c.r === 33), 'telegraph ring');
  assert.ok(arcs.some((c) => c.cx === 80 && c.cy === 80), 'particle');
  assert.ok(arcs.some((c) => c.r > 0 && c.r <= 27 && c.cx === 70), 'nova');
});

test('hurt flash draws a gentle edge vignette in HUD space, and nothing once it has decayed', () => {
  const { calls } = frame((w) => { w.fx.hurtFlash = 0.25; w.fx.hurtFlashMax = 0.25; });
  const grad = ops(calls, 'createRadialGradient');
  assert.equal(grad.length, 1);
  const flash = calls.find((c) => c.op === 'fillRect' && c.w === 800 && c.h === 600 && c.alpha > 0 && c.alpha < 1);
  assert.ok(flash, 'full-view fillRect with partial alpha');
  assert.ok(flash.alpha <= fx.HURT_FLASH_PEAK, 'never brighter than the peak (no harsh flash)');
  assert.deepEqual(grad[0].stops.map((s) => s.color), ['transparent', pal.enemy]);
  assert.equal(ops(frame(() => {}).calls, 'createRadialGradient').length, 0);
});

test('hurt flash fades as the sim countdown runs down', () => {
  const alphaAt = (left) => frame((w) => { w.fx.hurtFlash = left; w.fx.hurtFlashMax = 0.25; })
    .calls.find((c) => c.op === 'fillRect' && c.w === 800 && c.h === 600 && c.alpha > 0 && c.alpha < 1)?.alpha ?? 0;
  assert.ok(alphaAt(0.24) > alphaAt(0.12));
  assert.ok(alphaAt(0.12) > alphaAt(0.02));
});

test('hurtPlayer arms the flash fields the renderer reads', () => {
  const R = loadRPG([
    'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js',
    'core/state.js', 'data/maps/test-map.js', 'data/monsters.js', 'data/weapons.js', 'data/skills.js',
    'data/difficulties.js', 'data/items.js', 'data/areas.js', 'world/stats.js', 'world/tilemap.js', 'world/collision.js',
    'world/combat.js', 'sim/player.js', 'sim/combat-step.js', 'sim/sim.js'
  ]);
  const world = R.sim.createWorld({ seed: 1 });
  R.sim.hurtPlayer(world, 5, { type: 'phys' });
  assert.ok(world.fx.hurtFlash > 0);
  assert.equal(world.fx.hurtFlashMax, world.fx.hurtFlash);
});

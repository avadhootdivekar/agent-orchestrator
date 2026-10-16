'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');
const { recordingContext } = require('./fakectx.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'render/palette.js', 'render/draw-shapes.js', 'render/draw-entities.js',
  'render/draw-effects.js', 'data/monsters.js', 'data/items.js', 'data/weapons.js'
]);
const E = RPG.render.entities;
const sword = RPG.data.weapons.sword;
const defId = Object.keys(RPG.data.monsters)[0];

const player = (attack) => ({ kind: 'player', x: 10, y: 10, r: 12, facing: 0, status: {}, attack });
const arcs = (calls) => calls.filter((c) => c.op === 'arc');
const monster = (over = {}) => Object.assign({
  defId, x: 100, y: 50, r: RPG.data.monsters[defId].radius, facing: 0, hp: 30, maxHp: 30, flash: 0, status: {}, dead: false
}, over);

function drawPlayerWith(attack) {
  const rec = recordingContext();
  E.drawPlayer(rec.ctx, player(attack), 10, 10, RPG.render.palette);
  assert.equal(rec.ctx.saveDepth, 0);
  return rec.calls;
}

test('idle player draws no swing arc; a sword attack in active phase does', () => {
  const idle = drawPlayerWith({ state: 'idle', t: 0, dir: 0 });
  const active = drawPlayerWith({ state: 'active', t: sword.active / 2, dir: 0, weaponId: 'sword' });
  const reach = sword.range + 6;
  assert.equal(arcs(idle).filter((c) => c.r === reach).length, 0);
  const swing = arcs(active).filter((c) => c.r === reach);
  assert.ok(swing.length >= 2, 'wedge and leading edge');
  for (const c of swing) {
    assert.ok(c.a0 >= -sword.arc / 2 - 1e-9 && c.a1 <= sword.arc / 2 + 1e-9, 'stays within the weapon cone');
  }
});

test('swing sweeps further as the active timer advances and fades in recover', () => {
  const edge = (state, t) => {
    const calls = drawPlayerWith({ state, t, dir: 0, weaponId: 'sword' });
    const a = arcs(calls).filter((c) => c.r === sword.range + 6);
    return { end: Math.max(...a.map((c) => c.a1)), alpha: calls.filter((c) => c.op === 'stroke' && c.lineWidth === 4).map((c) => c.alpha) };
  };
  assert.ok(edge('active', sword.active * 0.8).end > edge('active', sword.active * 0.2).end);
  assert.ok(Math.abs(edge('recover', 0).end - sword.arc / 2) < 1e-9, 'recover holds the full arc');
  const early = edge('recover', sword.recover * 0.1).alpha[0];
  const late = edge('recover', sword.recover * 0.9).alpha[0];
  assert.ok(early > late, 'fades out');
});

test('ranged weapons and unknown weapons draw no swing arc', () => {
  const bow = RPG.data.weapons['hunting-bow'].id;
  const base = arcs(drawPlayerWith({ state: 'idle', t: 0, dir: 0 })).length;
  assert.equal(arcs(drawPlayerWith({ state: 'active', t: 0.05, dir: 0, weaponId: bow })).length, base);
  assert.equal(arcs(drawPlayerWith({ state: 'active', t: 0.05, dir: 0, weaponId: 'nope' })).length, base);
});

test('a recently hit monster draws a flash glow that a calm one does not', () => {
  const glows = (m) => {
    const rec = recordingContext();
    E.drawMonster(rec.ctx, m, 1);
    return rec.calls.filter((c) => c.op === 'createRadialGradient' && c.stops.some((s) => s.color === '#f0f0f0')).length;
  };
  assert.equal(glows(monster({ flash: 0 })), 0);
  assert.equal(glows(monster({ flash: 0.1 })), 1);
});

test('stepWorldFx turns a fresh death into a ghost that ages out', () => {
  const m = monster({ dead: true });
  const world = { monsters: [m], fx: { particles: [], rng: { range: (a, b) => (a + b) / 2 } } };
  RPG.render.effects.stepWorldFx(world, { dt: 0.05, hits: [] });
  assert.equal(world.fx.deaths.length, 1);
  RPG.render.effects.stepWorldFx(world, { dt: 0.05, hits: [] });
  assert.equal(world.fx.deaths.length, 1, 'same corpse is not ghosted twice');
  assert.ok(world.fx.deaths[0].age > 0.09);
  world.monsters.length = 0; // the sim removed the corpse: the ghost is independent of it
  RPG.render.effects.stepWorldFx(world, { dt: 1, hits: [] });
  assert.equal(world.fx.deaths.length, 0);
});

test('dying ghosts shrink and fade, and are not world monsters', () => {
  const m = monster({ dead: true });
  const world = { monsters: [m], fx: { particles: [], rng: { range: (a, b) => (a + b) / 2 } } };
  RPG.render.effects.stepWorldFx(world, { dt: 0.01, hits: [] });
  const g = world.fx.deaths[0];
  assert.notEqual(g, m);
  assert.equal(world.monsters.includes(g), false);
  const radiusAt = (age) => {
    const rec = recordingContext();
    E.drawDying(rec.ctx, Object.assign({}, g, { age }), 1);
    assert.equal(rec.ctx.saveDepth, 0);
    const a = rec.calls.find((c) => c.op === 'arc');
    return { r: a.r, alpha: rec.calls.find((c) => c.op === 'fill').alpha };
  };
  const early = radiusAt(0.05), late = radiusAt(g.life * 0.9);
  assert.ok(late.r < early.r);
  assert.ok(late.alpha < early.alpha);
  const rec = recordingContext();
  E.drawDying(rec.ctx, Object.assign({}, g, { age: g.life }), 1);
  assert.equal(rec.calls.length, 0, 'finished ghosts draw nothing');
});

test('renderer draws dying ghosts before monsters', () => {
  const order = [];
  const saved = RPG.render.entities;
  RPG.render.entities = { drawDying: () => order.push('dying'), drawMonster: () => order.push('monster') };
  try {
    const src = require('node:fs').readFileSync(require('node:path').join(__dirname, '../../src/render/renderer.js'), 'utf8');
    assert.ok(src.indexOf("'drawDying'") > -1 && src.indexOf("'drawDying'") < src.indexOf("'drawMonster'"));
  } finally { RPG.render.entities = saved; }
});

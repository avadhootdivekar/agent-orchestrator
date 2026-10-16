'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

// Renderer needs the sim to build a real world, plus the map/shape drawers it calls. Everything here is headless:
// the canvas and 2D context are fakes, so draw() is checked by the calls it makes, never by pixels.
const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'core/events.js', 'core/pool.js', 'core/entities.js',
  'core/state.js', 'data/maps/test-map.js', 'data/monsters.js', 'data/weapons.js', 'data/skills.js',
  'data/difficulties.js', 'data/items.js', 'data/areas.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js',
  'render/palette.js', 'render/camera.js', 'render/draw-map.js', 'render/draw-shapes.js', 'render/renderer.js',
  'sim/player.js', 'sim/sim.js'
]);

const plain = (v) => JSON.parse(JSON.stringify(v));

/**
 * Recording 2D context. Methods the test cares about are recorded explicitly; anything else the drawers call is
 * recorded by name and does nothing, so draw() can run without us listing every canvas method up front.
 */
function recordingCanvas() {
  const calls = [];
  const base = {
    fillStyle: '#000', strokeStyle: '#000', globalAlpha: 1, lineWidth: 1,
    setTransform(a, b, c, d, e, f) { calls.push({ op: 'setTransform', m: [a, b, c, d, e, f] }); },
    fillRect(x, y, w, h) { calls.push({ op: 'fillRect', x, y, w, h, style: this.fillStyle }); },
    arc(x, y, r) { calls.push({ op: 'arc', x, y, r }); },
    fill() { calls.push({ op: 'fill', style: this.fillStyle }); },
    stroke() { calls.push({ op: 'stroke', style: this.strokeStyle }); }
  };
  const ctx = new Proxy(base, {
    get(target, key) {
      if (key in target) return target[key];
      return (...args) => { calls.push({ op: String(key), args }); };
    }
  });
  const canvas = { width: 0, height: 0, getContext(kind) { assert.equal(kind, '2d'); return ctx; } };
  return { canvas, ctx, calls };
}

test('resize sets the CSS viewport and scales the backing store by devicePixelRatio', () => {
  const { canvas } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(800, 600, 2);
  assert.equal(r.viewW, 800);
  assert.equal(r.viewH, 600);
  assert.equal(r.dpr, 2);
  assert.equal(canvas.width, 1600);
  assert.equal(canvas.height, 1200);
});

test('resize rounds fractional backing-store sizes to whole device pixels', () => {
  const { canvas } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(333.3, 201.7, 1.25);
  assert.equal(canvas.width, Math.round(333.3 * 1.25));
  assert.equal(canvas.height, Math.round(201.7 * 1.25));
});

test('create uses the default zoom on its camera', () => {
  const { canvas } = recordingCanvas();
  const r = RPG.render.create(canvas);
  assert.equal(r.camera.zoom, 1.5);
});

test('draw on a real sim world does not throw and paints the background first, then the player', () => {
  const { canvas, calls } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(800, 600, 1);
  const world = RPG.sim.createWorld({ seed: 1 });
  r.snapTo(world);
  calls.length = 0;
  r.draw(world, 1, 1 / 60);
  const bg = calls.find((c) => c.op === 'fillRect');
  assert.ok(bg, 'background fillRect recorded');
  assert.equal(bg.style, RPG.render.palette.bg);
  assert.deepEqual([bg.x, bg.y, bg.w, bg.h], [0, 0, canvas.width, canvas.height]);
  // Player body is an arc of the player's radius, drawn in the player colour.
  const body = calls.find((c) => c.op === 'arc' && c.r === world.player.r);
  assert.ok(body, 'player circle recorded');
  assert.ok(calls.indexOf(bg) < calls.indexOf(body), 'background painted before the player');
});

test('draw leaves the context transform reset to identity so later drawing is not skewed', () => {
  const { canvas, calls } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(800, 600, 2);
  const world = RPG.sim.createWorld({ seed: 1 });
  r.snapTo(world);
  calls.length = 0;
  r.draw(world, 0.5, 1 / 60);
  const transforms = calls.filter((c) => c.op === 'setTransform');
  assert.ok(transforms.length >= 2, 'a world transform is set, then reset');
  assert.deepEqual(transforms.at(-1).m, [1, 0, 0, 1, 0, 0]);
});

test('draw does not mutate the world (renderer only reads it)', () => {
  const { canvas } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(800, 600, 1);
  const world = RPG.sim.createWorld({ seed: 3 });
  const before = plain({ player: world.player, tick: world.tick, time: world.time, monsters: world.monsters });
  r.snapTo(world);
  r.draw(world, 0.25, 1 / 60);
  assert.deepEqual(plain({ player: world.player, tick: world.tick, time: world.time, monsters: world.monsters }), before);
});

test('draw interpolates the player between prevX/prevY and x/y by alpha', () => {
  const { canvas, calls } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(800, 600, 1);
  const world = RPG.sim.createWorld({ seed: 1 });
  world.player.prevX = 100;
  world.player.prevY = 200;
  world.player.x = 110;
  world.player.y = 220;
  r.snapTo(world);
  calls.length = 0;
  r.draw(world, 0.5, 0);
  const body = calls.find((c) => c.op === 'arc' && c.r === world.player.r);
  assert.equal(body.x, 105);
  assert.equal(body.y, 210);
});

test('draw follows the player with the camera when dt is positive', () => {
  const { canvas } = recordingCanvas();
  const r = RPG.render.create(canvas);
  r.resize(800, 600, 1);
  const world = RPG.sim.createWorld({ seed: 1 });
  r.snapTo(world);
  const start = { x: r.camera.x, y: r.camera.y };
  world.player.prevX = world.player.x = start.x + 400;
  world.player.prevY = world.player.y = start.y;
  r.draw(world, 1, 1 / 60);
  assert.ok(r.camera.x > start.x && r.camera.x < start.x + 400, 'camera moved toward the player but not onto it yet');
});

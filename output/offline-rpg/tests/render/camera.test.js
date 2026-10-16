'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

// camera.js is pure math and attaches to RPG.render; it needs no other module.
const RPG = loadRPG(['render/camera.js']);
const cam = RPG.render.camera;

const EPS = 1e-9;
const near = (actual, expected, msg) => assert.ok(Math.abs(actual - expected) < EPS, `${msg}: ${actual} != ${expected}`);

test('create starts centred on the origin at zoom 1 with no shake', () => {
  const c = cam.create();
  assert.deepEqual({ x: c.x, y: c.y, zoom: c.zoom, shake: c.shake }, { x: 0, y: 0, zoom: 1, shake: 0 });
});

test('snap places the camera exactly on the target with no easing', () => {
  const c = cam.create();
  cam.snap(c, 123.5, -40);
  assert.equal(c.x, 123.5);
  assert.equal(c.y, -40);
});

test('follow with dt = 0 does not move the camera', () => {
  const c = cam.create();
  cam.follow(c, 100, 100, 0);
  assert.equal(c.x, 0);
  assert.equal(c.y, 0);
});

test('follow closes the gap by 1 - exp(-rate * dt) each call (exponential ease, rate 10/s)', () => {
  const c = cam.create();
  cam.follow(c, 100, 0, 0.1);
  // Remaining gap after one call is exp(-10 * 0.1) = exp(-1) of the original 100.
  near(c.x, 100 * (1 - Math.exp(-1)), 'x after 0.1 s');
  assert.equal(c.y, 0);
});

test('follow is frame-rate independent: two half-steps land where one full step does', () => {
  const once = cam.create();
  cam.follow(once, 50, -30, 0.1);
  const twice = cam.create();
  cam.follow(twice, 50, -30, 0.05);
  cam.follow(twice, 50, -30, 0.05);
  near(twice.x, once.x, 'x');
  near(twice.y, once.y, 'y');
});

test('follow converges on a stationary target over time', () => {
  const c = cam.create();
  for (let i = 0; i < 600; i++) cam.follow(c, 200, -80, 1 / 60); // 10 s at 60 Hz
  near(c.x, 200, 'x converged');
  near(c.y, -80, 'y converged');
});

test('worldToScreen puts the followed point at the viewport centre', () => {
  const c = cam.create();
  cam.snap(c, 300, 450);
  const s = cam.worldToScreen(c, 300, 450, 800, 600);
  // Field comparison: the object is built in the vm realm, so a strict deepEqual against a literal would fail.
  assert.equal(s.x, 400);
  assert.equal(s.y, 300);
});

test('worldToScreen scales offsets by zoom around the viewport centre', () => {
  const c = cam.create();
  c.zoom = 1.5;
  const s = cam.worldToScreen(c, 10, 20, 800, 600);
  near(s.x, 400 + 10 * 1.5, 'x');
  near(s.y, 300 + 20 * 1.5, 'y');
});

test('screenToWorld inverts worldToScreen for a non-trivial camera, zoom and viewport', () => {
  const c = cam.create();
  cam.snap(c, -37.25, 412.5);
  c.zoom = 1.5;
  const viewW = 1023, viewH = 577;
  for (const [wx, wy] of [[0, 0], [-37.25, 412.5], [1000.75, -250.125], [-999, 3.5]]) {
    const s = cam.worldToScreen(c, wx, wy, viewW, viewH);
    const w = cam.screenToWorld(c, s.x, s.y, viewW, viewH);
    near(w.x, wx, `x round trip for ${wx}`);
    near(w.y, wy, `y round trip for ${wy}`);
  }
});

test('screenToWorld inverts worldToScreen the other way round (screen -> world -> screen)', () => {
  const c = cam.create();
  cam.snap(c, 64, 96);
  c.zoom = 2;
  const viewW = 640, viewH = 480;
  for (const [sx, sy] of [[0, 0], [640, 480], [123.5, 77.25]]) {
    const w = cam.screenToWorld(c, sx, sy, viewW, viewH);
    const s = cam.worldToScreen(c, w.x, w.y, viewW, viewH);
    near(s.x, sx, 'sx');
    near(s.y, sy, 'sy');
  }
});

test('clampToMap keeps the viewport inside the map on each axis', () => {
  const c = cam.create();
  c.zoom = 1.5;
  const viewW = 800, viewH = 600; // half-extents in world px at zoom 1.5: 266.67 x 200
  cam.snap(c, -500, 9000);
  cam.clampToMap(c, 2000, 1500, viewW, viewH);
  near(c.x, 800 / 3, 'x pinned to the left edge');
  near(c.y, 1500 - 600 / 3, 'y pinned to the bottom edge');
});

test('clampToMap leaves a camera that is already inside the bounds where it is', () => {
  const c = cam.create();
  c.zoom = 1;
  cam.snap(c, 700, 500);
  cam.clampToMap(c, 2000, 1500, 800, 600);
  assert.equal(c.x, 700);
  assert.equal(c.y, 500);
});

test('clampToMap centres the camera on a map smaller than the viewport instead of clamping', () => {
  const c = cam.create();
  c.zoom = 1;
  cam.snap(c, 10, 10);
  cam.clampToMap(c, 300, 200, 800, 600);
  assert.equal(c.x, 150);
  assert.equal(c.y, 100);
});

test('setShake stores the amplitude and treats negative or NaN input as no shake', () => {
  const c = cam.create();
  cam.setShake(c, 4);
  assert.equal(c.shake, 4);
  cam.setShake(c, -3);
  assert.equal(c.shake, 0);
  cam.setShake(c, NaN);
  assert.equal(c.shake, 0);
});

test('shakeOffset is zero with no shake and does not touch the RNG', () => {
  const c = cam.create();
  let draws = 0;
  const rng = { range() { draws++; return 1; } };
  const off = cam.shakeOffset(c, rng);
  assert.equal(off.x, 0);
  assert.equal(off.y, 0);
  assert.equal(draws, 0);
});

test('shakeOffset stays within the amplitude and is reproducible from the same RNG state', () => {
  const RPG2 = loadRPG(['core/namespace.js', 'core/rng.js', 'render/camera.js']);
  const c = cam.create();
  cam.setShake(c, 6);
  const a = RPG2.core.Rng.create(42), b = RPG2.core.Rng.create(42);
  for (let i = 0; i < 50; i++) {
    const oa = cam.shakeOffset(c, a);
    const ob = cam.shakeOffset(c, b);
    assert.ok(Math.abs(oa.x) <= 6 && Math.abs(oa.y) <= 6, 'within amplitude');
    assert.equal(oa.x, ob.x);
    assert.equal(oa.y, ob.y);
  }
});

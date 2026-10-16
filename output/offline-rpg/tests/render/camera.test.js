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

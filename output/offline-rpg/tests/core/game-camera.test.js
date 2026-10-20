'use strict';
// Regression for the w09-03 blocker: game.render passed dt=0 to renderer.draw, so camera.follow never moved and the
// player walked off-screen on any map larger than the view. Uses the REAL renderer/camera with a stub 2D context.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine, memoryBackend, DT } = require('../integration/helpers.js');

const FRAME = 1 / 60;
const NO_INPUT = () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });

function stubCanvas() {
  const ctx = new Proxy({ fillStyle: '#000', strokeStyle: '#000', globalAlpha: 1, lineWidth: 1 }, {
    get(t, k) { return k in t ? t[k] : (k === 'measureText' ? () => ({ width: 0 }) : () => {}); },
    set(t, k, v) { t[k] = v; return true; }
  });
  return { width: 0, height: 0, getContext: () => ctx };
}

async function setup() {
  const RPG = await loadEngine();
  const renderer = RPG.render.create(stubCanvas());
  renderer.resize(400, 300, 1);
  const clock = { t: 0, now() { return this.t; } };
  const storage = RPG.persistence.storage.createStorage({ backend: memoryBackend() });
  const game = RPG.core.game.create({
    input: { buildFrame: NO_INPUT }, renderer, storage, clock, screens: null
  });
  const frame = () => { clock.t += FRAME; game.render(0.5); };
  return { RPG, game, renderer, clock, frame, cam: renderer.camera };
}

// Distance between the camera and where it would sit when following the player exactly (clamped to the map).
function lag(g) {
  const p = g.game.world.player;
  return Math.hypot(g.cam.x - p.x, g.cam.y - p.y);
}

test('game.render passes the real frame dt, so the camera follows a moving player', async () => {
  const g = await setup();
  g.game.newGame({ seed: 5 });
  const map = g.game.world.area.map;
  const p = g.game.world.player;
  assert.ok(map.w * map.tileSize > g.renderer.viewW / g.cam.zoom * 1.5, 'map is larger than the view');
  g.frame();
  // Move the player well away from the snapped camera, staying inside the map.
  p.x = Math.min(p.x + 150, map.w * map.tileSize - 40); p.prevX = p.x;
  const before = g.cam.x;
  assert.ok(Math.abs(p.x - before) > 50);
  for (let i = 0; i < 90; i++) g.frame();
  assert.notEqual(g.cam.x, before, 'camera moved');
  assert.ok(lag(g) < 5, 'camera converged on the player, lag=' + lag(g));
});

test('camera follow is slowed by a short frame and capped for a stalled one', async () => {
  const g = await setup();
  g.game.newGame({ seed: 5 });
  g.frame();
  const p = g.game.world.player;
  const startX = g.cam.x;
  p.x += 100; p.prevX = p.x;
  g.clock.t += 0.01; g.game.render(0.5);
  const small = Math.abs(g.cam.x - startX);
  assert.ok(small > 0 && small < 100, 'partial step for a 10ms frame');
  const mid = lag(g);
  g.clock.t += 60; g.game.render(0.5); // tab stalled for a minute: dt is capped (0.1s), not an exact catch-up
  assert.ok(lag(g) < mid, 'still closing in');
  assert.ok(lag(g) > 0.01, 'a stall does not teleport the camera onto the player');
});

test('the camera snaps onto the player when an area is entered', async () => {
  const g = await setup();
  g.game.newGame({ seed: 5 });
  g.frame();
  g.game.world.progress.areasUnlocked.forest = true;
  g.RPG.world.areas.enter(g.game.world, 'forest', 'town');
  const p = g.game.world.player;
  assert.equal(g.cam.x, p.x);
  assert.equal(g.cam.y, p.y);
  assert.equal(p.prevX, p.x, 'interpolation start moved too');
});

test('new game and load snap the camera; respawn snaps it back to the start area', async () => {
  const g = await setup();
  g.game.newGame({ seed: 5 });
  g.cam.x = 99999; g.cam.y = -5000; // stale camera from a previous session
  g.game.newGame({ seed: 6 });
  assert.equal(g.cam.x, g.game.world.player.x);
  assert.equal(g.cam.y, g.game.world.player.y);

  assert.ok(g.game.saveToSlot(1).ok !== false);
  g.cam.x = 99999;
  const r = g.game.loadFromSlot(1);
  assert.ok(r.ok, JSON.stringify(r));
  assert.equal(g.cam.x, g.game.world.player.x);

  g.game.world.player.hp = 0; g.game.world.player.dead = true;
  g.game.tick(DT);
  if (g.game.state === 'dead') {
    g.cam.x = 99999;
    g.game.respawn();
    assert.equal(g.cam.x, g.game.world.player.x);
  }
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const MAP_FILES = ['world/tilemap.js', 'render/palette.js', 'render/draw-map.js'];
const RPG = loadRPG(MAP_FILES);
const { drawMap } = RPG.render;
const { create, fromRows } = RPG.world.tilemap;
const pal = RPG.render.palette;

// Plain-JSON comparison: objects built in the vm context have another realm's prototypes.
const plain = (v) => JSON.parse(JSON.stringify(v));

const TS = 32;

/** Records each fillRect with the fill style active at the time. Other context methods are no-ops. */
function recordingContext() {
  const calls = [];
  const ctx = {
    fillStyle: null,
    fillRect(x, y, w, h) { calls.push({ x, y, w, h, style: this.fillStyle }); },
  };
  return { ctx, calls };
}

/** A map where every tile is floor. */
function floorMap(w, h) {
  return create(w, h, TS);
}

test('culling: a small viewport on a large map draws only the visible tiles', () => {
  const map = floorMap(100, 100);
  // Camera centred on tile (50,50); 320 CSS px at zoom 1 is 10 tiles wide/high, so the visible span is
  // tiles 45..55 on each axis: 11 x 11 = 121 tiles, one fillRect each for floor.
  const cam = { x: 50 * TS + TS / 2, y: 50 * TS + TS / 2, zoom: 1 };
  const { ctx, calls } = recordingContext();
  drawMap(ctx, map, cam, 320, 320, pal);
  assert.equal(calls.length, 11 * 11);
  for (const c of calls) {
    assert.ok(c.x >= 45 * TS && c.x <= 55 * TS, `x=${c.x} outside visible columns`);
    assert.ok(c.y >= 45 * TS && c.y <= 55 * TS, `y=${c.y} outside visible rows`);
  }
});

test('culling: zoom changes how many tiles fit in the view', () => {
  const map = floorMap(100, 100);
  const cam = { x: 50 * TS, y: 50 * TS, zoom: 2 };
  const { ctx, calls } = recordingContext();
  drawMap(ctx, map, cam, 320, 320, pal);
  // Half-width is 320/2/2 = 80 px = 2.5 tiles: floor(47.5)..floor(52.5) = tiles 47..52, 6 per axis.
  assert.equal(calls.length, 6 * 6);
});

test('walls cost four fills (body, top face, bottom rim, right rim); floors cost one', () => {
  const map = fromRows(['##', '.#'], TS);
  const cam = { x: TS, y: TS, zoom: 1 };
  const { ctx, calls } = recordingContext();
  drawMap(ctx, map, cam, 1000, 1000, pal);
  // 4 tiles: 3 walls (4 fills each) + 1 floor (1 fill).
  assert.equal(calls.length, 3 * 4 + 1);
  assert.ok(calls.some((c) => c.style === pal.wallTop));
  assert.ok(calls.some((c) => c.style === pal.wallEdge));
});

test('deterministic: drawing the same map twice produces identical calls', () => {
  // Every tile type in one map, so the test covers the whole drawer, not just floors.
  const map = fromRows([
    '.#~+>,.#~+>,',
    '#.S.#.~.,.+.',
    '.~.#.~.,.+.>',
    '+>,.#.~.,.#.',
  ], TS);
  const cam = { x: 6 * TS, y: 6 * TS, zoom: 1.5 };
  const first = recordingContext();
  const second = recordingContext();
  drawMap(first.ctx, map, cam, 800, 600, pal);
  drawMap(second.ctx, map, cam, 800, 600, pal);
  assert.deepEqual(plain(second.calls), plain(first.calls));
});

test('floor variation: some floors take the alt tone, most do not, and the split is stable', () => {
  const map = floorMap(16, 16);
  const cam = { x: 8 * TS, y: 8 * TS, zoom: 1 };
  const { ctx, calls } = recordingContext();
  drawMap(ctx, map, cam, 2000, 2000, pal);
  const alt = calls.filter((c) => c.style === pal.floorAlt).length;
  const base = calls.filter((c) => c.style === pal.floor).length;
  assert.equal(alt + base, calls.length);
  assert.ok(alt > 0, 'expected some alt-tone floors');
  assert.ok(base > alt, 'alt tone should stay the minority');
});

test('a view entirely off the map draws nothing and does not throw', () => {
  const map = floorMap(10, 10);
  const far = [
    { x: -1e6, y: -1e6 },
    { x: 1e6, y: 1e6 },
    { x: -1e6, y: 1e6 },
    { x: 1e6, y: -1e6 },
  ];
  for (const at of far) {
    const { ctx, calls } = recordingContext();
    assert.doesNotThrow(() => drawMap(ctx, map, { x: at.x, y: at.y, zoom: 1 }, 320, 320, pal));
    assert.equal(calls.length, 0);
  }
});

test('a viewport that overhangs the map edge draws only the on-map tiles', () => {
  const map = floorMap(4, 4);
  const cam = { x: 0, y: 0, zoom: 1 };
  const { ctx, calls } = recordingContext();
  drawMap(ctx, map, cam, 1000, 1000, pal);
  assert.equal(calls.length, 16);
});

test('palette defaults to RPG.render.palette when omitted', () => {
  const map = fromRows(['#'], TS);
  const { ctx, calls } = recordingContext();
  drawMap(ctx, map, { x: 16, y: 16, zoom: 1 }, 200, 200);
  assert.equal(calls[0].style, pal.wall);
});

test('renderer.draw hands the world map to drawMap', () => {
  // Load the renderer on top of the map drawer, then spy on drawMap to confirm the call path.
  const R = loadRPG(['world/tilemap.js', 'render/palette.js', 'render/camera.js', 'render/draw-map.js', 'render/renderer.js']);
  const real = R.render.drawMap;
  const seen = [];
  R.render.drawMap = (...args) => { seen.push(args); return real(...args); };

  const calls = [];
  const canvas = {
    width: 0, height: 0,
    getContext() {
      return {
        fillStyle: null, globalAlpha: 1,
        setTransform() {}, fillRect(x, y, w, h) { calls.push({ x, y, w, h, style: this.fillStyle }); },
        beginPath() {}, arc() {}, ellipse() {}, fill() {}, stroke() {}, moveTo() {}, lineTo() {}, closePath() {},
      };
    },
  };
  const renderer = R.render.create(canvas);
  renderer.resize(320, 320, 1);
  const map = fromRows(['..', '.#'], TS);
  const world = {
    area: { map },
    player: { x: 16, y: 16, prevX: 16, prevY: 16, r: 8, facing: 0 },
  };
  renderer.draw(world, 1, 0);

  assert.equal(seen.length, 1);
  assert.equal(seen[0][1], map);
  assert.ok(calls.some((c) => c.style === R.render.palette.wall), 'wall tile was drawn');
});

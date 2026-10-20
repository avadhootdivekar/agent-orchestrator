'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');
const { recordingContext } = require('./fakectx.js');

const RPG = loadRPG(['render/palette.js', 'render/draw-shapes.js']);
const S = RPG.render.shapes;
const pal = RPG.render.palette;

const TWO_PI = Math.PI * 2;
const X = 100;
const Y = 50;
const R = 20;

/**
 * Like recordingContext, but throws on a negative or non-finite radius the way a real Canvas does (IndexSizeError),
 * so a degenerate size that slips past the guards fails the test instead of passing silently.
 */
function strictContext() {
  const rec = recordingContext();
  const checkRadius = (r) => {
    if (!(typeof r === 'number' && isFinite(r) && r >= 0)) throw new RangeError(`invalid radius ${r}`);
  };
  const { ctx } = rec;
  const arc = ctx.arc;
  const ellipse = ctx.ellipse;
  ctx.arc = (cx, cy, r, a0, a1) => { checkRadius(r); return arc.call(ctx, cx, cy, r, a0, a1); };
  ctx.ellipse = (cx, cy, rx, ry, rot, a0, a1) => {
    checkRadius(rx);
    checkRadius(ry);
    return ellipse.call(ctx, cx, cy, rx, ry, rot, a0, a1);
  };
  return rec;
}

/** Calls that a drawer makes for real, i.e. everything except the save/restore bookkeeping. */
const drawOps = (calls) => calls.filter((c) => c.op !== 'save' && c.op !== 'restore');

/** Vertices of the polygon path, in the order they were traced. */
function polygonVertices(calls) {
  return calls.filter((c) => c.op === 'moveTo' || c.op === 'lineTo').map((c) => [c.x, c.y]);
}

/** Runs a primitive with valid arguments and checks it left the context balanced. */
function assertBalanced(calls, ctx) {
  const saves = calls.filter((c) => c.op === 'save').length;
  const restores = calls.filter((c) => c.op === 'restore').length;
  assert.equal(saves, restores, 'save/restore count');
  assert.equal(ctx.saveDepth, 0, 'depth after the primitive');
  assert.equal(ctx.minSaveDepth, 0, 'depth never dropped below zero');
  assert.ok(saves >= 1, 'primitive wrapped its work in save()');
}

test('outlinedCircle fills the path then strokes it, in its own save/restore block', () => {
  const { ctx, calls } = strictContext();
  S.outlinedCircle(ctx, X, Y, R, pal.enemy, pal.playerOutline, 2);
  assert.deepEqual(calls.map((c) => c.op), ['save', 'beginPath', 'arc', 'fill', 'stroke', 'restore']);
  const arc = calls.find((c) => c.op === 'arc');
  assert.deepEqual([arc.cx, arc.cy, arc.r, arc.a0, arc.a1], [X, Y, R, 0, TWO_PI]);
  assert.equal(calls.find((c) => c.op === 'fill').style, pal.enemy);
  const stroke = calls.find((c) => c.op === 'stroke');
  assert.equal(stroke.style, pal.playerOutline);
  assert.equal(stroke.lineWidth, 2);
  assertBalanced(calls, ctx);
});

test('outlinedCircle with no stroke colour fills only, and with no fill colour strokes only', () => {
  const fillOnly = strictContext();
  S.outlinedCircle(fillOnly.ctx, X, Y, R, pal.player, undefined, 2);
  assert.deepEqual(fillOnly.calls.filter((c) => c.op === 'fill' || c.op === 'stroke').map((c) => c.op), ['fill']);
  const strokeOnly = strictContext();
  S.outlinedCircle(strokeOnly.ctx, X, Y, R, null, pal.ui, 1);
  assert.deepEqual(strokeOnly.calls.filter((c) => c.op === 'fill' || c.op === 'stroke').map((c) => c.op), ['stroke']);
});

test('outlinedRect fills the rect with its fill colour and outlines it with the stroke colour', () => {
  const { ctx, calls } = strictContext();
  S.outlinedRect(ctx, 10, 20, 30, 40, pal.floor, pal.playerOutline, 2);
  assert.deepEqual(drawOps(calls).map((c) => c.op), ['fillRect', 'strokeRect']);
  assert.deepEqual([calls[1].x, calls[1].y, calls[1].w, calls[1].h], [10, 20, 30, 40]);
  assert.equal(calls[1].style, pal.floor);
  assert.equal(calls[2].style, pal.playerOutline);
  assertBalanced(calls, ctx);
});

test('rotatedPolygon traces the named shapes with the right vertex count and a vertex at 12 o\'clock', () => {
  const expected = { triangle: 3, diamond: 4, hex: 6 };
  for (const [name, sides] of Object.entries(expected)) {
    const { ctx, calls } = strictContext();
    S.rotatedPolygon(ctx, X, Y, R, name, 0, pal.enemy, null, 0);
    const verts = polygonVertices(calls);
    assert.equal(verts.length, sides, `${name} vertex count`);
    assert.equal(calls.filter((c) => c.op === 'closePath').length, 1);
    assert.ok(Math.abs(verts[0][0] - X) < 1e-9 && Math.abs(verts[0][1] - (Y - R)) < 1e-9, `${name} starts at 12 o'clock`);
    for (const [vx, vy] of verts) {
      assert.ok(Math.abs(Math.hypot(vx - X, vy - Y) - R) < 1e-9, `${name} vertex lies on the circumradius`);
    }
    assertBalanced(calls, ctx);
  }
});

test('rotatedPolygon accepts a numeric side count and applies rotation to every vertex', () => {
  const { ctx, calls } = strictContext();
  S.rotatedPolygon(ctx, X, Y, R, 5, Math.PI, pal.hazard, null, 0);
  const verts = polygonVertices(calls);
  assert.equal(verts.length, 5);
  // Rotating by pi puts the first vertex at 6 o'clock instead of 12.
  assert.ok(Math.abs(verts[0][0] - X) < 1e-9 && Math.abs(verts[0][1] - (Y + R)) < 1e-9);
});

test('rotatedPolygon ignores an unknown shape name or an out-of-range side count', () => {
  for (const shape of ['pentagon', 2, 0, 1000, NaN, undefined]) {
    const { ctx, calls } = strictContext();
    assert.doesNotThrow(() => S.rotatedPolygon(ctx, X, Y, R, shape, 0, pal.enemy, pal.ui, 1));
    assert.equal(drawOps(calls).length, 0, `shape ${String(shape)} drew something`);
    assert.equal(ctx.saveDepth, 0);
  }
});

test('softShadow draws two ellipses at the feet, using the shadow token by default', () => {
  const { ctx, calls } = strictContext();
  S.softShadow(ctx, X, Y, 12, 6, 1);
  const ellipses = calls.filter((c) => c.op === 'ellipse');
  assert.equal(ellipses.length, 2);
  assert.ok(ellipses.every((e) => e.cx === X && e.cy === Y), 'centred on the feet');
  assert.ok(ellipses[0].rx > ellipses[1].rx, 'outer layer is wider than the inner one');
  assert.ok(calls.filter((c) => c.op === 'fill').every((f) => f.style === pal.shadow));
  assertBalanced(calls, ctx);
});

test('softShadow scales its layers by alpha and honours a supplied colour', () => {
  const low = strictContext();
  S.softShadow(low.ctx, X, Y, 12, 6, 0.5, pal.wall);
  const fills = low.calls.filter((c) => c.op === 'fill');
  assert.equal(fills.length, 2);
  assert.ok(Math.abs(fills[0].alpha - 0.5 * 0.4) < 1e-9);
  assert.ok(Math.abs(fills[1].alpha - 0.5 * 0.6) < 1e-9);
  assert.ok(fills.every((f) => f.style === pal.wall));
});

test('glow fades a radial gradient from the colour to transparent at radius r', () => {
  const { ctx, calls } = strictContext();
  S.glow(ctx, X, Y, R, pal.hazard, 0.3);
  const grad = calls.find((c) => c.op === 'createRadialGradient');
  assert.deepEqual([grad.x0, grad.y0, grad.r0, grad.x1, grad.y1, grad.r1], [X, Y, 0, X, Y, R]);
  assert.deepEqual(grad.stops.map((s) => [s.offset, s.color]), [[0, pal.hazard], [1, 'transparent']]);
  const fill = calls.find((c) => c.op === 'fill');
  assert.equal(fill.alpha, 0.3);
  assertBalanced(calls, ctx);
});

test('glow clamps alpha into [0, 1]', () => {
  const { ctx, calls } = strictContext();
  S.glow(ctx, X, Y, R, pal.hazard, 7);
  assert.equal(calls.find((c) => c.op === 'fill').alpha, 1);
});

test('ring strokes a full circle at the given width and colour', () => {
  const { ctx, calls } = strictContext();
  S.ring(ctx, X, Y, R, 3, pal.enemy, 0.8);
  assert.deepEqual(drawOps(calls).map((c) => c.op), ['beginPath', 'arc', 'stroke']);
  const arc = calls.find((c) => c.op === 'arc');
  assert.deepEqual([arc.a0, arc.a1], [0, TWO_PI]);
  const stroke = calls.find((c) => c.op === 'stroke');
  assert.equal(stroke.style, pal.enemy);
  assert.equal(stroke.lineWidth, 3);
  assert.equal(stroke.alpha, 0.8);
  assertBalanced(calls, ctx);
});

test('arc strokes between the two angles it is given', () => {
  const { ctx, calls } = strictContext();
  S.arc(ctx, X, Y, R, -Math.PI / 2, Math.PI / 2, pal.hazard, 2);
  const arc = calls.find((c) => c.op === 'arc');
  assert.deepEqual([arc.a0, arc.a1], [-Math.PI / 2, Math.PI / 2]);
  assert.deepEqual(drawOps(calls).map((c) => c.op), ['beginPath', 'arc', 'stroke']);
  assertBalanced(calls, ctx);
});

test('cone traces a closed pie slice from the centre, spanning halfArc either side of dir', () => {
  const { ctx, calls } = strictContext();
  S.cone(ctx, X, Y, R, 0, Math.PI / 4, pal.hazard, null, 0);
  const ops = drawOps(calls);
  assert.deepEqual(ops.map((c) => c.op), ['beginPath', 'moveTo', 'arc', 'closePath', 'fill']);
  assert.deepEqual([ops[1].x, ops[1].y], [X, Y]);
  const arc = ops[2];
  assert.ok(Math.abs(arc.a0 + Math.PI / 4) < 1e-9 && Math.abs(arc.a1 - Math.PI / 4) < 1e-9);
  assertBalanced(calls, ctx);
});

test('cone clamps a half-angle above pi to a full circle rather than sweeping past it', () => {
  const { ctx, calls } = strictContext();
  S.cone(ctx, X, Y, R, 0, 10, pal.hazard, null, 0);
  const arc = calls.find((c) => c.op === 'arc');
  assert.ok(Math.abs(arc.a1 - arc.a0 - TWO_PI) < 1e-9);
});

test('every primitive leaves the context balanced across a sweep of valid sizes', () => {
  const draws = [
    (c, s) => S.outlinedCircle(c, X, Y, R * s, pal.enemy, pal.playerOutline, 2),
    (c, s) => S.outlinedRect(c, X, Y, R * s, R * s, pal.floor, pal.playerOutline, 2),
    (c, s) => S.rotatedPolygon(c, X, Y, R * s, 'hex', 0.3, pal.enemy, pal.ui, 1),
    (c, s) => S.softShadow(c, X, Y, R * s, (R / 2) * s, 0.9),
    (c, s) => S.glow(c, X, Y, R * s, pal.hazard, 0.2),
    (c, s) => S.ring(c, X, Y, R * s, 1, pal.ui),
    (c, s) => S.arc(c, X, Y, R * s, 0, 1, pal.ui, 1),
    (c, s) => S.cone(c, X, Y, R * s, 0, 0.5, pal.hazard, pal.ui, 1)
  ];
  for (const draw of draws) {
    for (const scale of [0.25, 1, 4]) {
      const { ctx, calls } = strictContext();
      assert.doesNotThrow(() => draw(ctx, scale));
      assert.ok(drawOps(calls).length > 0, 'a valid size draws something');
      assertBalanced(calls, ctx);
    }
  }
});

test('zero, negative and non-finite sizes draw nothing and do not throw', () => {
  const bad = [0, -1, -R, NaN, Infinity, undefined, null];
  const cases = [];
  for (const v of bad) {
    cases.push(
      ['outlinedCircle', (c) => S.outlinedCircle(c, X, Y, v, pal.enemy, pal.ui, 2)],
      ['outlinedRect w', (c) => S.outlinedRect(c, X, Y, v, R, pal.enemy, pal.ui, 2)],
      ['outlinedRect h', (c) => S.outlinedRect(c, X, Y, R, v, pal.enemy, pal.ui, 2)],
      ['rotatedPolygon', (c) => S.rotatedPolygon(c, X, Y, v, 'triangle', 0, pal.enemy, pal.ui, 1)],
      ['softShadow rx', (c) => S.softShadow(c, X, Y, v, R, 1)],
      ['softShadow ry', (c) => S.softShadow(c, X, Y, R, v, 1)],
      ['glow', (c) => S.glow(c, X, Y, v, pal.hazard, 1)],
      ['ring r', (c) => S.ring(c, X, Y, v, 2, pal.ui)],
      ['ring w', (c) => S.ring(c, X, Y, R, v, pal.ui)],
      ['arc r', (c) => S.arc(c, X, Y, v, 0, 1, pal.ui, 1)],
      ['cone r', (c) => S.cone(c, X, Y, v, 0, 0.5, pal.hazard, pal.ui, 1)],
      ['cone halfArc', (c) => S.cone(c, X, Y, R, 0, v, pal.hazard, pal.ui, 1)]
    );
  }
  // Angles are not sizes: 0 and negative values are valid, so only the non-finite inputs are degenerate here.
  for (const v of [NaN, Infinity, undefined, null]) {
    cases.push(['arc angle', (c) => S.arc(c, X, Y, R, v, 1, pal.ui, 1)]);
  }
  cases.push(
    ['outlinedCircle centre',(c) => S.outlinedCircle(c, NaN, Y, R, pal.enemy, pal.ui, 2)],
    ['outlinedRect centre', (c) => S.outlinedRect(c, X, undefined, R, R, pal.enemy, pal.ui, 2)],
    ['cone dir', (c) => S.cone(c, X, Y, R, NaN, 0.5, pal.hazard, pal.ui, 1)],
    ['glow no colour', (c) => S.glow(c, X, Y, R, undefined, 1)]
  );
  for (const [label, draw] of cases) {
    const { ctx, calls } = strictContext();
    assert.doesNotThrow(() => draw(ctx), label);
    assert.equal(calls.length, 0, `${label} recorded ${calls.length} call(s)`);
    assert.equal(ctx.saveDepth, 0, `${label} left the context unbalanced`);
  }
});

test('zero line width outlines are skipped while the fill still draws', () => {
  const { ctx, calls } = strictContext();
  S.outlinedCircle(ctx, X, Y, R, pal.enemy, pal.ui, 0);
  assert.deepEqual(calls.filter((c) => c.op === 'fill' || c.op === 'stroke').map((c) => c.op), ['fill']);
});

test('the module exposes exactly the documented primitives and cannot be reassigned', () => {
  assert.deepEqual(Object.keys(S).sort(), [
    'arc', 'cone', 'glow', 'outlinedCircle', 'outlinedRect', 'ring', 'rotatedPolygon', 'softShadow'
  ]);
  assert.ok(Object.isFrozen(S));
});

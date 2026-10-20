/* Geometric primitives for the flat style (ARCHITECTURE §4, render/draw-shapes). Colours are passed in as strings,
   normally tokens from RPG.render.palette. Each primitive is wrapped in save()/restore(). A zero, negative or
   non-finite size draws nothing and returns before save(), because Canvas throws on negative radii. */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  var TWO_PI = Math.PI * 2;
  var START_ANGLE = -Math.PI / 2; // polygons point up (a vertex at 12 o'clock) before rot is applied
  var MAX_SIDES = 64;
  var POLY_SIDES = { triangle: 3, diamond: 4, hex: 6 };
  var SHADOW_OUTER_SCALE = 1.2;
  var SHADOW_OUTER_ALPHA = 0.4;
  var SHADOW_INNER_SCALE = 0.7;
  var SHADOW_INNER_ALPHA = 0.6;
  var GLOW_EDGE = 'transparent';
  var HALF_TURN = Math.PI;

  function finite(v) { return typeof v === 'number' && isFinite(v); }
  function usable(v) { return finite(v) && v > 0; }
  function clamp01(v) { return v > 0 ? (v < 1 ? v : 1) : 0; }
  function alphaOf(v) { return finite(v) ? clamp01(v) : 1; }

  /** Fills and/or strokes the current path. A missing fill or stroke colour skips that pass. */
  function paint(ctx, fill, stroke, lineW) {
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke && usable(lineW)) { ctx.strokeStyle = stroke; ctx.lineWidth = lineW; ctx.stroke(); }
  }

  /** Circle with optional fill and outline, both as palette colours. */
  function outlinedCircle(ctx, x, y, r, fill, stroke, lineW) {
    if (!finite(x) || !finite(y) || !usable(r)) return;
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, r, 0, TWO_PI);
    paint(ctx, fill, stroke, lineW);
    ctx.restore();
  }

  /** Axis-aligned rectangle (x, y is the top-left corner) with optional fill and outline. */
  function outlinedRect(ctx, x, y, w, h, fill, stroke, lineW) {
    if (!finite(x) || !finite(y) || !usable(w) || !usable(h)) return;
    ctx.save();
    if (fill) { ctx.fillStyle = fill; ctx.fillRect(x, y, w, h); }
    if (stroke && usable(lineW)) { ctx.strokeStyle = stroke; ctx.lineWidth = lineW; ctx.strokeRect(x, y, w, h); }
    ctx.restore();
  }

  /**
   * Regular polygon of circumradius r. `shape` is a side count (3 or more) or one of 'triangle' | 'diamond' | 'hex'.
   * Vertices start at 12 o'clock and step clockwise; `rot` (radians) turns the whole shape.
   */
  function rotatedPolygon(ctx, x, y, r, shape, rot, fill, stroke, lineW) {
    var sides = typeof shape === 'string' ? POLY_SIDES[shape] : Math.floor(shape);
    if (!finite(x) || !finite(y) || !usable(r) || !(sides >= 3 && sides <= MAX_SIDES)) return;
    var base = START_ANGLE + (finite(rot) ? rot : 0);
    ctx.save();
    ctx.beginPath();
    for (var i = 0; i < sides; i++) {
      var a = base + (i * TWO_PI) / sides;
      var px = x + r * Math.cos(a);
      var py = y + r * Math.sin(a);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
    paint(ctx, fill, stroke, lineW);
    ctx.restore();
  }

  /**
   * Contact shadow under a character: an ellipse at the feet, built from a wide faint layer and a narrower denser one.
   * `alpha` scales both layers. `color` defaults to the palette's shadow token.
   */
  function softShadow(ctx, x, y, rx, ry, alpha, color) {
    if (!finite(x) || !finite(y) || !usable(rx) || !usable(ry)) return;
    var a = alphaOf(alpha);
    ctx.save();
    ctx.fillStyle = color || RPG.render.palette.shadow;
    ctx.globalAlpha = a * SHADOW_OUTER_ALPHA;
    ctx.beginPath();
    ctx.ellipse(x, y, rx * SHADOW_OUTER_SCALE, ry * SHADOW_OUTER_SCALE, 0, 0, TWO_PI);
    ctx.fill();
    ctx.globalAlpha = a * SHADOW_INNER_ALPHA;
    ctx.beginPath();
    ctx.ellipse(x, y, rx * SHADOW_INNER_SCALE, ry * SHADOW_INNER_SCALE, 0, 0, TWO_PI);
    ctx.fill();
    ctx.restore();
  }

  /** Soft radial glow: `color` at the centre, fading to transparent at radius r. Keep alpha low to stay restrained. */
  function glow(ctx, x, y, r, color, alpha) {
    if (!finite(x) || !finite(y) || !usable(r) || !color) return;
    ctx.save();
    var g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, color);
    g.addColorStop(1, GLOW_EDGE);
    ctx.globalAlpha = alphaOf(alpha);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, TWO_PI);
    ctx.fill();
    ctx.restore();
  }

  /** Stroked circle, for range and area-of-effect indicators. `w` is the line width. */
  function ring(ctx, x, y, r, w, color, alpha) {
    if (!finite(x) || !finite(y) || !usable(r) || !usable(w) || !color) return;
    ctx.save();
    ctx.globalAlpha = alphaOf(alpha);
    ctx.strokeStyle = color;
    ctx.lineWidth = w;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, TWO_PI);
    ctx.stroke();
    ctx.restore();
  }

  /** Stroked arc from angle a0 to a1 (radians, clockwise), e.g. for a telegraph edge. */
  function arc(ctx, x, y, r, a0, a1, color, w) {
    if (!finite(x) || !finite(y) || !usable(r) || !finite(a0) || !finite(a1) || !usable(w) || !color) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = w;
    ctx.beginPath();
    ctx.arc(x, y, r, a0, a1);
    ctx.stroke();
    ctx.restore();
  }

  /**
   * Filled pie slice of radius r, centred on angle `dir`, spanning `halfArc` either side of it. Used for cone-shaped
   * telegraphs. A half-angle above π is clamped to a full circle.
   */
  function cone(ctx, x, y, r, dir, halfArc, fill, stroke, lineW) {
    if (!finite(x) || !finite(y) || !usable(r) || !usable(halfArc) || !finite(dir)) return;
    var half = Math.min(halfArc, HALF_TURN);
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.arc(x, y, r, dir - half, dir + half);
    ctx.closePath();
    paint(ctx, fill, stroke, lineW);
    ctx.restore();
  }

  RPG.render.shapes = Object.freeze({
    outlinedCircle: outlinedCircle,
    outlinedRect: outlinedRect,
    rotatedPolygon: rotatedPolygon,
    softShadow: softShadow,
    glow: glow,
    ring: ring,
    arc: arc,
    cone: cone
  });
})(globalThis.RPG = globalThis.RPG || {});

/* Effect drawers (INTEGRATION-PLAN W4-10, ARCHITECTURE §4 render/draw-effects): telegraphs, aoe rings, whirl and nova
   shapes, floating damage numbers, particle bursts and the screen-edge hurt flash. Read-only over the state they are given:
   the sim owns ages, lifetimes and removal, this file only draws what is there. Built on RPG.render.shapes.
   Time fields are seconds. `age` counts up from 0; `t`/`duration` describe a windup that fills as it runs. */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  var TWO_PI = Math.PI * 2;
  var DAMAGE_NUMBER_CAP = 40;        // matches sim/sim.js DAMAGE_NUMBER_CAP; the newest entries win
  var DAMAGE_NUMBER_LIFE = 1.2;      // seconds, matches sim/sim.js DAMAGE_NUMBER_LIFE
  var DAMAGE_NUMBER_RISE = 26;       // pixels a number floats up over its life
  var DAMAGE_FONT = 'bold 14px sans-serif';
  var CRIT_FONT = 'bold 18px sans-serif';
  var CRIT_COLOUR_TOKEN = 'hazard';
  var NORMAL_COLOUR_TOKEN = 'ui';

  var PARTICLE_CAP = 300;            // ARCHITECTURE §7: particles at most 300 live, scaled by quality
  var QUALITY_SCALE = { low: 0.25, med: 0.6, high: 1 };
  var DEFAULT_QUALITY = 'med';       // unknown quality strings fall back here
  var PARTICLE_LIFE_MIN = 0.3;
  var PARTICLE_LIFE_MAX = 0.6;
  var PARTICLE_SPEED_MIN = 40;       // px/s
  var PARTICLE_SPEED_MAX = 120;
  var PARTICLE_RADIUS_MIN = 1;
  var PARTICLE_RADIUS_MAX = 2.5;
  var PARTICLE_DRAG = 3;             // per-second velocity decay, so bursts settle instead of flying off

  var TELEGRAPH_FILL_ALPHA = 0.35;
  var TELEGRAPH_LINE_WIDTH = 2;
  var CONE_HALF_ARC = 0.9;           // radians either side of the aim; mirrors sim/monsters.js MELEE_HALF_ARC
  var RING_WIDTH = 3;
  var NOVA_FILL_ALPHA = 0.3;
  var WHIRL_BLADES = 3;
  var WHIRL_ARC = 0.9;               // radians of each blade arc
  var WHIRL_WIDTH = 3;

  var HURT_FLASH_PEAK = 0.35;        // maximum alpha of the edge vignette; kept low so it never blinds
  var HURT_FLASH_SECONDS = 0.4;      // one pulse per hit, decaying smoothly; callers do not re-trigger it while it runs

  function finite(v) { return typeof v === 'number' && isFinite(v); }
  function usable(v) { return finite(v) && v > 0; }
  function clamp01(v) { return v > 0 ? (v < 1 ? v : 1) : 0; }
  function easeOut(p) { var q = 1 - p; return 1 - q * q; }

  /** Fraction of a windup that has filled: t/duration in [0, 1]. A missing or zero duration reads as fully charged. */
  function telegraphFill(t, duration) {
    if (!usable(duration)) return 1;
    return finite(t) ? clamp01(t / duration) : 0;
  }

  /** Canvas-style colour lookup: a palette token, or the raw colour string if it is not a token. */
  function colourOf(token) {
    var pal = RPG.render.palette || {};
    return pal[token] || token;
  }

  /** Fills and/or strokes a closed polygon given as [x, y] pairs. */
  function polygon(ctx, pts, fill, stroke, lineW) {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke && usable(lineW)) { ctx.strokeStyle = stroke; ctx.lineWidth = lineW; ctx.stroke(); }
    ctx.restore();
  }

  /**
   * Ground telegraph for an incoming attack. `tg` = { shape: 'circle'|'line'|'cone', x, y, dirX, dirY, radius, range,
   * halfArc?, t, duration }. The outline always shows the full shape; the fill grows from the origin with t/duration.
   * 'circle' uses radius; 'line' is a capsule-like strip of length `range` and half-width `radius` along (dirX, dirY);
   * 'cone' is a pie of length `range` spanning `halfArc` either side of (dirX, dirY).
   */
  function drawTelegraph(ctx, tg, colour) {
    if (!tg || !finite(tg.x) || !finite(tg.y) || !colour) return;
    var f = telegraphFill(tg.t, tg.duration);
    var dir = Math.atan2(tg.dirY || 0, tg.dirX || 0);
    ctx.save();
    ctx.globalAlpha = TELEGRAPH_FILL_ALPHA;
    if (tg.shape === 'line' && usable(tg.range) && usable(tg.radius)) {
      var len = tg.range, hw = tg.radius;
      var ux = Math.cos(dir), uy = Math.sin(dir), nx = -uy * hw, ny = ux * hw;
      var strip = function (s) {
        var ex = tg.x + ux * len * s, ey = tg.y + uy * len * s;
        return [[tg.x + nx, tg.y + ny], [ex + nx, ey + ny], [ex - nx, ey - ny], [tg.x - nx, tg.y - ny]];
      };
      if (f > 0) polygon(ctx, strip(f), colour, null);
      ctx.globalAlpha = 1;
      polygon(ctx, strip(1), null, colour, TELEGRAPH_LINE_WIDTH);
    } else if (tg.shape === 'cone' && usable(tg.range)) {
      var half = usable(tg.halfArc) ? tg.halfArc : CONE_HALF_ARC;
      if (f > 0) RPG.render.shapes.cone(ctx, tg.x, tg.y, tg.range * f, dir, half, colour, null);
      ctx.globalAlpha = 1;
      RPG.render.shapes.cone(ctx, tg.x, tg.y, tg.range, dir, half, null, colour, TELEGRAPH_LINE_WIDTH);
    } else if (usable(tg.radius)) {
      if (f > 0) RPG.render.shapes.outlinedCircle(ctx, tg.x, tg.y, tg.radius * f, colour, null);
      ctx.globalAlpha = 1;
      RPG.render.shapes.ring(ctx, tg.x, tg.y, tg.radius, TELEGRAPH_LINE_WIDTH, colour, 1);
    }
    ctx.restore();
  }

  /** Expanding ring for an aoe that has landed. `age`/`ttl` in seconds: it grows to `radius` and fades out. */
  function drawAoeRing(ctx, x, y, radius, age, ttl, colour) {
    if (!finite(x) || !finite(y) || !usable(radius) || !usable(ttl) || !colour) return;
    var p = clamp01((finite(age) ? age : 0) / ttl);
    if (p >= 1) return;
    RPG.render.shapes.ring(ctx, x, y, radius * easeOut(p), RING_WIDTH * (1 - p) + 1, colour, 1 - p);
  }

  /** Expanding filled nova with a bright leading ring. Same timing as drawAoeRing. */
  function drawNova(ctx, x, y, radius, age, ttl, colour) {
    if (!finite(x) || !finite(y) || !usable(radius) || !usable(ttl) || !colour) return;
    var p = clamp01((finite(age) ? age : 0) / ttl);
    if (p >= 1) return;
    var r = radius * easeOut(p);
    RPG.render.shapes.glow(ctx, x, y, r, colour, NOVA_FILL_ALPHA * (1 - p));
    RPG.render.shapes.ring(ctx, x, y, r, RING_WIDTH, colour, 1 - p);
  }

  /** Whirlwind: three rotating blade arcs at `radius`. `phase` is the rotation in radians (drive it from world.time). */
  function drawWhirl(ctx, x, y, radius, phase, colour) {
    if (!finite(x) || !finite(y) || !usable(radius) || !colour) return;
    var rot = finite(phase) ? phase : 0;
    for (var i = 0; i < WHIRL_BLADES; i++) {
      var a0 = rot + (i * TWO_PI) / WHIRL_BLADES;
      RPG.render.shapes.arc(ctx, x, y, radius, a0, a0 + WHIRL_ARC, colour, WHIRL_WIDTH);
    }
  }

  /** Floating damage numbers. `list` entries: { x, y, amount, crit?, age }. Only the newest DAMAGE_NUMBER_CAP are drawn. */
  function drawDamageNumbers(ctx, list) {
    if (!list || !list.length) return;
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (var i = Math.max(0, list.length - DAMAGE_NUMBER_CAP); i < list.length; i++) {
      var n = list[i];
      if (!n || !finite(n.x) || !finite(n.y) || !finite(n.amount)) continue;
      var age = finite(n.age) ? n.age : 0;
      var p = clamp01(age / DAMAGE_NUMBER_LIFE);
      if (p >= 1) continue;
      ctx.globalAlpha = 1 - p * p;
      ctx.font = n.crit ? CRIT_FONT : DAMAGE_FONT;
      ctx.fillStyle = colourOf(n.crit ? CRIT_COLOUR_TOKEN : NORMAL_COLOUR_TOKEN);
      ctx.fillText(String(Math.round(n.amount)), n.x, n.y - DAMAGE_NUMBER_RISE * easeOut(p));
    }
    ctx.restore();
  }

  /** Live-particle budget for a quality setting ('low' | 'med' | 'high'); unknown values use DEFAULT_QUALITY. */
  function particleBudget(quality) {
    var scale = QUALITY_SCALE[quality];
    return Math.floor(PARTICLE_CAP * (scale === undefined ? QUALITY_SCALE[DEFAULT_QUALITY] : scale));
  }

  /**
   * Adds up to `count` particles to `particles` at (x, y), never growing it past the quality budget. Randomness comes
   * only from `rng` (pass world.fx.rng) so the same seed gives the same burst. Returns the number actually added.
   */
  function spawnBurst(particles, rng, x, y, count, colour, quality) {
    if (!particles || !rng || !finite(x) || !finite(y) || !finite(count) || count <= 0) return 0;
    var room = particleBudget(quality) - particles.length;
    var n = Math.min(Math.floor(count), room);
    for (var i = 0; i < n; i++) {
      var a = rng.range(0, TWO_PI), s = rng.range(PARTICLE_SPEED_MIN, PARTICLE_SPEED_MAX);
      particles.push({
        x: x, y: y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, age: 0,
        ttl: rng.range(PARTICLE_LIFE_MIN, PARTICLE_LIFE_MAX), r: rng.range(PARTICLE_RADIUS_MIN, PARTICLE_RADIUS_MAX),
        colour: colour
      });
    }
    return n;
  }

  /** Advances particles by dt seconds and removes the dead ones in place. Returns the same array. */
  function stepParticles(particles, dt) {
    if (!particles || !usable(dt)) return particles;
    var keep = 0, damp = Math.max(0, 1 - PARTICLE_DRAG * dt);
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      p.age += dt;
      if (p.age >= p.ttl) continue;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.vx *= damp;
      p.vy *= damp;
      particles[keep++] = p;
    }
    particles.length = keep;
    return particles;
  }

  /** Draws live particles as small discs that shrink and fade over their ttl. */
  function drawParticles(ctx, particles) {
    if (!particles || !particles.length) return;
    ctx.save();
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      if (!finite(p.x) || !finite(p.y) || !usable(p.ttl)) continue;
      var k = clamp01(p.age / p.ttl);
      if (k >= 1) continue;
      ctx.globalAlpha = 1 - k;
      RPG.render.shapes.outlinedCircle(ctx, p.x, p.y, p.r * (1 - 0.5 * k), colourOf(p.colour), null);
    }
    ctx.restore();
  }

  /**
   * Hurt flash strength for `age` seconds since the player was hit: HURT_FLASH_PEAK at age 0, decaying smoothly to 0 at
   * HURT_FLASH_SECONDS. Always within [0, HURT_FLASH_PEAK]; a non-finite or negative age gives 0.
   */
  function hurtFlashAlpha(age) {
    if (!finite(age) || age < 0) return 0;
    var inv = 1 - clamp01(age / HURT_FLASH_SECONDS);
    return HURT_FLASH_PEAK * inv * inv;
  }

  /** Screen-edge vignette in `colour` for a hit `age` seconds old. Draws nothing once the flash has decayed. */
  function drawHurtFlash(ctx, viewW, viewH, age, colour) {
    if (!usable(viewW) || !usable(viewH) || !colour) return;
    var alpha = hurtFlashAlpha(age);
    if (alpha <= 0) return;
    var cx = viewW / 2, cy = viewH / 2;
    var inner = Math.min(viewW, viewH) / 2, outer = Math.sqrt(cx * cx + cy * cy);
    ctx.save();
    var g = ctx.createRadialGradient(cx, cy, inner, cx, cy, outer);
    g.addColorStop(0, 'transparent');
    g.addColorStop(1, colour);
    ctx.globalAlpha = alpha;
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, viewW, viewH);
    ctx.restore();
  }

  RPG.render.effects = Object.freeze({
    DAMAGE_NUMBER_CAP: DAMAGE_NUMBER_CAP,
    PARTICLE_CAP: PARTICLE_CAP,
    HURT_FLASH_PEAK: HURT_FLASH_PEAK,
    telegraphFill: telegraphFill,
    drawTelegraph: drawTelegraph,
    drawAoeRing: drawAoeRing,
    drawNova: drawNova,
    drawWhirl: drawWhirl,
    drawDamageNumbers: drawDamageNumbers,
    particleBudget: particleBudget,
    spawnBurst: spawnBurst,
    stepParticles: stepParticles,
    drawParticles: drawParticles,
    hurtFlashAlpha: hurtFlashAlpha,
    drawHurtFlash: drawHurtFlash
  });
})(globalThis.RPG = globalThis.RPG || {});

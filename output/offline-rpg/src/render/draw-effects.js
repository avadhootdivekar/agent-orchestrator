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

  // ---- World wiring: reads world.effects / world.telegraphs / world.fx, steps cosmetic particle state ---------------

  var HIT_BURST = 3;                 // particles per landed hit
  var DEATH_ANIM_SECONDS = 0.4;      // how long a dead monster's ghost plays its shrink/fade
  var DEATH_GHOST_CAP = 40;
  var DEATH_BURST = 12;              // particles when a monster dies
  var HIT_BURST_PLAYER_TOKEN = 'player';
  var HIT_BURST_MONSTER_TOKEN = 'enemy';
  var DEATH_BURST_TOKEN = 'hazard';
  var TELEGRAPH_TOKEN = 'enemy';
  var ZONE_TOKEN = 'hazard';
  var ZONE_GLOW_ALPHA = 0.22;
  var WHIRL_SPIN = 10;               // radians per second of world time
  var AURA_SCALE = 1.6;              // shield/heal rings sit outside the player body
  var AURA_ALPHA = 0.6;
  var AURA_WIDTH = 2;
  var DASH_WIDTH = 3;

  var scratchTg = { shape: null, x: 0, y: 0, dirX: 0, dirY: 0, radius: 0, range: 0, t: 0, duration: 0 };

  function liveFraction(e) {
    return usable(e.life) ? clamp01((e.life - e.ttl) / e.life) : 0;
  }

  function drawWarning(ctx, e, colour) {
    var tg = scratchTg;
    tg.shape = e.shape; tg.x = e.x; tg.y = e.y; tg.dirX = e.dirX; tg.dirY = e.dirY; tg.range = e.range;
    tg.radius = usable(e.radius) ? e.radius : e.r;
    if (e.kind === 'warning') {   // boss warning: counts `remaining` down from `duration`
      tg.duration = e.duration;
      tg.t = usable(e.duration) ? e.duration - e.remaining : e.remaining;
    } else {                      // pooled monster telegraph: `t` counts up to `duration`
      tg.duration = e.duration; tg.t = e.t;
    }
    drawTelegraph(ctx, tg, colour);
  }

  function drawBelow(ctx, world, pal) {
    var list = world.telegraphs, i, e;
    if (list) {
      for (i = 0; i < list.length; i++) {
        e = list[i];
        if (e.kind === 'warning' && !e.done) drawWarning(ctx, e, pal[TELEGRAPH_TOKEN]);
        else if (e.kind === 'zone' && !e.done && usable(e.radius)) {
          RPG.render.shapes.glow(ctx, e.x, e.y, e.radius, pal[ZONE_TOKEN], ZONE_GLOW_ALPHA);
          RPG.render.shapes.ring(ctx, e.x, e.y, e.radius, RING_WIDTH, pal[ZONE_TOKEN], 1);
        }
      }
    }
    list = world.effects;
    if (!list) return;
    for (i = 0; i < list.length; i++) {
      e = list[i];
      if (e.type === 'telegraph' && !e.dead) drawWarning(ctx, e, pal[TELEGRAPH_TOKEN]);
    }
  }

  function drawSkillEffect(ctx, world, e, pal) {
    var colour = e.color || pal.ui, p = liveFraction(e), ttl = e.life;
    switch (e.kind) {
      case 'aoe': drawNova(ctx, e.x, e.y, e.radius, e.life - e.ttl, ttl, colour); break;
      case 'ring': drawAoeRing(ctx, e.x, e.y, e.radius, e.life - e.ttl, ttl, colour); break;
      case 'whirl': drawWhirl(ctx, e.x, e.y, e.radius, world.time * WHIRL_SPIN, colour); break;
      case 'aura':
      case 'heal':
        RPG.render.shapes.ring(ctx, e.x, e.y, (e.radius || 1) * AURA_SCALE, AURA_WIDTH, colour, AURA_ALPHA * (1 - p * p));
        break;
      case 'arc':   // blink strike: a streak from start to landing that fades out
        if (!finite(e.x2) || !finite(e.y2)) break;
        ctx.save();
        ctx.globalAlpha = 1 - p;
        ctx.strokeStyle = colour;
        ctx.lineWidth = DASH_WIDTH;
        ctx.beginPath();
        ctx.moveTo(e.x, e.y);
        ctx.lineTo(e.x2, e.y2);
        ctx.stroke();
        ctx.restore();
        break;
    }
  }

  function drawAbove(ctx, world, pal) {
    var list = world.effects, i, e;
    if (list) {
      for (i = 0; i < list.length; i++) {
        e = list[i];
        if (e.skillId && usable(e.life)) drawSkillEffect(ctx, world, e, pal);
        else if (!e.dead && e.type === 'blast' && usable(e.r)) drawNova(ctx, e.x, e.y, e.r, e.t, e.duration, pal[ZONE_TOKEN]);
        else if (!e.dead && (e.type === 'aoe' || e.type === 'zone') && usable(e.r)) {
          drawAoeRing(ctx, e.x, e.y, e.r, e.t, e.duration, pal[ZONE_TOKEN]);
        }
      }
    }
    if (world.fx) drawParticles(ctx, world.fx.particles);
  }

  /** Renderer entry: draws the world's effects for one layer, 'below' the creatures (ground warnings) or 'above' them. */
  function drawEffects(ctx, world, alpha, pal, layer) {
    if (!world || !pal) return;
    if (layer === 'below') drawBelow(ctx, world, pal);
    else if (layer === 'above') drawAbove(ctx, world, pal);
  }

  /** Cosmetic copy of a monster that just died; draw-entities.drawDying plays it out after the sim has removed the monster. */
  function addDeathGhost(fx, m) {
    if (!fx.deaths) fx.deaths = [];
    if (fx.deaths.length >= DEATH_GHOST_CAP) fx.deaths.shift();
    fx.deaths.push({ defId: m.defId, x: m.x, y: m.y, r: m.r, facing: m.facing || 0, elite: !!m.elite, age: 0, life: DEATH_ANIM_SECONDS });
  }

  function stepDeaths(fx, dt) {
    var list = fx.deaths;
    if (!list) return;
    var step = finite(dt) && dt > 0 ? dt : 0;
    for (var i = list.length - 1; i >= 0; i--) {
      list[i].age += step;
      if (list[i].age >= list[i].life) list.splice(i, 1);
    }
  }

  /**
   * Cosmetic per-tick step, called from the sim's cleanup stage: spawns hit and death bursts from this tick's hits and
   * newly dead monsters, then advances every particle. Uses only world.fx.rng, so gameplay streams and saves are unaffected.
   */
  function stepWorldFx(world, ctx) {
    var fx = world.fx;
    if (!fx) return;
    if (!fx.particles) fx.particles = [];
    var hits = ctx && ctx.hits, i, h, quality = fx.quality;
    if (hits) {
      for (i = 0; i < hits.length; i++) {
        h = hits[i];
        if (h.dot || !(h.dealt > 0)) continue;
        spawnBurst(fx.particles, fx.rng, h.x, h.y, HIT_BURST, colourOf(h.team === 1 ? HIT_BURST_MONSTER_TOKEN : HIT_BURST_PLAYER_TOKEN), quality);
      }
    }
    var mons = world.monsters;
    for (i = 0; mons && i < mons.length; i++) {
      if (mons[i].dead && !mons[i].fxDied) {
        mons[i].fxDied = true;
        spawnBurst(fx.particles, fx.rng, mons[i].x, mons[i].y, DEATH_BURST, colourOf(DEATH_BURST_TOKEN), quality);
        addDeathGhost(fx, mons[i]);
      }
    }
    stepDeaths(fx, ctx && ctx.dt);
    stepParticles(fx.particles, ctx && ctx.dt);
  }

  RPG.render.effects = Object.freeze({
    drawEffects: drawEffects,
    stepWorldFx: stepWorldFx,
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

// Headless benchmark: steps the simulation and runs the renderer against a counting canvas stub with several hundred
// monsters plus a stream of projectiles. Prints ms/frame (mean, p95, max) for sim, draw and sim+draw.
//
// Usage: node scripts/bench.mjs [--entities N] [--frames N] [--warmup N] [--seed N] [--json]
// Zero dependencies; node >= 18. Numbers vary by machine: they are a regression guard, not a device guarantee.

import { createContext, runInContext } from 'node:vm';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DT = 1 / 60;
const BUDGET_MS = 16;

function parseArgs(argv) {
  const o = { entities: 320, frames: 600, warmup: 120, seed: 7, json: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--json') o.json = true;
    else if (a.startsWith('--') && a.slice(2) in o) o[a.slice(2)] = Number(argv[++i]);
    else throw new Error('bench: unknown argument ' + a);
  }
  return o;
}

async function loadEngine() {
  const { JS_ORDER } = await import(pathToFileURL(join(ROOT, 'scripts', 'build.mjs')).href);
  const ctx = createContext({});
  ctx.globalThis = ctx;
  for (const rel of JS_ORDER) {
    if (rel === 'src/main.js' || !existsSync(join(ROOT, rel))) continue; // main.js needs a canvas
    runInContext(readFileSync(join(ROOT, rel), 'utf8'), ctx, { filename: rel });
  }
  return ctx.RPG;
}

/** Canvas 2D stand-in that counts calls and does nothing else, so draw time is the renderer's own cost. */
function countingCanvas() {
  const counts = { calls: 0 };
  const noop = function () { counts.calls++; };
  const grad = { addColorStop: noop };
  const ctx = {
    fillStyle: '', strokeStyle: '', globalAlpha: 1, lineWidth: 1, font: '', textAlign: '', textBaseline: '',
    createRadialGradient() { counts.calls++; return grad; },
    createLinearGradient() { counts.calls++; return grad; },
    measureText() { counts.calls++; return { width: 10 }; }
  };
  for (const n of ['fillRect', 'strokeRect', 'fillText', 'strokeText', 'arc', 'ellipse', 'moveTo', 'lineTo', 'beginPath',
    'closePath', 'fill', 'stroke', 'save', 'restore', 'setTransform', 'translate', 'rotate', 'scale', 'clip', 'rect',
    'quadraticCurveTo', 'bezierCurveTo', 'drawImage', 'setLineDash']) ctx[n] = noop;
  return { canvas: { width: 0, height: 0, getContext: () => ctx }, counts };
}

function percentile(sorted, p) {
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
}
function stats(samples) {
  const s = samples.slice().sort((a, b) => a - b);
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  return { mean, p50: percentile(s, 0.5), p95: percentile(s, 0.95), max: s[s.length - 1] };
}

// Spreads `count` monsters over walkable tiles near the player so most are inside the camera view.
function populate(RPG, world, count) {
  const map = world.area.map, ts = map.tileSize, p = world.player;
  const ids = ['husk-grunt', 'skitter', 'bone-archer', 'stone-brute', 'cinder-bomb'];
  const R = 14;
  const tx0 = Math.floor(p.x / ts), ty0 = Math.floor(p.y / ts);
  const cells = [];
  for (let r = 3; r <= R; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        if (!RPG.world.tilemap.isSolid(map, tx0 + dx, ty0 + dy)) cells.push([tx0 + dx, ty0 + dy]);
      }
    }
  }
  let n = 0;
  for (let i = 0; n < count && cells.length; i++, n++) {
    const c = cells[i % cells.length];
    const jitter = (i % 7) * 3;
    RPG.world.spawnMonster(world, ids[i % ids.length], (c[0] + 0.5) * ts + jitter, (c[1] + 0.5) * ts, { level: 3 });
  }
  return n;
}

function run(RPG, o) {
  const world = RPG.sim.createWorld({ seed: o.seed, areaId: 'forest' });
  world.monsters.length = 0;
  // Pin the player alive and the monster count steady so every frame carries the same load.
  const spawned = populate(RPG, world, o.entities);
  const { canvas, counts } = countingCanvas();
  const renderer = RPG.render.create(canvas);
  renderer.resize(960, 540, 1);
  renderer.snapTo(world);

  const sim = [], draw = [], total = [];
  const peak = { monsters: 0, projectiles: 0, effects: 0, enemyProjectiles: 0, damageNumbers: 0 };
  let ang = 0;
  const intent = { move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: true, skills: [], dash: false, toggles: {} };
  const frames = o.warmup + o.frames;
  for (let f = 0; f < frames; f++) {
    const p = world.player;
    p.hp = p.maxHp || p.hp;
    if (world.monsters.length < o.entities) populate(RPG, world, o.entities - world.monsters.length);
    ang += 0.05;
    intent.move.x = Math.cos(ang * 0.3); intent.move.y = Math.sin(ang * 0.3) * 0.5;
    intent.aim.x = p.x + Math.cos(ang) * 100; intent.aim.y = p.y + Math.sin(ang) * 100;
    // Fire a few extra enemy-side and player projectiles so pooling and the projectile loops are exercised.
    if (f % 4 === 0) {
      for (let k = 0; k < 6; k++) {
        const shot = RPG.world.spawnProjectile(world, { x: p.x + k * 10, y: p.y, angle: ang + k, speed: 200, team: k % 2, lifetime: 1.5, radius: 4, damage: null });
        shot.hitIds = [];
      }
    }
    counts.calls = 0;
    const t0 = performance.now();
    RPG.sim.step(world, intent, DT);
    const t1 = performance.now();
    renderer.draw(world, 0.5, DT, null);
    const t2 = performance.now();
    if (f >= o.warmup) {
      sim.push(t1 - t0); draw.push(t2 - t1); total.push(t2 - t0);
      peak.monsters = Math.max(peak.monsters, world.monsters.length);
      peak.projectiles = Math.max(peak.projectiles, world.projectiles.length);
      peak.enemyProjectiles = Math.max(peak.enemyProjectiles, (world.enemyProjectiles || []).length);
      peak.effects = Math.max(peak.effects, world.effects.length);
      peak.damageNumbers = Math.max(peak.damageNumbers, ((world.fx && world.fx.damageNumbers) || []).length);
    }
  }
  return { spawned, frames: o.frames, drawCallsPerFrame: counts.calls, peak, sim: stats(sim), draw: stats(draw), total: stats(total) };
}

const o = parseArgs(process.argv.slice(2));
const RPG = await loadEngine();
const r = run(RPG, o);
if (o.json) {
  console.log(JSON.stringify(r, null, 2));
} else {
  const fmt = (s) => `mean ${s.mean.toFixed(3)}  p50 ${s.p50.toFixed(3)}  p95 ${s.p95.toFixed(3)}  max ${s.max.toFixed(3)}`;
  console.log(`bench: ${r.frames} frames, ${o.entities} monsters requested (${r.spawned} initial), peak monsters ${r.peak.monsters}, ` +
    `projectiles ${r.peak.projectiles}+${r.peak.enemyProjectiles}, effects ${r.peak.effects}, damage numbers ${r.peak.damageNumbers}`);
  console.log(`sim   ms/frame: ${fmt(r.sim)}`);
  console.log(`draw  ms/frame: ${fmt(r.draw)}   (${r.drawCallsPerFrame} canvas calls in last frame)`);
  console.log(`total ms/frame: ${fmt(r.total)}`);
  console.log(`budget ${BUDGET_MS} ms p95: ${r.total.p95 < BUDGET_MS ? 'PASS' : 'OVER'}`);
}

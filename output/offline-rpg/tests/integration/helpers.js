'use strict';
// Shared harness for the headless integration tests (docs/INTEGRATION-PLAN.md §8, W4-14).
// Each game gets its own vm context loaded with the real engine files in build order (scripts/build.mjs JS_ORDER),
// so two games never share listeners, pools or RNG state. No window, no document, no real clock.

const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const ROOT = path.resolve(__dirname, '..', '..');
const BUILD = path.join(ROOT, 'scripts', 'build.mjs');
const DT = 1 / 60;
const BOSS_ID = 'warden-of-ash';

// build.mjs is an ES module; its JS_ORDER is the single source of truth, so tests load exactly what the page loads.
async function loadEngine() {
  const { JS_ORDER } = await import(pathToFileURL(BUILD).href);
  const ctx = vm.createContext({});
  ctx.globalThis = ctx;
  for (const rel of JS_ORDER) {
    if (rel === 'src/main.js' || !fs.existsSync(path.join(ROOT, rel))) continue; // main.js needs a canvas
    vm.runInContext(fs.readFileSync(path.join(ROOT, rel), 'utf8'), ctx, { filename: rel });
  }
  return ctx.RPG;
}

// In-memory key-value backend standing in for localStorage. Pass the same backend to two games to share saves.
function memoryBackend() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); }
  };
}

// Headless renderer/input stubs: game.tick only needs camera/viewW/viewH to sample an intent; draw/snapTo are no-ops.
function stubRenderer() {
  return { camera: {}, viewW: 800, viewH: 600, draw() {}, snapTo() {}, resize() {} };
}

// STOPGAP, not engine behaviour (followups recorded in the w04-20 report):
//  - INTEGRATION-PLAN §2.3 says createWorld gives a fresh player a sword in slot 0; sim/sim.js does not do it.
//  - loot.generateItem sets baseId but not weaponId, and world/weapons.js defFromSlot resolves only weaponId/defId,
//    so a generated weapon can never attack. The harness sets weaponId on the granted item to work around that.
// Without a weapon the player never attacks, so the sword comes from a private RNG (the world's streams are untouched).
// Remove both lines once the engine grants and resolves weapons itself.
function grantStartingWeapon(RPG, world) {
  const p = world.player;
  if (p.weaponSlots[0]) return;
  const sword = RPG.world.loot.generateItem(RPG.core.Rng.create(1), { baseId: 'sword', rarity: 'common', ilvl: 1 });
  sword.weaponId = 'sword';
  p.weaponSlots[0] = sword;
}

/**
 * Builds a headless Game in a fresh engine. `drive(n, intentFn)` ticks n fixed steps, feeding intentFn(game) as the
 * intent each tick. A death is answered with game.respawn() (the real death-screen action) and counted.
 */
async function makeGame(opts) {
  opts = opts || {};
  const RPG = await loadEngine();
  const backend = opts.backend || memoryBackend();
  const storage = RPG.persistence.storage.createStorage({ backend });
  const ctl = { source: null, deaths: 0 };
  const input = { buildFrame: () => ctl.source(game) };
  const game = RPG.core.game.create({
    input, renderer: stubRenderer(), storage, clock: { now: () => 0 }, screens: null
  });
  const api = {
    RPG, game, backend, storage,
    events: RPG.core.events,
    NAMES: RPG.core.events.NAMES,
    newGame(seed, difficulty) {
      const r = game.newGame({ seed: seed, difficulty: difficulty || 'normal' });
      if (!r.ok) throw new Error('newGame failed: ' + JSON.stringify(r));
      grantStartingWeapon(RPG, game.world);
      return game.world;
    },
    drive(n, intentFn) {
      ctl.source = intentFn;
      for (let i = 0; i < n; i++) {
        if (game.state === 'dead') { game.respawn(); ctl.deaths++; }
        game.tick(DT);
      }
      return { deaths: ctl.deaths, stepErrors: game.stepErrors };
    }
  };
  return api;
}

/** Bot: attacks the nearest living monster with a seeded wander when none is in sight. Reads the world, so it is
 *  only deterministic when the world is. Returns a function(game) -> intent. */
function makeBot(seed) {
  let dir = 0, timer = 0, step = 0;
  return function botIntent(game) {
    const w = game.world, p = w.player;
    let best = null, bestD2 = Infinity;
    for (const m of w.monsters) {
      if (m.dead) continue;
      const dx = m.x - p.x, dy = m.y - p.y, d2 = dx * dx + dy * dy;
      if (d2 < bestD2) { bestD2 = d2; best = m; }
    }
    const move = { x: 0, y: 0 };
    if (best) {
      const d = Math.sqrt(bestD2);
      if (d > 40) { move.x = (best.x - p.x) / d; move.y = (best.y - p.y) / d; }
      return { move, aim: { x: best.x, y: best.y }, attack: d < 260, skills: [], dash: false, toggles: {} };
    }
    if (timer <= 0) { dir = wander(seed, step++); timer = 120; }
    timer--;
    move.x = Math.cos(dir); move.y = Math.sin(dir);
    return { move, aim: { x: p.x + move.x, y: p.y + move.y }, attack: false, skills: [], dash: false, toggles: {} };
  };
}

// Deterministic, world-independent walk for continuation tests: same call count -> same intents.
function makeWalker(seed) {
  let dir = 0, timer = 0, step = 0;
  return function walkIntent(game) {
    const p = game.world.player;
    if (timer <= 0) { dir = wander(seed, step++); timer = 90; }
    timer--;
    const mx = Math.cos(dir), my = Math.sin(dir);
    return { move: { x: mx, y: my }, aim: { x: p.x + mx, y: p.y + my }, attack: false, skills: [], dash: false, toggles: {} };
  };
}

// Turns (seed, step) into an angle without Math.random: an integer hash, stable across runs and realms.
function wander(seed, step) {
  let h = (Math.imul(seed, 2654435761) + Math.imul(step, 40503) + 1) >>> 0;
  h ^= h >>> 15; h = Math.imul(h, 2246822519) >>> 0; h ^= h >>> 13;
  return (h / 4294967296) * Math.PI * 2;
}

// Full gameplay state as a string (strings compare across vm realms; objects do not). Covers clock, both RNG streams,
// the player, live monsters, drops and progress: any divergence in the simulation shows up here.
function fingerprint(world) {
  return JSON.stringify({
    tick: world.tick,
    time: world.time,
    rng: world.rng.getState(),
    lootRng: world.lootRng ? world.lootRng.getState() : null,
    player: world.player,
    monsters: world.monsters.filter((m) => !m.dead).map((m) => [m.id, m.defId, m.x, m.y, m.hp, m.dead]),
    drops: world.drops.map((d) => [d.id, d.x, d.y, d.gold || 0, d.item ? d.item.uid : null]),
    progress: world.progress
  });
}

module.exports = { loadEngine, makeGame, makeBot, makeWalker, fingerprint, memoryBackend, DT, BOSS_ID };

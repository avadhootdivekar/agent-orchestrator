#!/usr/bin/env node
/**
 * Seeded headless balance simulation.
 *
 * Plays a scripted player (basic-attack melee bot, equipment kit rolled for its level) at player levels 1-15 through
 * each combat area on every difficulty plus custom, and reports time-to-kill, damage taken, XP/level pace and the loot
 * rarity distribution. It loads the real engine files in build order inside a vm context (no DOM, no clock), so a run
 * is fully determined by its seeds.
 *
 *   node scripts/balance.mjs                       full report (levels 1-15, all areas, all difficulties)
 *   node scripts/balance.mjs --quick               levels 1,5,10,15 and one seed
 *   node scripts/balance.mjs --seeds 5 --seconds 90
 *   node scripts/balance.mjs --json                machine-readable output
 *
 * Exports `loadEngine`, `simulateCell`, `sweep` and `threat` so tests/scripts/balance.test.mjs can drive it.
 */
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DT = 1 / 60;
export const DIFFICULTIES = ['easy', 'normal', 'hard', 'nightmare', 'custom'];
export const AREAS = ['forest', 'caves', 'arena'];
export const RARITIES = ['common', 'uncommon', 'rare', 'epic', 'legendary'];
const GEAR_SLOTS = ['offhand', 'head', 'chest', 'legs', 'boots', 'ring', 'amulet'];

/** Loads the real engine (JS_ORDER from build.mjs) into a fresh vm context and returns its RPG namespace. */
export async function loadEngine() {
  const { JS_ORDER } = await import(pathToFileURL(path.join(ROOT, 'scripts', 'build.mjs')).href);
  const ctx = vm.createContext({});
  ctx.globalThis = ctx;
  for (const rel of JS_ORDER) {
    if (rel === 'src/main.js' || !fs.existsSync(path.join(ROOT, rel))) continue; // main.js needs a canvas
    vm.runInContext(fs.readFileSync(path.join(ROOT, rel), 'utf8'), ctx, { filename: rel });
  }
  return ctx.RPG;
}

function memoryBackend() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
  };
}

function makeGame(RPG) {
  const ctl = { source: null };
  const input = { buildFrame: () => ctl.source(game) };
  const renderer = { camera: {}, viewW: 800, viewH: 600, draw() {}, snapTo() {}, resize() {} };
  const storage = RPG.persistence.storage.createStorage({ backend: memoryBackend() });
  const game = RPG.core.game.create({ input, renderer, storage, clock: { now: () => 0 }, screens: null });
  return { game, ctl };
}

// Scripted player: walks to and attacks the nearest living monster, wandering on a seeded hash when none is left.
function wanderAngle(seed, step) {
  let h = (Math.imul(seed, 2654435761) + Math.imul(step, 40503) + 1) >>> 0;
  h ^= h >>> 15; h = Math.imul(h, 2246822519) >>> 0; h ^= h >>> 13;
  return (h / 4294967296) * Math.PI * 2;
}

function makeBot(seed) {
  let dir = 0, timer = 0, step = 0;
  return function intent(game) {
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
    if (timer <= 0) { dir = wanderAngle(seed, step++); timer = 120; }
    timer--;
    move.x = Math.cos(dir); move.y = Math.sin(dir);
    return { move, aim: { x: p.x + move.x, y: p.y + move.y }, attack: false, skills: [], dash: false, toggles: {} };
  };
}

// Raises the player to `level` through the real XP path, then equips one uncommon item per gear slot at ilvl = level
// and a common sword. This is the "typical find" kit: a player who has been playing through the earlier areas.
function prepPlayer(RPG, world, level) {
  const p = world.player;
  while (p.level < level) RPG.sim.rewards.grantXp(p, RPG.world.progression.xpToNext(p.level));
  p.hp = p.stats.maxHp;
  const rng = RPG.core.Rng.create(level * 7919 + 13);
  // Gear goes through the real inventory path (equipFromSlot), which also refreshes the derived stats.
  const equip = (item) => { p.inventory[0] = item; RPG.ui.inventory.equipFromSlot(p, 0); };
  const weapon = RPG.world.loot.generateItem(rng, { baseId: 'sword', rarity: 'common', ilvl: level });
  p.weaponSlots[0] = weapon;
  for (const slot of GEAR_SLOTS) {
    const it = RPG.world.loot.generateItem(rng, { slot, rarity: 'uncommon', ilvl: level });
    if (it) equip(it);
  }
  p.hp = p.stats.maxHp;
}

/**
 * One scripted run: player at `level` enters `areaId` on `difficulty` and fights for `seconds` of world time,
 * respawning after deaths. Returns plain numbers (see the `return`).
 */
export async function simulateCell(opts) {
  const { difficulty, areaId, level, seed = 1, seconds = 60 } = opts;
  const RPG = opts.RPG || (await loadEngine());
  const { game, ctl } = makeGame(RPG);
  const r = game.newGame({ seed: `bal-${areaId}-${difficulty}-${level}-${seed}`, difficulty });
  if (!r.ok) throw new Error('newGame failed: ' + JSON.stringify(r));
  const world = game.world;
  prepPlayer(RPG, world, level);
  RPG.world.areas.enter(world, areaId);
  const N = RPG.core.events.NAMES;
  const rarity = Object.fromEntries(RARITIES.map((x) => [x, 0]));
  let kills = 0, damageTaken = 0, deaths = 0, xpGained = 0, levelUps = 0, enemyHp = 0, enemyDmg = 0;
  game.on(N.KILL, (e) => { kills++; xpGained += e.xp; });
  game.on(N.LEVEL_UP, () => { levelUps++; });
  game.on(N.ITEM_DROPPED, (e) => { rarity[e.rarity]++; });
  // Fight time per monster: first hit on it until it is seen dead. Walking to a far monster is not part of "time to kill".
  const firstHit = new Map(), fightTimes = [];
  game.on(N.HIT, (e) => { if (e.targetId !== world.player.id && !firstHit.has(e.targetId)) firstHit.set(e.targetId, world.tick); });
  for (const m of world.monsters) { if (!m.dead) { enemyHp += m.maxHp || m.hp; enemyDmg += m.damage || 0; } }
  const startLevel = world.player.level, startXp = world.player.xp;
  const maxHp = world.player.stats.maxHp;
  ctl.source = makeBot(seed);
  const ticks = Math.round(seconds / DT);
  let killTicks = 0;
  for (let i = 0; i < ticks; i++) {
    if (game.state === 'dead') { game.respawn(); deaths++; }
    const hpBefore = world.player.hp;
    game.tick(DT);
    // Damage taken is the HP the player lost this tick, so telegraphed area attacks and DoTs count as well as direct hits.
    if (game.state !== 'dead' && world.player.hp < hpBefore) damageTaken += hpBefore - world.player.hp;
    else if (game.state === 'dead') damageTaken += hpBefore;
    // A monster that was hit and is no longer a live entry (dead, or swept back into the pool) counts as killed.
    if (firstHit.size) {
      const live = new Set();
      for (const m of world.monsters) if (!m.dead) live.add(m.id);
      for (const [id, tick] of firstHit) {
        if (!live.has(id)) { fightTimes.push((world.tick - tick) * DT); firstHit.delete(id); }
      }
    }
    if (world.monsters.every((m) => m.dead)) { killTicks = i + 1; break; }
  }
  const elapsed = (killTicks || ticks) * DT;
  const drops = Object.values(rarity).reduce((a, b) => a + b, 0);
  const xpNeed = RPG.world.progression.xpToNext(startLevel);
  return {
    kills, cleared: killTicks > 0, elapsed, deaths, stepErrors: game.stepErrors,
    ttk: fightTimes.length ? mean(fightTimes) : Infinity, damageTaken, 
    playerMaxHp: maxHp, damageTakenPct: damageTaken / maxHp, xpGained, xpPerMin: xpGained / (elapsed / 60),
    levelFraction: xpGained / xpNeed, levelUps, startLevel, startXp, rarity, drops, enemyHp, enemyDmg,
  };
}

/**
 * Analytic enemy strength: mean over an area's spawn table (weighted) and level range of hp * damage * sqrt-speed
 * after difficulty and player-level scaling. No simulation, so it is cheap enough for unit tests.
 */
export function threat(RPG, areaId, playerLevel, difficulty) {
  const area = RPG.data.areas[areaId];
  const diff = RPG.world.difficulty.resolve(difficulty);
  let total = 0, weight = 0;
  const entries = area.spawnTable.length ? area.spawnTable : [{ monsterId: area.boss.bossId, weight: 1 }];
  for (const e of entries) {
    const def = RPG.data.monsters[e.monsterId];
    for (let al = area.level[0]; al <= area.level[1]; al++) {
      const f = RPG.world.difficulty.scaleMonster(def, playerLevel, diff, al);
      total += e.weight * def.baseStats.hp * f.hp * def.baseStats.damage * f.damage;
      weight += e.weight;
    }
  }
  return total / weight;
}

/** Analytic XP pace: spawn-table-weighted kills of `areaId` needed to go from `level` to the next one. */
export function killsToLevel(RPG, areaId, level, difficulty) {
  const area = RPG.data.areas[areaId];
  const diff = RPG.world.difficulty.resolve(difficulty);
  let total = 0, weight = 0;
  for (const e of area.spawnTable) {
    const def = RPG.data.monsters[e.monsterId];
    for (let al = area.level[0]; al <= area.level[1]; al++) {
      total += e.weight * Math.floor(def.xp * RPG.world.difficulty.scaleMonster(def, level, diff, al).xp);
      weight += e.weight;
    }
  }
  return RPG.world.progression.xpToNext(level) / (total / weight);
}

/**
 * Normal-play route to the gates: for each seed, populates each area in progression order (the monsters are the real
 * spawned ones with their scaled XP), assumes every monster is killed once (no respawn), and reports the cumulative XP,
 * the level it reaches and how much of the area's XP is needed to clear the next gate. -> [{ seed, steps: [...] }]
 */
export function gateRoute(RPG, opts = {}) {
  const { difficulty = 'normal', seeds = 5 } = opts;
  const P = RPG.world.progression;
  const route = [['forest', 'caves'], ['caves', 'arena']];
  const out = [];
  for (let seed = 1; seed <= seeds; seed++) {
    const { game } = makeGame(RPG);
    const r = game.newGame({ seed: `route-${seed}`, difficulty });
    if (!r.ok) throw new Error('newGame failed: ' + JSON.stringify(r));
    let total = 0;
    const steps = [];
    for (const [areaId, nextId] of route) {
      RPG.world.areas.enter(game.world, areaId);
      const supply = game.world.monsters.reduce((a, m) => a + (m.defId === 'warden-of-ash' ? 0 : m.xp), 0);
      const need = RPG.data.areas[areaId].exits.find((e) => e.to === nextId).requires.level;
      const needXp = P.totalXpForLevel(need);
      total += supply;
      let level = 1;
      while (P.totalXpForLevel(level + 1) <= total) level++;
      steps.push({ area: areaId, gate: nextId, gateLevel: need, gateXp: needXp, supply, cumulativeXp: total, level, clearedShare: Math.min(1, (needXp - (total - supply)) / supply) });
    }
    out.push({ seed, steps });
  }
  return out;
}

/** Share of drops that are `rare` or better for monsters of `areaId` on `difficulty` (analytic, from the rarity weights). */
export function rareShare(RPG, areaId, difficulty) {
  const area = RPG.data.areas[areaId];
  const diff = RPG.world.difficulty.resolve(difficulty);
  const w = RPG.world.loot.rarityWeights({ lootQuality: diff.lootQuality, areaLevel: area.level[1] });
  const total = w.reduce((a, e) => a + e.weight, 0);
  return w.filter((e) => RPG.data.rarities[e.id].rank >= RPG.data.rarities.rare.rank).reduce((a, e) => a + e.weight, 0) / total;
}

function mean(xs) { return xs.reduce((a, b) => a + b, 0) / xs.length; }

/** Runs every (difficulty, area, level) cell over `seeds` seeds and averages them. -> { rows, RPG } */
export async function sweep(opts = {}) {
  const levels = opts.levels || Array.from({ length: 15 }, (_, i) => i + 1);
  const difficulties = opts.difficulties || DIFFICULTIES;
  const areas = opts.areas || AREAS;
  const seeds = opts.seeds || 3;
  const seconds = opts.seconds || 60;
  const RPG = await loadEngine();
  const rows = [];
  for (const difficulty of difficulties) {
    for (const areaId of areas) {
      for (const level of levels) {
        const runs = [];
        for (let seed = 1; seed <= seeds; seed++) runs.push(await simulateCell({ RPG, difficulty, areaId, level, seed, seconds }));
        const fin = runs.filter((r) => Number.isFinite(r.ttk));
        const rar = Object.fromEntries(RARITIES.map((x) => [x, runs.reduce((a, r) => a + r.rarity[x], 0)]));
        rows.push({
          difficulty, areaId, level, seeds,
          ttk: fin.length ? mean(fin.map((r) => r.ttk)) : Infinity,
          damageTakenPct: mean(runs.map((r) => r.damageTakenPct)),
          deaths: mean(runs.map((r) => r.deaths)),
          clearRate: runs.filter((r) => r.cleared).length / runs.length,
          xpPerMin: mean(runs.map((r) => r.xpPerMin)),
          levelFraction: mean(runs.map((r) => r.levelFraction)),
          killsPerMin: mean(runs.map((r) => r.kills / (r.elapsed / 60))),
          rarity: rar, stepErrors: runs.reduce((a, r) => a + r.stepErrors, 0),
          threat: threat(RPG, areaId, level, difficulty),
        });
      }
    }
  }
  return { rows, RPG };
}

const f1 = (v) => (Number.isFinite(v) ? v.toFixed(1) : '  inf');
const f2 = (v) => (Number.isFinite(v) ? v.toFixed(2) : '  inf');
const pad = (s, n) => String(s).padStart(n);

function table(title, header, lines) {
  return [`\n${title}`, header.map((h, i) => pad(h, i === 0 ? 10 : 9)).join(' '), ...lines].join('\n');
}

/** Per-area-by-level tables for each difficulty, then per-difficulty summaries and the rarity distribution. */
export function formatReport(rpg, rows, levels) {
  const out = [];
  const by = (d, a) => rows.filter((r) => r.difficulty === d && r.areaId === a);
  const metrics = [
    ['TTK (s per kill)', (r) => f2(r.ttk)],
    ['Damage taken (% of max HP per run)', (r) => f2(r.damageTakenPct * 100)],
    ['XP/min', (r) => f1(r.xpPerMin)],
    ['Level progress per run (fraction of the current level)', (r) => f2(r.levelFraction)],
    ['Deaths per run', (r) => f2(r.deaths)],
  ];
  const difficulties = [...new Set(rows.map((r) => r.difficulty))];
  const areas = [...new Set(rows.map((r) => r.areaId))];
  for (const d of difficulties) {
    for (const [name, fn] of metrics) {
      const lines = areas.map((a) => {
        const rs = by(d, a);
        return [pad(a, 10), ...levels.map((l) => pad(fn(rs.find((r) => r.level === l)), 9))].join(' ');
      });
      out.push(table(`[${d}] ${name} — rows: area, columns: player level`, ['area', ...levels.map((l) => 'L' + l)], lines));
    }
  }
  const sumLines = difficulties.map((d) => {
    const rs = rows.filter((r) => r.difficulty === d);
    return [pad(d, 10), pad(f2(mean(rs.map((r) => r.ttk).filter(Number.isFinite))), 9),
      pad(f2(mean(rs.map((r) => r.damageTakenPct)) * 100), 9), pad(f2(mean(rs.map((r) => r.deaths))), 9),
      pad(f1(mean(rs.map((r) => r.xpPerMin))), 9)].join(' ');
  });
  out.push(table('Per-difficulty summary (mean over areas and levels)', ['difficulty', 'TTK s', 'dmg %HP', 'deaths', 'XP/min'], sumLines));
  const areaLines = areas.map((a) => {
    const rs = rows.filter((r) => r.areaId === a);
    return [pad(a, 10), pad(f2(mean(rs.map((r) => r.ttk).filter(Number.isFinite))), 9),
      pad(f2(mean(rs.map((r) => r.damageTakenPct)) * 100), 9), pad(f2(mean(rs.map((r) => r.deaths))), 9),
      pad(f1(mean(rs.map((r) => r.xpPerMin))), 9)].join(' ');
  });
  out.push(table('Per-area summary (mean over difficulties and levels)', ['area', 'TTK s', 'dmg %HP', 'deaths', 'XP/min'], areaLines));
  out.push(table('XP pace: kills needed for the next level (analytic, spawn-table average) — rows: difficulty/area',
    ['diff/area', ...levels.map((l) => 'L' + l)],
    difficulties.flatMap((d) => ['forest', 'caves'].map((a) => [pad(d.slice(0, 4) + '/' + a.slice(0, 4), 10),
      ...levels.map((l) => pad(Math.ceil(killsToLevel(rpg, a, l, d)), 9))].join(' ')))));
  const rarLines = difficulties.map((d) => {
    const rs = rows.filter((r) => r.difficulty === d);
    const tot = Object.fromEntries(RARITIES.map((x) => [x, rs.reduce((a, r) => a + r.rarity[x], 0)]));
    const all = Object.values(tot).reduce((a, b) => a + b, 0) || 1;
    return [pad(d, 10), ...RARITIES.map((x) => pad(f1((tot[x] / all) * 100) + '%', 9)), pad(all, 9)].join(' ');
  });
  out.push(table('Loot rarity distribution (share of drops)', ['difficulty', ...RARITIES, 'drops'], rarLines));
  return out.join('\n');
}

async function main(argv) {
  const arg = (name, dflt) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : dflt; };
  const quick = argv.includes('--quick');
  const levels = arg('--levels', '') ? arg('--levels').split(',').map(Number) : quick ? [1, 5, 10, 15] : Array.from({ length: 15 }, (_, i) => i + 1);
  const { rows, RPG } = await sweep({ levels, seeds: Number(arg('--seeds', quick ? 1 : 3)), seconds: Number(arg('--seconds', 60)) });
  if (argv.includes('--json')) console.log(JSON.stringify(rows, null, 1));
  else {
    console.log(formatReport(RPG, rows, levels));
    console.log('\nGate route (normal, every monster killed once, no respawn) — share of the area that must be cleared to open the next gate:');
    for (const run of gateRoute(RPG, { seeds: Number(arg('--seeds', quick ? 1 : 3)) })) {
      for (const st of run.steps) {
        console.log(`  seed ${run.seed} ${st.area}->${st.gate}: gate L${st.gateLevel} (${st.gateXp} XP total), area supplies ${st.supply} XP, cumulative ${st.cumulativeXp} (L${st.level}), clear ${(Math.max(0, st.clearedShare) * 100).toFixed(0)}% of the area`);
      }
    }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((e) => { console.error(e); process.exit(1); });
}

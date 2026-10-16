/**
 * Map generation: turns an AreaDefinition into a Tilemap plus spawn, exits, encounter slots and boss slot.
 * Deterministic: every random choice draws from the `rng` argument, so one seed gives one map.
 * Headless: no window/document access at load time.
 *
 * Coordinates: spawn, exits, encounterSlots and bossSlot are pixel centres of tiles, the same convention
 * sim/sim.js uses for the player's spawn. Slot radius `r` is in pixels.
 *
 * Kinds: 'authored' (ASCII layout), 'cave' (cellular automata), 'open' (forest scatter).
 * 'rooms' (BSP) is not implemented yet and throws.
 */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  const TILE = RPG.world.tilemap.TILE;

  const DEFAULT_TILE_SIZE = 32;
  const DEFAULT_BUDGET = 4; // encounter slots per 100 walkable tiles when the area sets no budget
  const MIN_SIZE = 5; // smallest generated map: a 3x3 interior around a spawn
  const CAVE_FILL = 0.45; // chance an interior tile starts as wall
  const CAVE_STEPS = 5; // cellular automata smoothing passes
  const CAVE_WALL_LIMIT = 5; // a tile becomes wall when >= this many of its 3x3 neighbourhood are walls
  const CAVE_MIN_OPEN = 0.3; // regenerate until the largest open region covers this share of the interior
  const CAVE_ATTEMPTS = 8;
  const FOREST_TREE_CHANCE = 0.16; // chance an interior tile of an 'open' area starts as a tree (wall)
  const SPAWN_CLEAR_RADIUS = 1; // tiles around the spawn forced open so the start is never boxed in
  const SLOT_MIN_DISTANCE = 5; // walkable tiles from spawn; keeps encounters off the start
  const MIN_SPACING = 3; // Chebyshev tiles between boss, exits and encounter slots
  const ENCOUNTER_RADIUS_TILES = 2;

  const NEIGHBOURS = [[1, 0], [-1, 0], [0, 1], [0, -1]];

  function idxOf(map, tx, ty) {
    return ty * map.w + tx;
  }

  function centreOf(map, i) {
    const tx = i % map.w;
    const ty = Math.floor(i / map.w);
    return { x: (tx + 0.5) * map.tileSize, y: (ty + 0.5) * map.tileSize };
  }

  /** Chebyshev distance < MIN_SPACING to any index in `list`. */
  function tooClose(map, i, list) {
    const tx = i % map.w;
    const ty = Math.floor(i / map.w);
    for (const j of list) {
      const ox = Math.abs(j % map.w - tx);
      const oy = Math.abs(Math.floor(j / map.w) - ty);
      if (Math.max(ox, oy) < MIN_SPACING) return true;
    }
    return false;
  }

  /**
   * Breadth-first distances in steps from (sx, sy) over walkable tiles. Unreachable tiles (and walls) are -1.
   * Returns an Int32Array indexed like `map.tiles`.
   */
  function distancesFrom(map, sx, sy) {
    const { w, h } = map;
    const dist = new Int32Array(w * h).fill(-1);
    if (RPG.world.tilemap.isSolid(map, sx, sy)) return dist;
    const queue = new Int32Array(w * h);
    let head = 0;
    let tail = 0;
    const start = idxOf(map, sx, sy);
    dist[start] = 0;
    queue[tail++] = start;
    while (head < tail) {
      const cur = queue[head++];
      const tx = cur % w;
      const ty = Math.floor(cur / w);
      for (const [ox, oy] of NEIGHBOURS) {
        const nx = tx + ox;
        const ny = ty + oy;
        if (RPG.world.tilemap.isSolid(map, nx, ny)) continue;
        const n = idxOf(map, nx, ny);
        if (dist[n] !== -1) continue;
        dist[n] = dist[cur] + 1;
        queue[tail++] = n;
      }
    }
    return dist;
  }

  /** Distances from the largest walkable region's first tile, or null if the map has no walkable tile. */
  function largestRegion(map) {
    const seen = new Uint8Array(map.w * map.h);
    let best = null;
    let bestSize = 0;
    for (let i = 0; i < map.tiles.length; i++) {
      if (seen[i]) continue;
      const tx = i % map.w;
      const ty = Math.floor(i / map.w);
      if (RPG.world.tilemap.isSolid(map, tx, ty)) continue;
      const dist = distancesFrom(map, tx, ty);
      let size = 0;
      for (let j = 0; j < dist.length; j++) {
        if (dist[j] >= 0) {
          seen[j] = 1;
          size++;
        }
      }
      if (size > bestSize) {
        best = dist;
        bestSize = size;
      }
    }
    return best;
  }

  /** Fills the whole grid with wall, then `pick()` for each interior tile in row-major order. */
  function stampInterior(map, pick) {
    const { w, h, tiles } = map;
    tiles.fill(TILE.WALL);
    for (let ty = 1; ty < h - 1; ty++) {
      for (let tx = 1; tx < w - 1; tx++) tiles[idxOf(map, tx, ty)] = pick();
    }
  }

  /** One cellular automata pass. Out-of-bounds cells count as walls, so the border stays closed. */
  function smoothCave(map) {
    const { w, h, tiles } = map;
    const next = tiles.slice();
    for (let ty = 1; ty < h - 1; ty++) {
      for (let tx = 1; tx < w - 1; tx++) {
        let walls = 0;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const x = tx + dx;
            const y = ty + dy;
            if (x < 0 || y < 0 || x >= w || y >= h || tiles[idxOf(map, x, y)] === TILE.WALL) walls++;
          }
        }
        next[idxOf(map, tx, ty)] = walls >= CAVE_WALL_LIMIT ? TILE.WALL : TILE.FLOOR;
      }
    }
    tiles.set(next);
  }

  function generateCave(map, rng) {
    const interior = (map.w - 2) * (map.h - 2);
    for (let attempt = 1; ; attempt++) {
      stampInterior(map, () => (rng.next() < CAVE_FILL ? TILE.WALL : TILE.FLOOR));
      for (let step = 0; step < CAVE_STEPS; step++) smoothCave(map);
      const region = largestRegion(map);
      const size = region ? region.reduce((n, d) => (d >= 0 ? n + 1 : n), 0) : 0;
      if (size >= CAVE_MIN_OPEN * interior || attempt >= CAVE_ATTEMPTS) return;
    }
  }

  function generateOpen(map, rng) {
    stampInterior(map, () => (rng.next() < FOREST_TREE_CHANCE ? TILE.WALL : TILE.FLOOR));
  }

  /**
   * Generated maps: spawn at the walkable tile of the largest region nearest the map centre, clear a small
   * ring around it, then wall off anything not reachable from it. Afterwards every floor tile is connected.
   */
  function prepareGenerated(map, id) {
    const region = largestRegion(map);
    if (!region) throw new Error(`mapgen.generate: area '${id}' has no walkable tiles`);
    const cx = (map.w - 1) / 2;
    const cy = (map.h - 1) / 2;
    let spawn = -1;
    let bestD = Infinity;
    for (let i = 0; i < region.length; i++) {
      if (region[i] < 0) continue;
      const dx = (i % map.w) - cx;
      const dy = Math.floor(i / map.w) - cy;
      if (dx * dx + dy * dy < bestD) {
        bestD = dx * dx + dy * dy;
        spawn = i;
      }
    }
    const sx = spawn % map.w;
    const sy = Math.floor(spawn / map.w);
    for (let dy = -SPAWN_CLEAR_RADIUS; dy <= SPAWN_CLEAR_RADIUS; dy++) {
      for (let dx = -SPAWN_CLEAR_RADIUS; dx <= SPAWN_CLEAR_RADIUS; dx++) {
        const tx = sx + dx;
        const ty = sy + dy;
        if (tx > 0 && ty > 0 && tx < map.w - 1 && ty < map.h - 1) map.tiles[idxOf(map, tx, ty)] = TILE.FLOOR;
      }
    }
    const dist = distancesFrom(map, sx, sy);
    for (let i = 0; i < dist.length; i++) {
      if (dist[i] < 0) map.tiles[i] = TILE.WALL;
    }
    return { spawn, dist };
  }

  /**
   * Places the boss (farthest reachable tile, if the area has one), exits and encounter slots.
   * `authoredPortals` lists the PORTAL tile indices of an authored layout; null means choose exits here.
   * Returns { bossSlot?, exits, encounterSlots } in pixel coordinates.
   */
  function placeFeatures(map, def, rng, dist, authoredPortals) {
    const ts = map.tileSize;
    const reachable = [];
    let farthest = -1;
    for (let i = 0; i < dist.length; i++) {
      if (dist[i] < 0) continue;
      reachable.push(i);
      if (farthest < 0 || dist[i] > dist[farthest]) farthest = i;
    }
    const taken = [];
    const out = { exits: [], encounterSlots: [] };

    if (def.boss && farthest >= 0) {
      out.bossSlot = centreOf(map, farthest);
      taken.push(farthest);
    }

    const exitDefs = def.exits || [];
    const exitIdx = authoredPortals || [];
    if (!authoredPortals) {
      // Farthest first, so exits sit at the far end of the area, away from spawn and from the boss.
      const order = reachable.filter((i) => dist[i] > 0).sort((a, b) => dist[b] - dist[a] || a - b);
      for (const i of order) {
        if (exitIdx.length >= exitDefs.length) break;
        if (tooClose(map, i, taken)) continue;
        exitIdx.push(i);
        taken.push(i);
      }
    }

    exitIdx.forEach((i, k) => {
      map.tiles[i] = TILE.PORTAL;
      const to = exitDefs[k] ? exitDefs[k].to : null;
      out.exits.push({ ...centreOf(map, i), toArea: to });
    });

    // Encounter slots: shuffled candidates, greedy spacing, budgeted per 100 reachable tiles.
    const candidates = reachable.filter((i) => dist[i] >= SLOT_MIN_DISTANCE);
    rng.shuffle(candidates);
    const want = Math.round((reachable.length * (def.budget || DEFAULT_BUDGET)) / 100);
    for (const i of candidates) {
      if (out.encounterSlots.length >= want) break;
      if (tooClose(map, i, taken)) continue;
      taken.push(i);
      out.encounterSlots.push({ ...centreOf(map, i), r: ENCOUNTER_RADIUS_TILES * ts });
    }
    return out;
  }

  function generateAuthored(def) {
    if (!Array.isArray(def.layout)) throw new Error(`mapgen.generate: authored area '${def.id}' has no layout`);
    const map = RPG.world.tilemap.fromRows(def.layout, def.tileSize || DEFAULT_TILE_SIZE);
    const spawnTile = RPG.world.tilemap.findTile(map, TILE.SPAWN);
    if (!spawnTile) throw new Error(`mapgen.generate: authored area '${def.id}' has no spawn ('S') tile`);
    const portals = [];
    for (let i = 0; i < map.tiles.length; i++) {
      if (map.tiles[i] === TILE.PORTAL) portals.push(i);
    }
    return { map, spawn: idxOf(map, spawnTile.tx, spawnTile.ty), portals };
  }

  /**
   * Builds the map for `def` (an AreaDefinition). `rng` is an RPG.core.Rng.
   * Returns { map, spawn:{x,y}, exits:[{x,y,toArea}], encounterSlots:[{x,y,r}], bossSlot?:{x,y} }.
   */
  function generate(def, rng) {
    if (!def || typeof def !== 'object') throw new Error('mapgen.generate: area definition is required');
    const tileSize = def.tileSize || DEFAULT_TILE_SIZE;
    let map;
    let spawn;
    let dist;
    let authoredPortals = null;

    if (def.kind === 'authored') {
      const built = generateAuthored(def);
      map = built.map;
      spawn = built.spawn;
      authoredPortals = built.portals;
      dist = distancesFrom(map, spawn % map.w, Math.floor(spawn / map.w));
    } else if (def.kind === 'cave' || def.kind === 'open') {
      const size = def.size;
      if (!Array.isArray(size) || size.length !== 2 || size[0] < MIN_SIZE || size[1] < MIN_SIZE) {
        throw new Error(`mapgen.generate: area '${def.id}' needs size [w, h] with each side >= ${MIN_SIZE}`);
      }
      map = RPG.world.tilemap.create(size[0], size[1], tileSize);
      if (def.kind === 'cave') generateCave(map, rng);
      else generateOpen(map, rng);
      ({ spawn, dist } = prepareGenerated(map, def.id));
    } else {
      throw new Error(`mapgen.generate: unknown or unimplemented kind '${def.kind}' for area '${def.id}'`);
    }

    map.theme = def.theme || '';
    const features = placeFeatures(map, def, rng, dist, authoredPortals);
    return { map, spawn: centreOf(map, spawn), ...features };
  }

  RPG.world.mapgen = { generate };
})(globalThis.RPG = globalThis.RPG || {});

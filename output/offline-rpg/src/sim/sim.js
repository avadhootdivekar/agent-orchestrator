/* Simulation entry: builds a world on a map and advances it one fixed tick. Owns all mutation (ARCHITECTURE §6). */
(function (RPG) {
  'use strict';

  RPG.sim = RPG.sim || {};

  /** World on the named map (RPG.data.maps), player standing on its spawn tile. opts: { seed, mapId }. */
  function createWorld(opts) {
    opts = opts || {};
    var def = RPG.data.maps[opts.mapId || 'test-map'];
    var tm = RPG.world.tilemap;
    var map = tm.fromRows(def.layout, def.tileSize);
    map.theme = def.theme;
    var world = RPG.core.state.createWorldState({ seed: opts.seed });
    var s = tm.findTile(map, tm.TILE.SPAWN) || { tx: 1, ty: 1 };
    var spawn = { x: (s.tx + 0.5) * map.tileSize, y: (s.ty + 0.5) * map.tileSize };
    world.area = { id: def.id, map: map, spawn: spawn, exits: [], visited: {} };
    world.area.visited[def.id] = true;
    world.player = RPG.sim.player.create(world, spawn.x, spawn.y);
    return world;
  }

  /** One fixed tick. `intent` is plain data (input/intent.js shape), so scripted intents replay exactly. */
  function step(world, intent, dt) {
    RPG.sim.player.step(world, intent, dt);
    world.tick++;
    world.time += dt;
  }

  RPG.sim.createWorld = createWorld;
  RPG.sim.step = step;
})(globalThis.RPG = globalThis.RPG || {});

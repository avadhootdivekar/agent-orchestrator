/* Areas (INTEGRATION-PLAN §2.2 stage 15, §3.3, §4): enter an area (map from mapgen, monsters from encounters, boss and
   modifiers in the arena, arrival on the portal back), exit checks, area gating, boss bookkeeping and cleared-slot
   bookkeeping for saves. Headless: no DOM. Every random choice comes from an Rng seeded by hash(world.seed, areaId),
   so one seed and one area always give the same map and the same monsters.

   enter(world, areaId, fromId?, opts?)   opts: { restorePos: {x, y}, silent }. `fromId` may be omitted, and an options
                                          object may stand in for it: enter(world, areaId, { restorePos }).
   checkExits(world)                      -> the area id entered, or null
   isUnlocked(world, areaId)              gating along `order`: town -> forest -> caves -> arena
   markBossDefeated(world, bossId)        boss restarts on re-entry until this has been called
   onMonsterDead(world, monster)          records a fully cleared encounter slot in world.cleared[areaId]

   World fields owned here: world.area { id, def, map, spawn, exits, visited, bossSlot?, slotOf, slotLeft },
   world.cleared { areaId: [slotIndex] }, world.progress.areasUnlocked / bossesDefeated. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  // The player counts as standing on an exit within this many tiles (INTEGRATION-PLAN §2.2 stage 15).
  var EXIT_RADIUS_TILES = 0.6;
  var DEFAULT_DIFFICULTY = 'normal';
  var BOSS_ARRIVAL_LEVEL_KEY = 1; // index into area.level: the boss spawns at the area's top level

  function resolveDiff(world) {
    return RPG.world.difficulty.resolve(world.difficulty || DEFAULT_DIFFICULTY);
  }

  function areaDef(areaId) {
    var def = RPG.data.areas[areaId];
    if (!def) throw new Error('areas: unknown area ' + areaId);
    return def;
  }

  function areaRng(world, areaId) {
    return RPG.core.Rng.create(RPG.core.Rng.hashString(world.seed + ':' + areaId));
  }

  function playerLevel(world) {
    return world.player && world.player.level ? world.player.level : 1;
  }

  function bossAlive(world) {
    var def = world.area && world.area.def;
    if (!def || !def.boss) return false;
    if (world.progress.bossesDefeated[def.boss.bossId]) return false;
    for (var i = 0; i < world.monsters.length; i++) {
      var m = world.monsters[i];
      if (!m.dead && m.defId === def.boss.bossId) return true;
    }
    return false;
  }

  // ---- gating ---------------------------------------------------------------------------------------------------

  function requirementMet(world, requires) {
    var req = requires || {};
    if (req.level !== undefined && playerLevel(world) < req.level) return false;
    var bosses = req.bossesDefeated || [];
    for (var i = 0; i < bosses.length; i++) {
      if (!world.progress.bossesDefeated[bosses[i]]) return false;
    }
    return true;
  }

  function previousArea(def) {
    var best = null;
    var areas = RPG.data.areas;
    for (var id in areas) {
      var a = areas[id];
      if (a.order < def.order && (!best || a.order > best.order)) best = a;
    }
    return best;
  }

  /** Town is always open. Any other area is open once entered before, or while its predecessor along `order` is open
   *  and the predecessor's exit into it has its requirements (player level, defeated bosses) met. */
  function isUnlocked(world, areaId) {
    var def = RPG.data.areas[areaId];
    if (!def) return false;
    if (def.order === 0 || world.progress.areasUnlocked[areaId]) return true;
    var prev = previousArea(def);
    if (!prev || !isUnlocked(world, prev.id)) return false;
    var exits = prev.exits || [];
    for (var i = 0; i < exits.length; i++) {
      if (exits[i].to === areaId) return requirementMet(world, exits[i].requires);
    }
    return false;
  }

  // ---- entering -------------------------------------------------------------------------------------------------

  function clearList(list) {
    for (var i = 0; i < list.length; i++) list[i].dead = true;
  }

  // Empties every transient entity list so nothing from the old area follows the player through the portal.
  function clearEntities(world) {
    clearList(world.monsters);
    clearList(world.projectiles);
    clearList(world.effects);
    clearList(world.drops);
    RPG.world.despawnDead(world);
    if (world.telegraphs) world.telegraphs.length = 0;
    if (world.enemyProjectiles) {
      for (var i = 0; i < world.enemyProjectiles.length; i++) RPG.world.releaseProjectile(world, world.enemyProjectiles[i]);
      world.enemyProjectiles.length = 0;
    }
    if (world.fx && world.fx.damageNumbers) world.fx.damageNumbers.length = 0;
    if (world.grid && RPG.world.spatial) RPG.world.spatial.clear(world.grid);
  }

  // Scales a def's base stats by difficulty, area level and player level (difficulty.scaleMonster multipliers).
  function makeScaleFn(world, diff, areaLevel) {
    var level = playerLevel(world);
    return function (def, _level) {
      var f = RPG.world.difficulty.scaleMonster(def, level, diff, areaLevel);
      var b = def.baseStats;
      return { hp: b.hp * f.hp, damage: b.damage * f.damage, speed: b.speed * f.speed, armor: b.armor };
    };
  }

  function spawnScaled(world, defId, x, y, areaLevel, diff, opts) {
    var spawnOpts = opts || {};
    spawnOpts.level = areaLevel;
    spawnOpts.scaleFn = makeScaleFn(world, diff, areaLevel);
    var m = RPG.world.spawnMonster(world, defId, x, y, spawnOpts);
    var def = RPG.data.monsters[defId];
    var f = RPG.world.difficulty.scaleMonster(def, playerLevel(world), diff, areaLevel);
    m.areaLevel = areaLevel;
    m.xp = Math.round((def.xp || 0) * f.xp);
    m.lootQuality = f.lootQuality;
    m.goldMult = f.gold;
    // What the difficulty multiplied in, so a later difficulty change can rescale this monster (rescaleMonsters).
    m.diffScale = { playerLevel: playerLevel(world), hp: f.hp, damage: f.damage, speed: f.speed, xp: f.xp, lootQuality: f.lootQuality, gold: f.gold };
    return m;
  }

  /**
   * Applies a new difficulty to the live monsters (A4.3). Each monster that came from `spawnScaled` is multiplied by
   * newFactor / spawnFactor for hp, damage and speed: the HP ratio (and any elite shield) is preserved, a monster keeps
   * the player level and area level it spawned with, and no RNG is drawn. Summons and other unscaled monsters are left
   * alone. Reward multipliers (xp, loot quality, gold) follow, so what a kill pays matches the difficulty then in force.
   * Rescaling to the difficulty a monster already has is a no-op, and rescaling there and back restores its stats.
   */
  function rescaleMonsters(world, diffIn) {
    var diff = RPG.world.difficulty.resolve(diffIn);
    var count = 0;
    for (var i = 0; i < world.monsters.length; i++) {
      var m = world.monsters[i], old = m.diffScale;
      if (!old || m.dead) continue;
      var def = RPG.data.monsters[m.defId];
      if (!def) continue;
      var f = RPG.world.difficulty.scaleMonster(def, old.playerLevel, diff, m.areaLevel);
      var rHp = f.hp / old.hp, rDmg = f.damage / old.damage, rSpd = f.speed / old.speed;
      var oldMax = m.maxHp, newMax = Math.max(1, Math.round(oldMax * rHp));
      m.hp = m.hp * (newMax / oldMax);
      m.maxHp = newMax;
      if (typeof m.shield === 'number') m.shield *= newMax / oldMax;
      if (typeof m.affixHp === 'number') m.affixHp = m.hp;
      m.damage *= rDmg;
      m.speed *= rSpd;
      if (m.stats) {
        m.stats.setBase('maxHp', newMax);
        m.stats.setBase('damage', m.damage);
        m.stats.setBase('moveSpeed', m.speed);
      }
      m.xp = Math.round((def.xp || 0) * f.xp);
      m.lootQuality = f.lootQuality;
      m.goldMult = f.gold;
      m.diffScale = { playerLevel: old.playerLevel, hp: f.hp, damage: f.damage, speed: f.speed, xp: f.xp, lootQuality: f.lootQuality, gold: f.gold };
      count++;
    }
    return count;
  }

  function nearestSlot(slots, x, y) {
    var best = 0, bestD = Infinity;
    for (var i = 0; i < slots.length; i++) {
      var dx = slots[i].x - x, dy = slots[i].y - y, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  function populate(world, def, gen, rng, diff) {
    var area = world.area;
    var cleared = world.cleared[def.id] || [];
    var lo = def.level[0], hi = def.level[1];

    if (gen.encounterSlots.length > 0 && def.spawnTable.length > 0) {
      // Composed over every slot, so a slot's contents never depend on which other slots were cleared.
      var placements = RPG.world.encounters.compose(def, gen.encounterSlots, hi, diff, rng);
      area.slotLeft = gen.encounterSlots.map(function () { return 0; });
      for (var i = 0; i < placements.length; i++) {
        var p = placements[i];
        var slot = nearestSlot(gen.encounterSlots, p.x, p.y);
        var level = rng.int(lo, hi);
        if (cleared.indexOf(slot) !== -1) continue;
        var m = spawnScaled(world, p.defId, p.x, p.y, level, diff, { elite: p.elite, affixes: p.affixes });
        area.slotOf[m.id] = slot;
        area.slotLeft[slot]++;
      }
    }

    if (def.boss && gen.bossSlot && !world.progress.bossesDefeated[def.boss.bossId]) {
      var bossDef = RPG.data.monsters[def.boss.bossId];
      var boss = spawnScaled(world, def.boss.bossId, gen.bossSlot.x, gen.bossSlot.y, def.level[BOSS_ARRIVAL_LEVEL_KEY], diff);
      boss.modifiers = RPG.world.encounters.bossModifiers(bossDef, diff, rng);
    }
  }

  function isWalkableFloor(map, tx, ty) {
    var tm = RPG.world.tilemap;
    return !tm.isSolid(map, tx, ty) && map.tiles[ty * map.w + tx] !== tm.TILE.PORTAL;
  }

  // Nearest non-solid, non-portal tile centre to (x, y), searched in growing square rings (row-major within a ring,
  // so the result is deterministic). Returns null only for a map with no such tile.
  function nearestWalkable(map, x, y) {
    var cx = Math.floor(x / map.tileSize), cy = Math.floor(y / map.tileSize);
    var maxR = Math.max(map.w, map.h);
    for (var r = 0; r <= maxR; r++) {
      var best = null, bestD = Infinity;
      for (var ty = cy - r; ty <= cy + r; ty++) {
        for (var tx = cx - r; tx <= cx + r; tx++) {
          if (Math.max(Math.abs(tx - cx), Math.abs(ty - cy)) !== r) continue;
          if (!RPG.world.tilemap.inBounds(map, tx, ty) || !isWalkableFloor(map, tx, ty)) continue;
          var px = (tx + 0.5) * map.tileSize, py = (ty + 0.5) * map.tileSize;
          var d = (px - x) * (px - x) + (py - y) * (py - y);
          if (d < bestD) { bestD = d; best = { x: px, y: py }; }
        }
      }
      if (best) return best;
    }
    return null;
  }

  /** Where `restorePos` lands: itself when it is on a walkable non-portal tile, otherwise the nearest such tile. */
  function restorePos(world, pos) {
    var map = world.area.map;
    var tx = Math.floor(pos.x / map.tileSize), ty = Math.floor(pos.y / map.tileSize);
    if (RPG.world.tilemap.inBounds(map, tx, ty) && isWalkableFloor(map, tx, ty)) return { x: pos.x, y: pos.y };
    return nearestWalkable(map, pos.x, pos.y) || { x: world.area.spawn.x, y: world.area.spawn.y };
  }

  // Arrival: next to the portal that leads back to `fromId` (one tile off it, so the arrival does not trigger the exit
  // straight away), else the area's spawn.
  function arrivalPos(world, fromId) {
    var area = world.area;
    if (fromId) {
      for (var i = 0; i < area.exits.length; i++) {
        var ex = area.exits[i];
        if (ex.toArea !== fromId) continue;
        var map = area.map, ts = map.tileSize;
        var ptx = Math.floor(ex.x / ts), pty = Math.floor(ex.y / ts);
        var steps = [[0, 1], [0, -1], [1, 0], [-1, 0], [1, 1], [-1, 1], [1, -1], [-1, -1]];
        for (var k = 0; k < steps.length; k++) {
          var tx = ptx + steps[k][0], ty = pty + steps[k][1];
          if (RPG.world.tilemap.inBounds(map, tx, ty) && isWalkableFloor(map, tx, ty)) {
            return { x: (tx + 0.5) * ts, y: (ty + 0.5) * ts };
          }
        }
        return nearestWalkable(map, ex.x, ex.y) || { x: area.spawn.x, y: area.spawn.y };
      }
    }
    return { x: area.spawn.x, y: area.spawn.y };
  }

  function enter(world, areaId, fromArg, optsArg) {
    var opts = optsArg || (fromArg && typeof fromArg === 'object' ? fromArg : {});
    var explicitFrom = typeof fromArg === 'string' ? fromArg : null;
    var fromId = explicitFrom || (world.area ? world.area.id : null);
    var def = areaDef(areaId);
    var diff = resolveDiff(world);

    world.progress = world.progress || { bossesDefeated: {}, areasUnlocked: {} };
    world.progress.bossesDefeated = world.progress.bossesDefeated || {};
    world.progress.areasUnlocked = world.progress.areasUnlocked || {};
    world.cleared = world.cleared || {};
    var visited = (world.area && world.area.visited) || {};

    clearEntities(world);

    var rng = areaRng(world, areaId);
    var popRng = rng.fork('populate');
    var gen = RPG.world.mapgen.generate(def, rng);

    world.area = {
      id: areaId, def: def, map: gen.map, spawn: gen.spawn, exits: gen.exits || [], visited: visited,
      slotOf: {}, slotLeft: [], level: def.level[1]
    };
    if (gen.bossSlot) world.area.bossSlot = gen.bossSlot;
    world.area.visited[areaId] = true;
    world.progress.areasUnlocked[areaId] = true;

    populate(world, def, gen, popRng, diff);

    var pos = opts.restorePos ? restorePos(world, opts.restorePos) : arrivalPos(world, explicitFrom);
    var p = world.player;
    if (p) {
      p.x = pos.x; p.y = pos.y;
      p.vx = 0; p.vy = 0;
    }

    if (!opts.silent && !opts.restorePos) {
      RPG.core.events.emit(RPG.core.events.NAMES.AREA_CHANGED, { from: fromId, to: areaId });
    }
    return world.area;
  }

  /** If the player stands on an exit (within EXIT_RADIUS_TILES) whose target is unlocked, enters it. Exits are locked
   *  while the area's boss is alive. Returns the new area id, or null when nothing happened. */
  function checkExits(world) {
    var area = world.area, p = world.player;
    if (!area || !p || bossAlive(world)) return null;
    var reach = EXIT_RADIUS_TILES * area.map.tileSize;
    for (var i = 0; i < area.exits.length; i++) {
      var ex = area.exits[i];
      if (!ex.toArea) continue;
      var dx = p.x - ex.x, dy = p.y - ex.y;
      if (dx * dx + dy * dy > reach * reach) continue;
      if (!isUnlocked(world, ex.toArea)) continue;
      var from = area.id;
      enter(world, ex.toArea, from);
      return ex.toArea;
    }
    return null;
  }

  function markBossDefeated(world, bossId) {
    world.progress.bossesDefeated[bossId] = true;
  }

  /** Call once per monster death. Returns true when this death emptied its encounter slot (now recorded as cleared). */
  function onMonsterDead(world, monster) {
    var area = world.area;
    if (!area || !area.slotOf || area.slotOf[monster.id] === undefined) return false;
    var slot = area.slotOf[monster.id];
    delete area.slotOf[monster.id];
    area.slotLeft[slot]--;
    if (area.slotLeft[slot] > 0) return false;
    var list = world.cleared[area.id] || (world.cleared[area.id] = []);
    if (list.indexOf(slot) === -1) list.push(slot);
    return true;
  }

  RPG.world.areas = {
    enter: enter,
    checkExits: checkExits,
    isUnlocked: isUnlocked,
    markBossDefeated: markBossDefeated,
    onMonsterDead: onMonsterDead,
    restorePos: restorePos,
    rescaleMonsters: rescaleMonsters,
    EXIT_RADIUS_TILES: EXIT_RADIUS_TILES
  };
})(globalThis.RPG);

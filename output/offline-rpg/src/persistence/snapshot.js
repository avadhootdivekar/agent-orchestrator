// World <-> save document bridge (docs/SAVE-SCHEMA.md). snapshot(world, game) builds the save body that
// persistence.serialize accepts; restore(doc) rebuilds a playable world from a body or a full/old-version document.
// restore never touches its input or any live world: it builds a brand-new world and returns it only if every check passed.
(function (RPG) {
  'use strict';
  var P = RPG.persistence = RPG.persistence || {};
  var GAME_VERSION = '1.0.0';
  var MOD_KINDS = ['flat', 'inc', 'percent', 'more'];
  var DIFFICULTY_META = ['name', 'ranges']; // difficulty fields that are presentation/slider metadata, not part of the save

  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
  function fail(code, error, details) { var r = { ok: false, code: code, error: error }; if (details) r.details = details; return r; }
  function bad(code, message, details) { var e = new Error(message); e.code = code; if (details) e.details = details; return e; }

  function rngState(r) { return r && typeof r.getState === 'function' ? r.getState() : undefined; }

  function difficultyOf(world, game) {
    var d = world.difficulty || (game && game.difficulty);
    if (typeof d === 'string') d = RPG.world.difficulty ? RPG.world.difficulty.resolve(d) : { id: d };
    if (!isObj(d)) throw bad('shape', 'snapshot: world has no difficulty');
    var out = {};
    Object.keys(d).forEach(function (k) { if (DIFFICULTY_META.indexOf(k) < 0) out[k] = clone(d[k]); });
    return out;
  }

  function cooldownsOf(p) {
    var out = {};
    [p.skills && p.skills.cooldowns, p.cooldowns].forEach(function (c) {
      if (isObj(c)) Object.keys(c).forEach(function (k) { if (c[k] > 0) out[k] = c[k]; });
    });
    return out;
  }

  /**
   * snapshot(world, game) -> save body (plain data, deep-cloned; pass to persistence.serialize).
   * `game` carries what the world does not: { settings, name, gameVersion, savedAt, difficulty (fallback), extra }.
   * Throws {code:'shape'} if the result would not validate, so a bad state is never handed to the writer.
   */
  function snapshot(world, game) {
    game = game || {};
    if (!isObj(world) || !isObj(world.player) || !isObj(world.area) || !world.rng) throw bad('shape', 'snapshot: not a world with a player and area');
    var p = world.player, pr = world.progress || {}, sk = p.skills || {};
    var lootState = rngState(world.lootRng);
    var body = {
      gameVersion: game.gameVersion || GAME_VERSION,
      savedAt: game.savedAt || new Date().toISOString(),
      seed: world.seed,
      rngState: { world: rngState(world.rng), loot: lootState === undefined ? rngState(world.rng) : lootState },
      meta: { level: p.level, area: world.area.id, difficulty: null, playtime: pr.playtime || 0, name: game.name || p.name || 'Wayfarer' },
      difficulty: difficultyOf(world, game),
      player: {
        classId: p.classId, level: p.level, xp: p.xp, gold: p.gold,
        attrs: clone(p.attrs || { might: 0, agility: 0, spirit: 0 }),
        hp: p.hp, resource: p.resource, pos: { x: p.x, y: p.y }, vel: { x: p.vx || 0, y: p.vy || 0 },
        weaponSlots: clone(p.weaponSlots), activeWeapon: p.activeWeapon,
        equipment: clone(p.equipment || {}), inventory: clone(p.inventory),
        skills: {
          learned: clone(sk.learned || {}), upgrades: clone(sk.upgrades || {}), bar: clone(sk.bar),
          points: sk.points, spentPoints: sk.spentPoints
        },
        cooldowns: cooldownsOf(p)
      },
      area: { id: world.area.id, visited: clone(world.area.visited || {}), cleared: clone(world.cleared || {}) },
      progress: {
        bossesDefeated: clone(pr.bossesDefeated || {}), areasUnlocked: clone(pr.areasUnlocked || {}),
        kills: pr.kills || 0, deaths: pr.deaths || 0,
        time: typeof world.time === 'number' && isFinite(world.time) && world.time >= 0 ? world.time : 0
      },
      settings: clone(game.settings || {})
    };
    body.meta.difficulty = body.difficulty.id;
    if (isObj(game.extra)) body.extra = clone(game.extra);
    var errs = P.validate(body, { body: true });
    if (errs.length) throw bad('shape', 'snapshot: invalid state: ' + errs[0], errs);
    return body;
  }

  // ---- restore ----------------------------------------------------------------------------------------------------

  // Resolves any accepted input to a validated, migrated body: {state, warnings, version}. Throws {code}.
  function toBody(doc) {
    var r;
    if (typeof doc === 'string') r = P.deserialize(doc);
    else if (isObj(doc) && doc.format !== undefined) r = P.deserialize(JSON.stringify(doc));
    else if (isObj(doc)) {
      var errs = P.validate(doc, { body: true });
      if (errs.length) throw bad('shape', 'invalid save: ' + errs[0], errs);
      return { state: clone(doc), warnings: [], version: P.SAVE_VERSION };
    } else throw bad('shape', 'restore: save document required');
    if (!r.ok) throw bad(r.code, r.error, r.details);
    return r;
  }

  function checkItems(player) {
    var seen = {}, all = [];
    function add(it, path) { if (it) all.push([it, path]); }
    player.weaponSlots.forEach(function (it, i) { add(it, 'weaponSlots[' + i + ']'); });
    Object.keys(player.equipment).forEach(function (k) { add(player.equipment[k], 'equipment.' + k); });
    player.inventory.forEach(function (it, i) { add(it, 'inventory[' + i + ']'); });
    all.forEach(function (e) {
      e[0].mods.forEach(function (m, i) {
        if (MOD_KINDS.indexOf(m.kind) < 0) throw bad('shape', 'invalid save: ' + e[1] + '.mods[' + i + '].kind unknown (' + m.kind + ')');
      });
    });
    Object.keys(player.equipment).forEach(function (k) {
      var it = player.equipment[k];
      if (!it) return;
      if (seen[it.uid]) throw bad('shape', 'invalid save: duplicate equipped item uid ' + it.uid);
      seen[it.uid] = true;
    });
  }

  // Unknown base items (removed content) are dropped per SAVE-SCHEMA, each reported as a warning.
  function dropUnknownItems(player, warnings) {
    var defs = RPG.data && RPG.data.items;
    if (!defs) return;
    function known(it, path) {
      if (!it || Object.prototype.hasOwnProperty.call(defs, it.baseId)) return it;
      warnings.push('dropped unknown item "' + it.baseId + '" at ' + path);
      return null;
    }
    player.weaponSlots = player.weaponSlots.map(function (it, i) { return known(it, 'weaponSlots[' + i + ']'); });
    player.inventory = player.inventory.map(function (it, i) { return known(it, 'inventory[' + i + ']'); });
    Object.keys(player.equipment).forEach(function (k) { player.equipment[k] = known(player.equipment[k], 'equipment.' + k); });
  }

  function checkProgression(player) {
    var prog = RPG.world.progression;
    if (!prog) return;
    var max = prog.MAX_LEVEL;
    if (player.level < max ? player.xp >= prog.xpToNext(player.level) : player.xp !== 0) {
      throw bad('shape', 'invalid save: xp ' + player.xp + ' is out of range for level ' + player.level);
    }
  }

  // Resolves the saved area id: a defined area, else a bare map (RPG.data.maps, e.g. the dev test map), else town.
  function resolveAreaId(areaId, warnings) {
    var defs = RPG.data && RPG.data.areas, maps = RPG.data && RPG.data.maps;
    if (!defs || defs[areaId] || (maps && maps[areaId])) return areaId;
    warnings.push('unknown area "' + areaId + '"; restored in town');
    return 'town';
  }

  // World on the saved area. Defined areas are regenerated from hash(seed, areaId); bare maps come from createWorld.
  function buildWorld(seed, areaId, cleared) {
    var def = RPG.data && RPG.data.areas && RPG.data.areas[areaId];
    var world = RPG.sim.createWorld({ seed: seed, mapId: def ? undefined : areaId });
    if (!def) return world;
    var visited = world.area.visited;
    // Cleared slots must be in place before the area is populated, or their monsters spawn again.
    world.cleared = cleared;
    if (RPG.world.areas && typeof RPG.world.areas.enter === 'function') {
      RPG.world.areas.enter(world, areaId);
    } else if (RPG.world.mapgen) {
      var gen = RPG.world.mapgen.generate(def, RPG.core.Rng.create(RPG.core.Rng.hashString(seed + ':' + areaId)));
      world.area = { id: areaId, map: gen.map, spawn: gen.spawn, exits: gen.exits || [], visited: visited,
        encounterSlots: gen.encounterSlots || [] };
      if (gen.bossSlot) world.area.bossSlot = gen.bossSlot;
    }
    return world;
  }

  function applyItemMods(statBlock, items) {
    items.forEach(function (it) {
      RPG.world.equipment.itemModifiers(it).forEach(function (m) { statBlock.addModifier(m); });
    });
  }

  /**
   * restore(doc, opts?) -> {ok:true, world, game, warnings, version} | {ok:false, code, error, details?}
   * `doc` is a save body (as returned by snapshot / deserialize().state), a full document of any supported version
   * (migrated first), or its JSON text. Never throws, never mutates `doc` or any existing world: on any failure
   * nothing is returned but the error. `game` gets { settings, name, difficulty, playtime, extra? } to hand back to the app.
   */
  function restore(doc) {
    try {
      var res = toBody(doc), s = res.state, warnings = res.warnings.slice();
      var pd = s.player;
      checkItems(pd);
      checkProgression(pd);
      var diff = RPG.world.difficulty ? RPG.world.difficulty.resolve(s.difficulty) : clone(s.difficulty);

      var areaId = resolveAreaId(s.area.id, warnings);
      var world = buildWorld(s.seed, areaId, clone(s.area.cleared));
      world.difficulty = diff;
      world.area.visited = clone(s.area.visited);
      world.area.visited[areaId] = true;
      world.cleared = clone(s.area.cleared);
      world.progress = {
        bossesDefeated: clone(s.progress.bossesDefeated), areasUnlocked: clone(s.progress.areasUnlocked),
        kills: s.progress.kills, deaths: s.progress.deaths, playtime: s.meta.playtime
      };
      world.time = typeof s.progress.time === 'number' ? s.progress.time : 0;

      dropUnknownItems(pd, warnings);
      var p = world.player;
      p.classId = pd.classId; p.name = s.meta.name || p.name;
      p.level = pd.level; p.xp = pd.xp; p.gold = pd.gold; p.attrs = clone(pd.attrs);
      p.weaponSlots = pd.weaponSlots; p.activeWeapon = pd.activeWeapon;
      p.equipment = pd.equipment; p.inventory = pd.inventory;

      // Derived stats: fresh base + equipped-item modifiers + learned-passive modifiers (same sources the live game uses).
      p.statBlock = new RPG.world.StatBlock();
      var equipped = Object.keys(pd.equipment).map(function (k) { return pd.equipment[k]; }).filter(Boolean);
      if (RPG.world.equipment) applyItemMods(p.statBlock, equipped);
      // Level growth is a 'level' modifier per stat (see sim/rewards.js applyLevelGrowth); it is derived, not saved, so reapply it.
      var growth = RPG.world.progression && RPG.world.progression.LEVEL_STAT_GROWTH;
      if (growth && pd.level > 1) {
        Object.keys(growth).forEach(function (stat) {
          p.statBlock.addModifier({ source: 'level', stat: stat, kind: 'flat', value: growth[stat] * (pd.level - 1) });
        });
      }
      var shim = { level: pd.level, stats: p.statBlock, skills: null };
      if (RPG.world.skills) {
        RPG.world.skills.restore(shim, pd.skills);
        var back = RPG.world.skills.serialize(shim);
        if (JSON.stringify(back) !== JSON.stringify({ learned: pd.skills.learned, upgrades: pd.skills.upgrades, bar: pd.skills.bar, points: pd.skills.points, spentPoints: pd.skills.spentPoints })) {
          warnings.push('skills adjusted to current content (removed skills refunded as points)');
        }
        p.skills = shim.skills;
      } else {
        p.skills = { learned: clone(pd.skills.learned), upgrades: clone(pd.skills.upgrades), bar: clone(pd.skills.bar), points: pd.skills.points, spentPoints: pd.skills.spentPoints };
      }
      p.skills.cooldowns = p.skills.cooldowns || {};
      p.cooldowns = clone(pd.cooldowns || {});
      RPG.sim.player.refreshStats(p);
      p.maxHp = p.stats.maxHp;
      if (pd.hp > p.maxHp) { warnings.push('hp clamped to max'); }
      p.hp = Math.min(pd.hp, p.maxHp);
      p.resource = Math.min(pd.resource, p.stats.maxResource);

      var pos = pd.pos, map = world.area.map;
      if (areaId !== s.area.id || !map || !RPG.world.tilemap.inBounds(map, Math.floor(pos.x / map.tileSize), Math.floor(pos.y / map.tileSize)) ||
          RPG.world.tilemap.isSolid(map, Math.floor(pos.x / map.tileSize), Math.floor(pos.y / map.tileSize))) {
        if (areaId === s.area.id) warnings.push('saved position is not walkable; moved to area spawn');
        pos = world.area.spawn;
      }
      p.x = pos.x; p.y = pos.y; p.prevX = pos.x; p.prevY = pos.y;
      // Velocity (optional; absent in older saves = at rest). Only meaningful at the saved position, not after a move to spawn.
      var vel = pd.vel && pos === pd.pos ? pd.vel : null;
      p.vx = vel ? vel.x : 0; p.vy = vel ? vel.y : 0;

      world.rng.setState(s.rngState.world);
      if (!world.lootRng) world.lootRng = RPG.core.Rng.create(s.rngState.loot);
      else world.lootRng.setState(s.rngState.loot);

      var game = { settings: clone(s.settings), name: s.meta.name, difficulty: diff, playtime: s.meta.playtime };
      if (s.extra) game.extra = clone(s.extra);
      return { ok: true, world: world, game: game, warnings: warnings, version: res.version };
    } catch (e) {
      return fail(e && e.code ? e.code : 'shape', e && e.message ? e.message : String(e), e && e.details);
    }
  }

  P.GAME_VERSION = GAME_VERSION;
  P.snapshot = snapshot;
  P.restore = restore;
})(globalThis.RPG = globalThis.RPG || {});

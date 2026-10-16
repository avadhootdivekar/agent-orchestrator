/* Simulation stages 11-13 (docs/INTEGRATION-PLAN.md §2.2, §2.4): kill rewards and level-ups (11), pickups (12) and
   player death (13). Registered as RPG.sim.stage.rewards / .pickups / .playerDeath and called in order by sim.js.
   Headless: no DOM, no clock. Gameplay randomness (gold, drops) comes from world.lootRng only.

   Elite decision (w03-07 open question, resolved here): elites are rolled per member at spawn time by
   world/encounters.js, not per group and not at kill. This stage only reads `m.elite`. DATA-DEFINITIONS §5 still
   says "per group"; that doc is not owned by this unit and is a followup. */
(function (RPG) {
  'use strict';

  RPG.sim = RPG.sim || {};
  RPG.sim.stage = RPG.sim.stage || {};

  // Loot rolls treat an elite as one rarity step better (the elite boost is added to the table's rarityBoost).
  var ELITE_LOOT_BOOST = 1;
  // Pickup range widened by the interact key (F), in px on top of stats.pickupRadius.
  var INTERACT_PICKUP_BONUS = 24;
  // Gold drops are rolled from def.gold, a [min, max] pair, and scaled by difficulty and goldFind.
  var DEFAULT_DIFFICULTY = 'normal';
  var BOSS_ARCHETYPE = 'boss';
  // Fraction of carried gold lost on death, per difficulty.playerDeathPenalty id (DATA-DEFINITIONS §2).
  var DEATH_PENALTY = Object.freeze({ none: 0, gold10: 0.10, gold25: 0.25 });
  // Hint id on the UI_SOUND event when a pickup is refused because the inventory is full.
  var INVENTORY_FULL_HINT = 'inventory-full';

  function requireLootRng(world) {
    if (!world.lootRng) throw new Error('rewards: world.lootRng is missing (created by sim.createWorld)');
    return world.lootRng;
  }

  function areaLevelOf(world) {
    return (world.area && world.area.level) || 1;
  }

  /** Stat-independent XP base: m.xp when areas.populate set it, otherwise the def's xp scaled by difficulty. */
  function killXp(m, def, diff) {
    if (typeof m.xp === 'number') return m.xp;
    return def.xp * diff.xpMultiplier;
  }

  /**
   * Adds XP to `player` and applies every level gained: progression (level/xp), LEVEL_STAT_GROWTH as one 'level'
   * modifier per stat (value = growth x (level - 1), so re-applying it is idempotent), skill points, and a full
   * heal. Returns the level-up grants from progression.addXp; the caller emits LEVEL_UP for each.
   */
  function grantXp(player, amount) {
    var progression = RPG.world.progression;
    var prog = progression.createProgression({ xpMultiplier: 1 });
    prog.level = player.level;
    prog.xp = player.xp;
    prog.skillPointsGranted = (player.skills.spentPoints || 0) + (player.skills.points || 0);
    var result = progression.addXp(prog, amount);
    player.level = prog.level;
    player.xp = prog.xp;
    if (result.levelUps.length) applyLevelGrowth(player, result.levelUps);
    return result.levelUps;
  }

  /** Writes the 'level' modifiers for the current level and grants the skill points and heal for the new levels. */
  function applyLevelGrowth(player, levelUps) {
    var growth = RPG.world.progression.LEVEL_STAT_GROWTH;
    var gained = player.level - 1;
    player.statBlock.removeModifiersBySource('level');
    for (var stat in growth) {
      player.statBlock.addModifier({ source: 'level', stat: stat, kind: 'flat', value: growth[stat] * gained });
    }
    for (var i = 0; i < levelUps.length; i++) player.skills.points += levelUps[i].skillPoints;
    RPG.sim.player.refreshStats(player);
    player.hp = player.stats.maxHp;
    player.resource = player.stats.maxResource;
  }

  /** Rolls one kill's gold and drops and spawns them at the monster. Returns the gold amount (0 if none). */
  function dropLoot(world, m, def, diff, rng, player) {
    var gold = 0;
    if (def.gold) {
      var roll = rng.int(def.gold[0], def.gold[1]);
      gold = Math.round(roll * diff.goldMultiplier * (1 + player.statBlock.get('goldFind')));
    }
    if (gold > 0) {
      var goldDrop = RPG.world.spawnItemDrop(world, null, m.x, m.y);
      goldDrop.gold = gold;
    }
    var items = RPG.world.loot.rollMonsterDrops(rng, def, {
      areaLevel: m.areaLevel || areaLevelOf(world),
      difficulty: diff,
      eliteBoost: m.elite ? ELITE_LOOT_BOOST : 0,
    });
    var events = RPG.core.events;
    for (var i = 0; i < items.length; i++) {
      var drop = RPG.world.spawnItemDrop(world, items[i], m.x, m.y);
      events.emit(events.NAMES.ITEM_DROPPED, { dropId: drop.id, itemName: items[i].name, rarity: items[i].rarity, x: m.x, y: m.y });
    }
    return gold;
  }

  /** Stage 11: every monster killed since the last tick gets its reward exactly once (`rewarded` flag). */
  function rewards(world) {
    var player = world.player;
    var diff = RPG.world.difficulty.resolve(world.difficulty || DEFAULT_DIFFICULTY);
    var events = RPG.core.events;
    for (var i = 0; i < world.monsters.length; i++) {
      var m = world.monsters[i];
      if (!m.dead || m.rewarded) continue;
      m.rewarded = true;
      var def = RPG.data.monsters[m.defId];
      if (!def) continue;
      var isBoss = def.archetype === BOSS_ARCHETYPE;
      var xp = Math.floor(killXp(m, def, diff) * (1 + player.statBlock.get('xpGain')));
      world.progress.kills++;
      dropLoot(world, m, def, diff, requireLootRng(world), player);
      var areas = RPG.world.areas;
      if (areas) {
        areas.onMonsterDead(world, m);
        if (isBoss) areas.markBossDefeated(world, m.defId);
      } else if (isBoss) {
        world.progress.bossesDefeated[m.defId] = true;
      }
      events.emit(events.NAMES.KILL, { defId: m.defId, elite: !!m.elite, x: m.x, y: m.y, xp: xp, boss: isBoss });
      var levelUps = grantXp(player, xp);
      for (var l = 0; l < levelUps.length; l++) {
        events.emit(events.NAMES.LEVEL_UP, { level: levelUps[l].level });
      }
    }
  }

  /** Stage 12: drops within pickup range go to gold or the first free inventory slot. */
  function pickups(world, ctx) {
    var player = world.player;
    var events = RPG.core.events;
    var reach = player.statBlock.get('pickupRadius') + (ctx && ctx.actions && ctx.actions.wantInteract ? INTERACT_PICKUP_BONUS : 0);
    for (var i = 0; i < world.drops.length; i++) {
      var d = world.drops[i];
      if (d.dead) continue;
      var dx = d.x - player.x, dy = d.y - player.y;
      if (dx * dx + dy * dy > reach * reach) continue;
      if (d.gold !== undefined) {
        player.gold += d.gold;
        d.dead = true;
        events.emit(events.NAMES.PICKUP, { kind: 'gold', id: d.id, amount: d.gold });
        continue;
      }
      if (!d.item) { d.dead = true; continue; }
      if (RPG.ui.inventory.pickup(player, d.item) < 0) {
        events.emit(events.NAMES.UI_SOUND, { name: 'ui', hint: INVENTORY_FULL_HINT });
        continue;
      }
      d.dead = true;
      events.emit(events.NAMES.PICKUP, { kind: 'item', id: d.id, name: d.item.name, rarity: d.item.rarity });
    }
  }

  /** Stage 13: the first tick a dead player is seen applies the difficulty's death penalty and sets world.state. */
  function playerDeath(world) {
    var player = world.player;
    if (!player.dead || world.state === 'dead') return;
    var diff = RPG.world.difficulty.resolve(world.difficulty || DEFAULT_DIFFICULTY);
    // difficulty.resolve already rejected unknown penalty ids, so the lookup always hits.
    var penalty = DEATH_PENALTY[diff.playerDeathPenalty];
    world.progress.deaths++;
    player.gold -= Math.floor(player.gold * penalty);
    world.state = 'dead';
    RPG.core.events.emit(RPG.core.events.NAMES.PLAYER_DIED, { gold: player.gold });
  }

  RPG.sim.stage.rewards = rewards;
  RPG.sim.stage.pickups = pickups;
  RPG.sim.stage.playerDeath = playerDeath;
  RPG.sim.rewards = {
    ELITE_LOOT_BOOST: ELITE_LOOT_BOOST,
    INTERACT_PICKUP_BONUS: INTERACT_PICKUP_BONUS,
    DEATH_PENALTY: DEATH_PENALTY,
    grantXp: grantXp,
  };
})(globalThis.RPG = globalThis.RPG || {});

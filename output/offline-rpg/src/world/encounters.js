/* Seeded encounter composition (GAMEPLAY-SYSTEMS "Encounters", ARCHITECTURE "world/encounters"): turns an area's
   spawnTable, point budget and elite chance into monster placements spread over the area's encounter slots.
   Headless: no DOM. All randomness comes from the Rng passed in, so one seed gives one composition. Stats are not
   set here; difficulty.scaleMonster reads each placement's `elite` flag to do that. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var ELITE_AFFIXES = ['fast', 'shielded', 'vampiric', 'explosive-death', 'regenerating'];
  // Elite chance of the 'normal' difficulty preset. An area's eliteChance is scaled by diff.eliteChance / this value.
  var BASE_ELITE_CHANCE = 0.1;
  // Extra budget points an elite costs on top of its monster tier.
  var ELITE_COST = 1;
  // Chance that a group member after the first is drawn from the whole table, which gives ranged/melee mixes.
  var MIX_CHANCE = 0.3;

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function round2(v) { return Math.round(v * 100) / 100; }
  function weightOf(entry) { return entry.weight; }

  // Looks up a spawnable monster. Unknown ids and bosses are data errors, so they throw rather than being skipped.
  function spawnable(id) {
    var def = RPG.data.monsters[id];
    if (!def) throw new Error('encounters: unknown monster ' + id);
    if (def.archetype === 'boss') throw new Error('encounters: boss ' + id + ' cannot be spawned from a spawnTable');
    return def;
  }

  /** Budget points a member costs: its monster tier, plus ELITE_COST when elite. */
  function pointCost(defId, elite) {
    return spawnable(defId).tier + (elite ? ELITE_COST : 0);
  }

  // Table entries whose non-elite cost fits in the remaining budget.
  function affordable(table, remaining) {
    return table.filter(function (e) { return spawnable(e.monsterId).tier <= remaining; });
  }

  /**
   * Composes one area's encounters.
   *   areaDef: { spawnTable: [{ monsterId, weight, min, max }], budget, eliteChance }
   *            `budget` is the total points for this call; the caller converts the per-100-tiles figure.
   *   slots:   [{ x, y, r }] encounter slots. Each slot holds at most one group, so slots also cap the group count.
   *   level:   area level. Accepted for the documented signature; stat scaling is done by difficulty.scaleMonster.
   *   diff:    resolved difficulty with spawnDensity (scales the budget) and eliteChance (scales the elite roll).
   * Group size comes from the entry's min..max. Each member rolls elite on its own. A member that does not fit the
   * remaining budget drops to non-elite, and a member that still does not fit ends its group.
   * -> [{ defId, x, y, elite, affixes }]
   */
  function compose(areaDef, slots, level, diff, rng) {
    var table = areaDef.spawnTable;
    table.forEach(function (e) { spawnable(e.monsterId); });
    var remaining = areaDef.budget * diff.spawnDensity;
    var eliteP = clamp01(areaDef.eliteChance * diff.eliteChance / BASE_ELITE_CHANCE);
    var open = rng.shuffle(slots.slice());
    var out = [];

    for (var s = 0; s < open.length; s++) {
      var lead = rng.weighted(affordable(table, remaining), weightOf);
      if (!lead) break;
      var slot = open[s];
      var size = rng.int(lead.min, lead.max);
      for (var i = 0; i < size; i++) {
        var entry = i > 0 && rng.chance(MIX_CHANCE) ? rng.weighted(affordable(table, remaining), weightOf) : lead;
        if (!entry) break;
        var elite = rng.chance(eliteP);
        var cost = pointCost(entry.monsterId, elite);
        if (cost > remaining && elite) {
          elite = false;
          cost = pointCost(entry.monsterId, false);
        }
        if (cost > remaining) break;
        remaining -= cost;
        // Uniform over the slot's disc, so members spread out rather than stacking on the centre.
        var angle = rng.range(0, 2 * Math.PI);
        var dist = slot.r * Math.sqrt(rng.next());
        out.push({
          defId: entry.monsterId,
          x: round2(slot.x + Math.cos(angle) * dist),
          y: round2(slot.y + Math.sin(angle) * dist),
          elite: elite,
          affixes: elite ? [rng.pick(ELITE_AFFIXES)] : [],
        });
      }
    }
    return out;
  }

  /**
   * Seed-rolled boss modifiers: min(pool size, max(1, round(diff.bossHp))) distinct ids from bossDef.modifierPool.
   * -> string[]
   */
  function bossModifiers(bossDef, diff, rng) {
    var count = Math.min(bossDef.modifierPool.length, Math.max(1, Math.round(diff.bossHp)));
    return rng.shuffle(bossDef.modifierPool.slice()).slice(0, count);
  }

  RPG.world.encounters = {
    compose: compose,
    bossModifiers: bossModifiers,
    pointCost: pointCost,
    ELITE_AFFIXES: ELITE_AFFIXES,
  };
})(globalThis.RPG);

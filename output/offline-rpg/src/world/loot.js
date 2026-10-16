/* Seeded loot generation (DATA-DEFINITIONS §6): item instances from RPG.data.items, rarity weighted by area level and
   difficulty loot quality, modifier rolls within ilvl-bracket ranges, and drop tables from monster `lootTable` refs.
   Headless: no DOM. All randomness comes from the Rng passed in, so one seed gives one drop sequence. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  // Name words for the 'affix' (rare, epic) and 'unique' (legendary) rarity name styles.
  var AFFIXES = ['Sturdy', 'Gleaming', 'Grim', 'Runed', 'Hollow', 'Ashen', 'Wicked', 'Tempered'];
  var UNIQUES = ['Emberheart', 'Dawnbreaker', 'Voidtouch', 'Wardenbane', 'Stormcaller', 'Oathkeeper'];
  var EQUIP_SLOTS = RPG.data.itemSlots;

  function round3(v) { return Math.round(v * 1000) / 1000; }

  /** Rarity weights: each step up in rank is scaled by `f`, which grows with loot quality, area level and boost. */
  function rarityWeights(opts) {
    var quality = opts.lootQuality === undefined ? 1 : opts.lootQuality;
    var level = opts.areaLevel === undefined ? 1 : opts.areaLevel;
    var boost = opts.rarityBoost || 0;
    var f = quality * (1 + 0.02 * Math.max(0, level - 1)) * (1 + 0.5 * boost);
    var out = [];
    for (var id in RPG.data.rarities) {
      var r = RPG.data.rarities[id];
      out.push({ id: id, weight: r.dropWeight * Math.pow(f, r.rank) });
    }
    return out;
  }

  /** -> rarity id. */
  function rollRarity(rng, opts) {
    var table = rarityWeights(opts || {});
    return rng.weighted(table, function (e) { return e.weight; }).id;
  }

  /** Base item defs that can drop at `ilvl`; `slot` limits to one slot, otherwise equipment only (no consumables). */
  function eligibleBases(ilvl, slot) {
    var out = [];
    for (var id in RPG.data.items) {
      var d = RPG.data.items[id];
      if (slot ? d.slot !== slot : EQUIP_SLOTS.indexOf(d.slot) < 0) continue;
      if (ilvl >= d.ilvlMin && ilvl <= d.ilvlMax) out.push(d);
    }
    return out;
  }

  // Index of the ilvl bracket for a modifier, raised by the rarity's tier boost and capped at the top bracket.
  function bracketIndex(mod, ilvl, boost) {
    var idx = 0;
    for (var i = 0; i < mod.ranges.length; i++) if (ilvl >= mod.ranges[i].ilvlMin) idx = i;
    return Math.min(mod.ranges.length - 1, idx + boost);
  }

  function rollMods(rng, def, ilvl, rarity) {
    var count = rng.int(rarity.modCount[0], rarity.modCount[1]);
    var pool = [];
    for (var id in RPG.data.modifierPool) {
      var m = RPG.data.modifierPool[id];
      if (m.allowedSlots.indexOf(def.slot) >= 0) pool.push(m);
    }
    var mods = [];
    while (mods.length < count && pool.length) {
      var picked = rng.weighted(pool, function (m) { return m.weight; });
      pool.splice(pool.indexOf(picked), 1);
      var range = picked.ranges[bracketIndex(picked, ilvl, rarity.modTierBoost)];
      mods.push({ id: picked.id, stat: picked.stat, kind: picked.kind, value: round3(rng.range(range.min, range.max)) });
    }
    if (rarity.uniqueProperty) {
      mods.push({ id: 'unique', stat: 'critDamage', kind: 'flat', value: round3(rng.range(0.2, 0.4)) });
    }
    return mods;
  }

  function buildName(rng, def, rarity) {
    if (rarity.nameStyle === 'affix') return rng.pick(AFFIXES) + ' ' + def.name;
    if (rarity.nameStyle === 'unique') return rng.pick(UNIQUES) + ', ' + def.name;
    return def.name;
  }

  /**
   * Generates one ItemInstance. opts: { ilvl|areaLevel, lootQuality, rarityBoost, rarity (force), baseId (force), slot, uid }.
   * `seedTag` records the rng state before the roll, so the same item can be regenerated from it.
   */
  function generateItem(rng, opts) {
    opts = opts || {};
    var ilvl = opts.ilvl || opts.areaLevel || 1;
    var seedTag = 'loot:' + rng.getState().toString(16);
    var def = opts.baseId ? RPG.data.items[opts.baseId] : null;
    if (opts.baseId && !def) throw new Error('loot: unknown base item ' + opts.baseId);
    var rarityId = opts.rarity || rollRarity(rng, { areaLevel: ilvl, lootQuality: opts.lootQuality, rarityBoost: opts.rarityBoost });
    if (!def) {
      def = rng.weighted(eligibleBases(ilvl, opts.slot), function (d) { return d.weight; });
      if (!def) return null;
    }
    var item;
    if (EQUIP_SLOTS.indexOf(def.slot) < 0) {
      // Consumables and currency are not rolled for rarity or modifiers.
      item = { baseId: def.id, ilvl: ilvl, rarity: 'common', mods: [], name: def.name, qty: 1 };
    } else {
      var rarity = RPG.data.rarities[rarityId];
      if (!rarity) throw new Error('loot: unknown rarity ' + rarityId);
      var mods = rollMods(rng, def, ilvl, rarity);
      item = { baseId: def.id, ilvl: ilvl, rarity: rarityId, mods: mods, name: buildName(rng, def, rarity) };
    }
    item.uid = opts.uid !== undefined ? opts.uid : (rng.getState() & 0x7fffffff);
    item.seedTag = seedTag;
    return item;
  }

  /**
   * Rolls a monster's `lootTable` ({ dropChance, rolls, rarityBoost, guaranteed? }) -> ItemInstance[].
   * opts: { areaLevel, difficulty (a preset/config with lootQuality), eliteBoost }. Difficulty loot quality scales the
   * drop chance (capped at 1), the roll count and rarity weights. Guaranteed base ids always drop on a successful drop.
   */
  function rollDrops(rng, lootTable, opts) {
    opts = opts || {};
    if (!lootTable) return [];
    var quality = opts.difficulty && opts.difficulty.lootQuality !== undefined ? opts.difficulty.lootQuality : 1;
    var boost = (lootTable.rarityBoost || 0) + (opts.eliteBoost || 0);
    var base = { areaLevel: opts.areaLevel || 1, lootQuality: quality, rarityBoost: boost };
    if (!rng.chance(Math.min(1, lootTable.dropChance * quality))) return [];
    var scaled = (lootTable.rolls === undefined ? 1 : lootTable.rolls) * quality;
    var rolls = Math.floor(scaled) + (rng.chance(scaled - Math.floor(scaled)) ? 1 : 0);
    var out = [], i;
    var guaranteed = lootTable.guaranteed || [];
    for (i = 0; i < guaranteed.length; i++) out.push(generateItem(rng, Object.assign({ baseId: guaranteed[i] }, base)));
    for (i = 0; i < rolls; i++) {
      var it = generateItem(rng, base);
      if (it) out.push(it);
    }
    return out;
  }

  /** Convenience: drops for a monster definition. */
  function rollMonsterDrops(rng, monsterDef, opts) {
    return rollDrops(rng, monsterDef && monsterDef.lootTable, opts);
  }

  RPG.world.loot = {
    rarityWeights: rarityWeights, rollRarity: rollRarity, eligibleBases: eligibleBases,
    generateItem: generateItem, rollDrops: rollDrops, rollMonsterDrops: rollMonsterDrops
  };
})(globalThis.RPG);

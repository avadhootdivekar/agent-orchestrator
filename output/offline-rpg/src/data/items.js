/**
 * Item data (DATA-DEFINITIONS §6): equipment slots, base item types, the rarity table and the modifier pool
 * that seeded loot generation rolls from. Pure data; no functions, no rendering code.
 * Registers RPG.data.itemSlots, RPG.data.items, RPG.data.rarities and RPG.data.modifierPool.
 */
(function (RPG) {
  'use strict';

  RPG.data = RPG.data || {};

  const freeze = RPG.core.util.deepFreeze;

  // Equipment slots, in the order the character screen lists them.
  RPG.data.itemSlots = freeze(['weapon', 'offhand', 'head', 'chest', 'legs', 'boots', 'ring', 'amulet']);

  // Item level brackets. A modifier's roll range is picked by the bracket that contains the item's ilvl.
  const ILVL_BRACKET_STARTS = [1, 11, 21];

  function item(def) {
    return Object.freeze(Object.assign({ stackable: false, consumable: null, weaponId: null }, def));
  }

  RPG.data.items = freeze({
    // Weapons: the weapon definitions themselves live in src/data/weapons.js; these only give them a drop identity.
    'sword': item({ id: 'sword', name: 'Sword', slot: 'weapon', weaponId: 'sword', base: { stats: {} },
      ilvlMin: 1, ilvlMax: 30, weight: 10, tier: 1, icon: { glyph: 'sword', color: 'steel' } }),
    'hunting-bow': item({ id: 'hunting-bow', name: 'Hunting Bow', slot: 'weapon', weaponId: 'hunting-bow', base: { stats: {} },
      ilvlMin: 1, ilvlMax: 30, weight: 10, tier: 1, icon: { glyph: 'bow', color: 'leather' } }),
    'arc-staff': item({ id: 'arc-staff', name: 'Arc Staff', slot: 'weapon', weaponId: 'arc-staff', base: { stats: {} },
      ilvlMin: 5, ilvlMax: 30, weight: 6, tier: 2, icon: { glyph: 'staff', color: 'bronze' } }),

    'wooden-buckler': item({ id: 'wooden-buckler', name: 'Wooden Buckler', slot: 'offhand', base: { stats: { armor: 4 } },
      ilvlMin: 1, ilvlMax: 30, weight: 10, tier: 1, icon: { glyph: 'shield', color: 'leather' } }),

    'leather-cap': item({ id: 'leather-cap', name: 'Leather Cap', slot: 'head', base: { stats: { armor: 3 } },
      ilvlMin: 1, ilvlMax: 15, weight: 10, tier: 1, icon: { glyph: 'cap', color: 'leather' } }),
    'iron-helm': item({ id: 'iron-helm', name: 'Iron Helm', slot: 'head', base: { stats: { armor: 7 } },
      ilvlMin: 10, ilvlMax: 30, weight: 6, tier: 2, icon: { glyph: 'helm', color: 'steel' } }),

    'leather-vest': item({ id: 'leather-vest', name: 'Leather Vest', slot: 'chest', base: { stats: { armor: 8 } },
      ilvlMin: 1, ilvlMax: 15, weight: 10, tier: 1, icon: { glyph: 'vest', color: 'leather' } }),
    'chain-mail': item({ id: 'chain-mail', name: 'Chain Mail', slot: 'chest', base: { stats: { armor: 16 } },
      ilvlMin: 10, ilvlMax: 30, weight: 6, tier: 2, icon: { glyph: 'mail', color: 'steel' } }),

    'leather-leggings': item({ id: 'leather-leggings', name: 'Leather Leggings', slot: 'legs', base: { stats: { armor: 5 } },
      ilvlMin: 1, ilvlMax: 15, weight: 10, tier: 1, icon: { glyph: 'greaves', color: 'leather' } }),
    'chain-leggings': item({ id: 'chain-leggings', name: 'Chain Leggings', slot: 'legs', base: { stats: { armor: 11 } },
      ilvlMin: 10, ilvlMax: 30, weight: 6, tier: 2, icon: { glyph: 'greaves', color: 'steel' } }),

    'leather-boots': item({ id: 'leather-boots', name: 'Leather Boots', slot: 'boots', base: { stats: { armor: 2, moveSpeed: 6 } },
      ilvlMin: 1, ilvlMax: 30, weight: 10, tier: 1, icon: { glyph: 'boots', color: 'leather' } }),

    'copper-ring': item({ id: 'copper-ring', name: 'Copper Ring', slot: 'ring', base: { stats: { resourceRegen: 0.2 } },
      ilvlMin: 1, ilvlMax: 30, weight: 10, tier: 1, icon: { glyph: 'ring', color: 'bronze' } }),
    'jade-ring': item({ id: 'jade-ring', name: 'Jade Ring', slot: 'ring', base: { stats: { maxResource: 5 } },
      ilvlMin: 10, ilvlMax: 30, weight: 6, tier: 2, icon: { glyph: 'ring', color: 'green' } }),

    'bone-amulet': item({ id: 'bone-amulet', name: 'Bone Amulet', slot: 'amulet', base: { stats: { maxHp: 5 } },
      ilvlMin: 1, ilvlMax: 30, weight: 8, tier: 1, icon: { glyph: 'amulet', color: 'bone' } }),

    'health-potion': item({ id: 'health-potion', name: 'Health Potion', slot: 'consumable', stackable: true,
      consumable: { effect: 'heal', amount: 40 }, base: { stats: {} },
      ilvlMin: 1, ilvlMax: 30, weight: 20, tier: 1, icon: { glyph: 'potion', color: 'red' } }),
    'energy-potion': item({ id: 'energy-potion', name: 'Energy Potion', slot: 'consumable', stackable: true,
      consumable: { effect: 'resource', amount: 30 }, base: { stats: {} },
      ilvlMin: 1, ilvlMax: 30, weight: 12, tier: 1, icon: { glyph: 'potion', color: 'blue' } }),
  });

  // Rarity table, lowest to highest. `rank` gives the order; `modCount` is the random modifier count range;
  // `modTierBoost` raises the modifier roll tier; `dropWeight` is the relative chance of this rarity on a roll.
  RPG.data.rarities = freeze({
    common:    { id: 'common',    name: 'Common',    rank: 0, color: 'rarity-common',    modCount: [0, 0], modTierBoost: 0, dropWeight: 600, nameStyle: 'plain', uniqueProperty: false },
    uncommon:  { id: 'uncommon',  name: 'Uncommon',  rank: 1, color: 'rarity-uncommon',  modCount: [1, 2], modTierBoost: 0, dropWeight: 260, nameStyle: 'plain', uniqueProperty: false },
    rare:      { id: 'rare',      name: 'Rare',      rank: 2, color: 'rarity-rare',      modCount: [2, 3], modTierBoost: 1, dropWeight: 100, nameStyle: 'affix', uniqueProperty: false },
    epic:      { id: 'epic',      name: 'Epic',      rank: 3, color: 'rarity-epic',      modCount: [3, 4], modTierBoost: 2, dropWeight: 30,  nameStyle: 'affix', uniqueProperty: false },
    legendary: { id: 'legendary', name: 'Legendary', rank: 4, color: 'rarity-legendary', modCount: [4, 5], modTierBoost: 3, dropWeight: 10,  nameStyle: 'unique', uniqueProperty: true },
  });

  // Modifier pool. `stat` is a StatMap key; `kind` is how it applies ('flat' adds, 'inc' adds to the % multiplier).
  // `ranges` holds one [min, max] roll per ilvl bracket, listed in the order of ILVL_BRACKET_STARTS.
  function modifier(def) {
    return Object.freeze(Object.assign({ tags: [] }, def, {
      ranges: Object.freeze(def.ranges.map(function (r, i) {
        return Object.freeze({ ilvlMin: ILVL_BRACKET_STARTS[i], min: r[0], max: r[1] });
      })),
    }));
  }

  RPG.data.modifierPool = freeze({
    dmg: modifier({ id: 'dmg', stat: 'damage', kind: 'flat', weight: 10,
      ranges: [[1, 3], [3, 6], [6, 10]], allowedSlots: ['weapon', 'ring', 'amulet'], tags: ['offense'] }),
    hp: modifier({ id: 'hp', stat: 'maxHp', kind: 'flat', weight: 10,
      ranges: [[5, 10], [12, 22], [25, 40]], allowedSlots: ['offhand', 'head', 'chest', 'legs', 'ring', 'amulet'], tags: ['defense'] }),
    armor: modifier({ id: 'armor', stat: 'armor', kind: 'flat', weight: 10,
      ranges: [[1, 3], [3, 6], [6, 10]], allowedSlots: ['offhand', 'head', 'chest', 'legs', 'boots'], tags: ['defense'] }),
    crit: modifier({ id: 'crit', stat: 'critChance', kind: 'flat', weight: 6,
      ranges: [[0.01, 0.02], [0.02, 0.03], [0.03, 0.05]], allowedSlots: ['weapon', 'ring', 'amulet'], tags: ['offense'] }),
    attackSpeed: modifier({ id: 'attackSpeed', stat: 'attackSpeed', kind: 'inc', weight: 6,
      ranges: [[0.03, 0.06], [0.05, 0.09], [0.08, 0.12]], allowedSlots: ['weapon', 'ring', 'amulet'], tags: ['offense'] }),
    moveSpeed: modifier({ id: 'moveSpeed', stat: 'moveSpeed', kind: 'flat', weight: 6,
      ranges: [[4, 8], [8, 12], [12, 16]], allowedSlots: ['boots', 'ring', 'amulet'], tags: ['mobility'] }),
    skillDmg: modifier({ id: 'skillDmg', stat: 'skillDamage', kind: 'inc', weight: 6,
      ranges: [[0.05, 0.10], [0.10, 0.18], [0.18, 0.28]], allowedSlots: ['weapon', 'offhand', 'ring', 'amulet'], tags: ['skill'] }),
    resRegen: modifier({ id: 'resRegen', stat: 'resourceRegen', kind: 'flat', weight: 6,
      ranges: [[0.3, 0.6], [0.6, 1.0], [1.0, 1.6]], allowedSlots: ['chest', 'ring', 'amulet'], tags: ['resource'] }),
    lifeSteal: modifier({ id: 'lifeSteal', stat: 'lifeSteal', kind: 'flat', weight: 3,
      ranges: [[0.005, 0.01], [0.01, 0.015], [0.015, 0.025]], allowedSlots: ['weapon', 'ring', 'amulet'], tags: ['sustain'] }),
  });
})(globalThis.RPG);

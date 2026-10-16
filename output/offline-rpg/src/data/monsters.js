/**
 * Monster definitions (docs/DATA-DEFINITIONS.md §5): seven archetypes, one per AI behaviour, plus the
 * Warden of Ash boss. Pure data: string keys reference behaviours in src/world/ai.js and boss.js, and
 * shape/color tokens are looked up by the renderer. Registered on RPG.data.monsters keyed by id.
 *
 * Boss phases: `hpBelow` is a fraction of max HP. Phases are listed in descending `hpBelow` order and the
 * active phase is the last entry whose `hpBelow` is still >= the boss's HP fraction. Attack `minPhase` is
 * the index into `phases` from which that attack may be chosen.
 */
(function (RPG) {
  'use strict';

  RPG.data = RPG.data || {};
  RPG.data.monsters = RPG.data.monsters || {};

  // Freezes a definition and everything nested in it, so no caller can mutate shared content.
  function deepFreeze(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {
      Object.values(value).forEach(deepFreeze);
      Object.freeze(value);
    }
    return value;
  }

  function define(def) {
    RPG.data.monsters[def.id] = deepFreeze(def);
  }

  define({
    id: 'husk-grunt',
    name: 'Husk Grunt',
    archetype: 'melee',
    tier: 1,
    areas: ['town', 'forest'],
    shape: 'blob',
    radius: 14,
    color: 'bone',
    outline: 'ink',
    eyes: true,
    baseStats: { hp: 30, damage: 6, speed: 60, armor: 0 },
    perLevel: { hp: 1.12, damage: 1.1 },
    ai: 'melee',
    aiParams: { aggroRange: 220, leashRange: 400, attackRange: 36, attackCooldown: 1.0, windup: 0.35 },
    attacks: [
      { id: 'husk-claw', kind: 'melee', damageMult: 1, range: 36, radius: 0, windup: 0.35, telegraph: 'cone', weight: 1 },
    ],
    xp: 40,
    gold: [1, 3],
    lootTable: { id: 'loot-melee-common', dropChance: 0.25, rolls: 1, rarityBoost: 0 },
    sfx: { hit: 'hit-soft', death: 'death-thud', attack: 'swipe-dull' },
    deathAnim: 'pop',
    spawnWeight: 10,
    groupSize: [2, 4],
    eliteAllowed: true,
  });

  define({
    id: 'skitter',
    name: 'Skitter',
    archetype: 'fast',
    tier: 1,
    areas: ['forest', 'caves'],
    shape: 'dart',
    radius: 9,
    color: 'moss',
    outline: 'ink',
    eyes: false,
    baseStats: { hp: 14, damage: 4, speed: 150, armor: 0 },
    perLevel: { hp: 1.1, damage: 1.08 },
    ai: 'flanker',
    aiParams: { aggroRange: 260, leashRange: 420, attackRange: 28, attackCooldown: 0.8, windup: 0.2, flankAngle: 1.2 },
    attacks: [
      { id: 'skitter-bite', kind: 'melee', damageMult: 1, range: 28, radius: 0, windup: 0.2, weight: 1 },
    ],
    xp: 30,
    gold: [0, 2],
    lootTable: { id: 'loot-fast-common', dropChance: 0.12, rolls: 1, rarityBoost: 0 },
    sfx: { hit: 'hit-light', death: 'death-skitter', attack: 'snap-light' },
    deathAnim: 'shatter',
    spawnWeight: 8,
    groupSize: [3, 6],
    eliteAllowed: true,
  });

  define({
    id: 'bone-archer',
    name: 'Bone Archer',
    archetype: 'ranged',
    tier: 2,
    areas: ['forest', 'caves', 'ruins'],
    shape: 'archer',
    radius: 13,
    color: 'bone',
    outline: 'ink',
    eyes: true,
    baseStats: { hp: 22, damage: 7, speed: 70, armor: 0, resists: { poison: 0.25 } },
    perLevel: { hp: 1.1, damage: 1.12 },
    ai: 'ranged',
    aiParams: { aggroRange: 320, leashRange: 480, attackRange: 260, attackCooldown: 1.8, windup: 0.6, preferredDistance: 200 },
    attacks: [
      {
        id: 'bone-arrow',
        kind: 'projectile',
        damageMult: 1,
        range: 260,
        radius: 0,
        windup: 0.6,
        telegraph: 'line',
        weight: 1,
        projectile: { speed: 260, radius: 4, lifetime: 1.5, pierce: 0, bounce: 0, homing: 0, shape: 'arrow', trail: false },
      },
    ],
    xp: 70,
    gold: [2, 5],
    lootTable: { id: 'loot-ranged-common', dropChance: 0.3, rolls: 1, rarityBoost: 0 },
    sfx: { hit: 'hit-soft', death: 'death-thud', attack: 'bow-twang' },
    deathAnim: 'dissolve',
    spawnWeight: 6,
    groupSize: [1, 2],
    eliteAllowed: true,
  });

  define({
    id: 'stone-brute',
    name: 'Stone Brute',
    archetype: 'tank',
    tier: 2,
    areas: ['caves', 'ruins'],
    shape: 'brute',
    radius: 20,
    color: 'slate',
    outline: 'ink',
    eyes: false,
    baseStats: { hp: 90, damage: 10, speed: 40, armor: 25, resists: { poison: 0.3, cold: 0.2 } },
    perLevel: { hp: 1.15, damage: 1.1 },
    ai: 'tank',
    aiParams: { aggroRange: 180, leashRange: 300, attackRange: 44, attackCooldown: 1.6, windup: 0.7 },
    attacks: [
      { id: 'stone-slam', kind: 'melee', damageMult: 1.3, range: 44, radius: 0, windup: 0.7, telegraph: 'cone', weight: 1 },
    ],
    xp: 120,
    gold: [4, 8],
    lootTable: { id: 'loot-tank-uncommon', dropChance: 0.4, rolls: 2, rarityBoost: 1 },
    sfx: { hit: 'hit-heavy', death: 'death-rumble', attack: 'slam-heavy' },
    deathAnim: 'shatter',
    spawnWeight: 4,
    groupSize: [1, 1],
    eliteAllowed: true,
  });

  define({
    id: 'cinder-bomb',
    name: 'Cinder Bomb',
    archetype: 'exploder',
    tier: 2,
    areas: ['caves', 'ruins', 'dungeon'],
    shape: 'bomb',
    radius: 11,
    color: 'ember',
    outline: 'ink',
    eyes: false,
    baseStats: { hp: 16, damage: 18, speed: 90, armor: 0 },
    perLevel: { hp: 1.1, damage: 1.1 },
    ai: 'exploder',
    aiParams: { aggroRange: 240, leashRange: 360, attackRange: 40, attackCooldown: 3.0, windup: 1.0, fuse: 1.0, blastRadius: 70 },
    attacks: [
      { id: 'cinder-detonate', kind: 'aoe', damageMult: 1.5, range: 0, radius: 70, windup: 1.0, telegraph: 'circle', weight: 1 },
    ],
    xp: 60,
    gold: [1, 4],
    lootTable: { id: 'loot-exploder-common', dropChance: 0.15, rolls: 1, rarityBoost: 0 },
    sfx: { hit: 'hit-light', death: 'death-blast', attack: 'fuse-hiss' },
    deathAnim: 'explode',
    spawnWeight: 5,
    groupSize: [1, 3],
    eliteAllowed: false,
  });

  define({
    id: 'hexcaller',
    name: 'Hexcaller',
    archetype: 'summoner',
    tier: 3,
    areas: ['caves', 'ruins', 'dungeon'],
    shape: 'caster',
    radius: 15,
    color: 'violet',
    outline: 'ink',
    eyes: true,
    baseStats: { hp: 40, damage: 5, speed: 45, armor: 5, resists: { arcane: 0.3 } },
    perLevel: { hp: 1.12, damage: 1.1 },
    ai: 'summoner',
    aiParams: {
      aggroRange: 280, leashRange: 420, attackRange: 200, attackCooldown: 2.5, windup: 0.8,
      preferredDistance: 180, summonDefId: 'skitter', summonInterval: 6, summonMax: 4, retreatBelowHp: 0.3,
    },
    attacks: [
      {
        id: 'hex-bolt',
        kind: 'projectile',
        damageMult: 0.8,
        range: 200,
        radius: 0,
        windup: 0.8,
        telegraph: 'line',
        weight: 2,
        projectile: { speed: 200, radius: 6, lifetime: 2.0, pierce: 0, bounce: 0, homing: 0.2, shape: 'orb', trail: true },
      },
      {
        id: 'call-skitters',
        kind: 'summon',
        damageMult: 0,
        range: 0,
        radius: 0,
        windup: 0.8,
        telegraph: 'ring',
        weight: 1,
        summonDefId: 'skitter',
      },
    ],
    xp: 150,
    gold: [5, 10],
    lootTable: { id: 'loot-summoner-uncommon', dropChance: 0.45, rolls: 2, rarityBoost: 1 },
    sfx: { hit: 'hit-soft', death: 'death-whisper', attack: 'hex-chant' },
    deathAnim: 'dissolve',
    spawnWeight: 3,
    groupSize: [1, 1],
    eliteAllowed: true,
  });

  define({
    id: 'warden-of-ash',
    name: 'Warden of Ash',
    archetype: 'boss',
    tier: 4,
    areas: ['arena'],
    shape: 'boss-colossus',
    radius: 42,
    color: 'ember',
    outline: 'ink',
    eyes: true,
    baseStats: { hp: 1800, damage: 14, speed: 55, armor: 40, resists: { fire: 0.5 } },
    perLevel: { hp: 1.0, damage: 1.05 },
    ai: 'boss',
    aiParams: { aggroRange: 400, leashRange: 600, attackRange: 320, attackCooldown: 2.0, windup: 1.0 },
    // Every boss attack telegraphs for at least 0.8 s, so no damage is unavoidable (enforced by the tests).
    attacks: [
      {
        id: 'ember-circles',
        kind: 'aoe',
        damageMult: 1.6,
        range: 0,
        radius: 90,
        windup: 1.2,
        telegraph: 'circle',
        weight: 3,
        minPhase: 0,
      },
      {
        id: 'ash-charge',
        kind: 'charge',
        damageMult: 1.8,
        range: 260,
        radius: 40,
        windup: 1.0,
        telegraph: 'line',
        weight: 2,
        minPhase: 1,
      },
      {
        id: 'ash-add-wave',
        kind: 'summon',
        damageMult: 0,
        range: 0,
        radius: 0,
        windup: 0.9,
        telegraph: 'ring',
        weight: 1,
        minPhase: 1,
        summonDefId: 'husk-grunt',
      },
      {
        id: 'ring-of-ash',
        kind: 'nova',
        damageMult: 1.2,
        range: 0,
        radius: 160,
        windup: 1.5,
        telegraph: 'ring',
        weight: 2,
        minPhase: 2,
      },
    ],
    phases: [
      { hpBelow: 1, attacks: ['ember-circles'], speedMult: 1 },
      { hpBelow: 0.66, attacks: ['ember-circles', 'ash-charge', 'ash-add-wave'], speedMult: 1.15, special: 'adds' },
      { hpBelow: 0.33, attacks: ['ember-circles', 'ash-charge', 'ring-of-ash'], speedMult: 1.3, special: 'enrage' },
    ],
    mechanic: {
      id: 'cinder-brand',
      desc: 'Ember circles leave burning ground for a few seconds; every third circle is brighter and briefly ' +
        'leaves the Warden vulnerable.',
      params: { emberEvery: 3, emberDuration: 4, vulnerableDuration: 2 },
    },
    arenaAreaId: 'arena',
    modifierPool: ['swift', 'fortified', 'vampiric'],
    rewards: { guaranteedRarity: 'epic', gold: [60, 90], xp: 400 },
    xp: 400,
    gold: [60, 90],
    lootTable: { id: 'loot-boss-epic', dropChance: 1, rolls: 3, rarityBoost: 2, guaranteed: [] },
    sfx: { hit: 'hit-heavy', death: 'death-boss', attack: 'roar-low' },
    deathAnim: 'explode',
    spawnWeight: 0,
    groupSize: [1, 1],
    eliteAllowed: false,
  });

  // Elite affixes (docs/DATA-DEFINITIONS.md §5). Each elite rolls one id from this table in world/encounters.js; the
  // effect numbers are read by sim/monsters.js (stage 'affixes' and the speed/vampiric hooks). `color` is a render
  // token for the affix outline and tint (render/draw-entities.js resolves it; the palette wins when it defines it).
  // Declaration order is the roll order, so reordering changes which affix a given seed picks.
  RPG.data.eliteAffixes = deepFreeze({
    fast:               { id: 'fast', name: 'Fast', color: 'affix-fast', speedMult: 1.25 },
    shielded:           { id: 'shielded', name: 'Shielded', color: 'affix-shielded', shieldFraction: 0.3 },
    vampiric:           { id: 'vampiric', name: 'Vampiric', color: 'affix-vampiric', leechFraction: 0.5 },
    'explosive-death':  { id: 'explosive-death', name: 'Explosive', color: 'affix-explosive', deathRadius: 70, deathDamageMult: 1 },
    regenerating:       { id: 'regenerating', name: 'Regenerating', color: 'affix-regenerating', regenPerSecond: 0.04 },
  });

  RPG.data.monsters = Object.freeze(RPG.data.monsters);
})(globalThis.RPG = globalThis.RPG || {});

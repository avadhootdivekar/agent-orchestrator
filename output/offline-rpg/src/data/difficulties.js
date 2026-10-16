/**
 * Difficulty presets and the custom template (DATA-DEFINITIONS §2). Pure data.
 * Registers RPG.data.difficulties keyed by id. Presets run easy -> nightmare; each multiplier moves strictly in
 * one direction across them (bossTelegraph shrinks, the rest grow). `custom` starts at the baseline and carries
 * the slider `ranges` every field is clamped to; the presets all sit inside those ranges.
 */
(function (RPG) {
  'use strict';

  RPG.data = RPG.data || {};

  const freeze = RPG.core.util.deepFreeze;

  // Numeric multiplier fields, shared by every preset and by custom.
  const MULTIPLIER_FIELDS = [
    'enemyHp', 'enemyDamage', 'enemySpeed', 'spawnDensity', 'eliteChance',
    'bossHp', 'bossTelegraph', 'lootQuality', 'xpMultiplier', 'goldMultiplier',
  ];

  function preset(def) {
    return Object.freeze(def);
  }

  RPG.data.difficulties = freeze({
    easy: preset({
      id: 'easy', name: 'Easy',
      enemyHp: 0.7, enemyDamage: 0.55, enemySpeed: 0.9, spawnDensity: 0.7, eliteChance: 0.05,
      bossHp: 0.7, bossTelegraph: 1.3, lootQuality: 0.8, xpMultiplier: 0.9, goldMultiplier: 0.9,
      playerDeathPenalty: 'none',
    }),
    normal: preset({
      id: 'normal', name: 'Normal',
      enemyHp: 1, enemyDamage: 1, enemySpeed: 1, spawnDensity: 1, eliteChance: 0.10,
      bossHp: 1, bossTelegraph: 1, lootQuality: 1, xpMultiplier: 1, goldMultiplier: 1,
      playerDeathPenalty: 'gold10',
    }),
    hard: preset({
      id: 'hard', name: 'Hard',
      enemyHp: 1.3, enemyDamage: 1.2, enemySpeed: 1.1, spawnDensity: 1.15, eliteChance: 0.16,
      bossHp: 1.25, bossTelegraph: 0.85, lootQuality: 1.25, xpMultiplier: 1.25, goldMultiplier: 1.25,
      playerDeathPenalty: 'gold25',
    }),
    nightmare: preset({
      id: 'nightmare', name: 'Nightmare',
      enemyHp: 2.0, enemyDamage: 1.7, enemySpeed: 1.2, spawnDensity: 1.45, eliteChance: 0.25,
      bossHp: 1.5, bossTelegraph: 0.7, lootQuality: 1.4, xpMultiplier: 1.6, goldMultiplier: 1.5,
      playerDeathPenalty: 'gold25',
    }),
    custom: preset({
      id: 'custom', name: 'Custom',
      enemyHp: 1, enemyDamage: 1, enemySpeed: 1, spawnDensity: 1, eliteChance: 0.10,
      bossHp: 1, bossTelegraph: 1, lootQuality: 1, xpMultiplier: 1, goldMultiplier: 1,
      playerDeathPenalty: 'gold10',
      // Slider bounds [min, max] per multiplier. The first seven follow DATA-DEFINITIONS §2; bossHp, bossTelegraph
      // and goldMultiplier are not specified there, so they use the closest analogous bounds.
      ranges: freeze({
        enemyHp: [0.25, 4], enemyDamage: [0.25, 4], enemySpeed: [0.5, 1.5], spawnDensity: [0.25, 3],
        eliteChance: [0, 0.6], bossHp: [0.25, 4], bossTelegraph: [0.5, 1.5],
        lootQuality: [0.5, 3], xpMultiplier: [0.25, 4], goldMultiplier: [0.25, 4],
      }),
    }),
  });

  RPG.data.difficultyMultiplierFields = freeze(MULTIPLIER_FIELDS.slice());
})(globalThis.RPG);

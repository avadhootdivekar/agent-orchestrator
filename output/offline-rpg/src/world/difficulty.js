/**
 * Difficulty resolution and scaling (DATA-DEFINITIONS §2). Pure functions: no DOM, no clock, no RNG.
 * Presets are read from RPG.data.difficulties at call time, so this file only needs that data loaded before use.
 *
 * resolve(idOrConfig) -> DifficultyConfig                   preset by id, or a saved/partial config, slider-clamped
 * clampCustom(config) -> DifficultyConfig                   custom config with every multiplier inside its slider range
 * scaleMonster(def, level, diff, areaLevel) -> multipliers  one monster's hp/damage/speed/xp/loot/elite/telegraph factors
 * spawnBudget(area, diff) -> int                            encounter budget for an area, scaled by spawnDensity
 */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  const CUSTOM_ID = 'custom';
  const BOSS_ARCHETYPE = 'boss';
  const DEATH_PENALTIES = ['none', 'gold10', 'gold25'];
  // Used when an area does not declare its own `baseBudget`; the area data is not part of this module.
  const DEFAULT_BASE_BUDGET = 10;
  // Enemies keep pace with the player: hp and damage gain this fraction per player level above 1 (A4.4).
  const PLAYER_LEVEL_STEP = 0.04;
  // XP and loot quality rise per area level. The def's `perLevel` rates are per area level (DATA-DEFINITIONS §5).
  const AREA_XP_STEP = 0.1;
  const AREA_LOOT_STEP = 0.05;

  function presetFor(id) {
    const presets = RPG.data.difficulties;
    if (typeof id !== 'string' || !Object.prototype.hasOwnProperty.call(presets, id)) {
      throw new Error(`unknown difficulty "${id}"`);
    }
    return presets[id];
  }

  function assertFiniteNumber(name, v) {
    if (typeof v !== 'number' || !Number.isFinite(v)) throw new Error(`${name} must be a finite number, got ${v}`);
  }

  function assertPositiveInt(name, v) {
    if (!Number.isInteger(v) || v < 1) throw new Error(`${name} must be an integer >= 1, got ${v}`);
  }

  /**
   * Builds a custom config from a partial one. Each multiplier is clamped to its slider range; a missing value takes
   * the custom baseline. An out-of-range value is clamped rather than rejected, as the sliders would do.
   */
  function clampCustom(config) {
    const src = config || {};
    const base = presetFor(CUSTOM_ID);
    const out = { id: CUSTOM_ID, name: base.name, playerDeathPenalty: base.playerDeathPenalty };
    if (src.playerDeathPenalty !== undefined) {
      if (!DEATH_PENALTIES.includes(src.playerDeathPenalty)) {
        throw new Error(`playerDeathPenalty must be one of ${DEATH_PENALTIES.join(', ')}, got ${src.playerDeathPenalty}`);
      }
      out.playerDeathPenalty = src.playerDeathPenalty;
    }
    for (const field of Object.keys(base.ranges)) {
      const [lo, hi] = base.ranges[field];
      const v = src[field] === undefined ? base[field] : src[field];
      assertFiniteNumber(field, v);
      out[field] = RPG.core.util.clamp(v, lo, hi);
    }
    return Object.freeze(out);
  }

  /**
   * A string resolves to its preset (custom is clamped). An object resolves through its own `id` (default custom):
   * its fields win over the current preset values, so a config saved in a save file keeps its numbers even if the
   * presets are later edited (DATA-DEFINITIONS §2). Unknown ids throw.
   */
  function resolve(idOrConfig) {
    if (typeof idOrConfig === 'string') {
      const preset = presetFor(idOrConfig);
      return idOrConfig === CUSTOM_ID ? clampCustom(preset) : preset;
    }
    if (idOrConfig && typeof idOrConfig === 'object') {
      const id = idOrConfig.id === undefined ? CUSTOM_ID : idOrConfig.id;
      const base = presetFor(id);
      return Object.freeze({ ...clampCustom({ ...base, ...idOrConfig }), id, name: base.name });
    }
    throw new Error(`difficulty must be an id or a config object, got ${idOrConfig}`);
  }

  /** Multiplier from a per-area-level growth rate compounded `areaLevel - 1` times (area 1 is the base). */
  function areaGrowth(rate, areaLevel) {
    return Math.pow(rate, areaLevel - 1);
  }

  /**
   * Returns multipliers for one monster, to apply to the def's base stats. `level` is the player's level and
   * `areaLevel` the area's. `hp` and `damage` compound the def's `perLevel` rates per area level and rise with
   * player level. `xp` and `lootQuality` rise per area level. Boss defs also take `bossHp` and `telegraph`.
   * `speed`, `gold` and `eliteChance` come straight from the difficulty.
   */
  function scaleMonster(def, level, diffIn, areaLevel) {
    assertPositiveInt('level', level);
    const area = areaLevel === undefined ? 1 : areaLevel;
    assertPositiveInt('areaLevel', area);
    const diff = resolve(diffIn);
    const growth = def.perLevel || {};
    const isBoss = def.archetype === BOSS_ARCHETYPE;
    const playerScale = 1 + PLAYER_LEVEL_STEP * (level - 1);
    return {
      hp: diff.enemyHp * (isBoss ? diff.bossHp : 1) * areaGrowth(growth.hp === undefined ? 1 : growth.hp, area) * playerScale,
      damage: diff.enemyDamage * areaGrowth(growth.damage === undefined ? 1 : growth.damage, area) * playerScale,
      speed: diff.enemySpeed,
      xp: diff.xpMultiplier * (1 + AREA_XP_STEP * (area - 1)),
      lootQuality: diff.lootQuality * (1 + AREA_LOOT_STEP * (area - 1)),
      gold: diff.goldMultiplier,
      eliteChance: diff.eliteChance,
      telegraph: isBoss ? diff.bossTelegraph : 1,
    };
  }

  /** Encounter budget for an area: its base budget scaled by spawnDensity, never below 1. */
  function spawnBudget(area, diffIn) {
    const diff = resolve(diffIn);
    const base = area && area.baseBudget !== undefined ? area.baseBudget : DEFAULT_BASE_BUDGET;
    assertFiniteNumber('baseBudget', base);
    return Math.max(1, Math.round(base * diff.spawnDensity));
  }

  RPG.world.difficulty = { resolve, clampCustom, scaleMonster, spawnBudget };
})(globalThis.RPG);

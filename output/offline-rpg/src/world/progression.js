/**
 * Progression: XP curve, level-ups with carry-over, per-level skill point grants and save/restore.
 * Pure functions over a plain progression object `{level, xp, skillPointsGranted, xpMultiplier, maxLevel, onLevelUp}`;
 * the only mutation is the object passed to addXp. No DOM, no clock, no RNG.
 */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  // Cost to advance from level L to L+1 is round(XP_BASE * L^XP_EXPONENT). The exponent is >1 so each level is
  // harder than the last, and the rounded cost stays strictly increasing across the whole level range.
  const XP_BASE = 100;
  const XP_EXPONENT = 1.5;
  // Matches the save schema's level range (1..30).
  const MAX_LEVEL = 30;
  const SKILL_POINTS_PER_LEVEL = 1;
  // Per-level stat growth, mirroring docs/DATA-DEFINITIONS.md. Passed to level-up hooks; applying it is the stats layer's job.
  const LEVEL_STAT_GROWTH = Object.freeze({ maxHp: 12, maxResource: 3, damage: 1.2 });

  function assertPositiveInt(name, v) {
    if (!Number.isInteger(v) || v < 1) throw new Error(`${name} must be an integer >= 1, got ${v}`);
  }

  /** XP needed to go from `level` to `level + 1`. */
  function xpToNext(level) {
    assertPositiveInt('level', level);
    return Math.round(XP_BASE * Math.pow(level, XP_EXPONENT));
  }

  /** Total XP accumulated when a character first reaches `level` (level 1 needs 0). */
  function totalXpForLevel(level) {
    assertPositiveInt('level', level);
    let total = 0;
    for (let l = 1; l < level; l++) total += xpToNext(l);
    return total;
  }

  /**
   * `xpMultiplier` comes from the difficulty preset. `maxLevel` is the cap; XP earned at the cap is discarded.
   * `onLevelUp({level, skillPoints, statGrowth})` is called once per level gained, in order.
   */
  function createProgression(options) {
    const opts = options || {};
    const maxLevel = opts.maxLevel === undefined ? MAX_LEVEL : opts.maxLevel;
    const xpMultiplier = opts.xpMultiplier === undefined ? 1 : opts.xpMultiplier;
    assertPositiveInt('maxLevel', maxLevel);
    if (!Number.isFinite(xpMultiplier) || xpMultiplier < 0) {
      throw new Error(`xpMultiplier must be a finite number >= 0, got ${xpMultiplier}`);
    }
    return {
      level: 1,
      xp: 0,
      skillPointsGranted: 0,
      xpMultiplier,
      maxLevel,
      onLevelUp: typeof opts.onLevelUp === 'function' ? opts.onLevelUp : null,
    };
  }

  /**
   * Adds XP (scaled by the multiplier, rounded down) and applies every level-up it pays for.
   * The remainder carries into the next level. Returns `{gained, levelUps}`, where `levelUps` lists each level reached.
   */
  function addXp(prog, amount) {
    if (!Number.isFinite(amount) || amount < 0) {
      throw new Error(`XP amount must be a finite number >= 0, got ${amount}`);
    }
    const gained = Math.floor(amount * prog.xpMultiplier);
    const levelUps = [];
    prog.xp += gained;
    while (prog.level < prog.maxLevel && prog.xp >= xpToNext(prog.level)) {
      prog.xp -= xpToNext(prog.level);
      prog.level += 1;
      const grant = { level: prog.level, skillPoints: SKILL_POINTS_PER_LEVEL, statGrowth: LEVEL_STAT_GROWTH };
      prog.skillPointsGranted += grant.skillPoints;
      levelUps.push(grant);
      if (prog.onLevelUp) prog.onLevelUp(grant);
    }
    if (prog.level >= prog.maxLevel) prog.xp = 0;
    return { gained, levelUps };
  }

  /** Plain-data snapshot for the save file. The multiplier, cap and hook come from the current difficulty and code. */
  function serialize(prog) {
    return { level: prog.level, xp: prog.xp, skillPointsGranted: prog.skillPointsGranted };
  }

  /**
   * Rebuilds progression from a snapshot. Throws on anything that could not have come from `serialize`,
   * so a corrupt save is rejected rather than loaded half-valid.
   */
  function restore(data, options) {
    if (!data || typeof data !== 'object') throw new Error('progression data must be an object');
    const prog = createProgression(options);
    assertPositiveInt('level', data.level);
    if (data.level > prog.maxLevel) throw new Error(`level ${data.level} exceeds cap ${prog.maxLevel}`);
    if (!Number.isInteger(data.xp) || data.xp < 0) throw new Error(`xp must be an integer >= 0, got ${data.xp}`);
    const atCap = data.level === prog.maxLevel;
    if (atCap ? data.xp !== 0 : data.xp >= xpToNext(data.level)) {
      throw new Error(`xp ${data.xp} is out of range for level ${data.level}`);
    }
    if (!Number.isInteger(data.skillPointsGranted) || data.skillPointsGranted < 0) {
      throw new Error(`skillPointsGranted must be an integer >= 0, got ${data.skillPointsGranted}`);
    }
    prog.level = data.level;
    prog.xp = data.xp;
    prog.skillPointsGranted = data.skillPointsGranted;
    return prog;
  }

  RPG.world.progression = {
    XP_BASE,
    XP_EXPONENT,
    MAX_LEVEL,
    SKILL_POINTS_PER_LEVEL,
    LEVEL_STAT_GROWTH,
    xpToNext,
    totalXpForLevel,
    createProgression,
    addXp,
    serialize,
    restore,
  };
})(globalThis.RPG = globalThis.RPG || {});

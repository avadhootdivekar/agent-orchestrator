/* World state object shape (ARCHITECTURE §5). Plain data only; later layers fill in area/player. */
(function (RPG) {
  'use strict';

  /** Minimal world skeleton: the stable fields every system can rely on. */
  function createWorldState(opts) {
    opts = opts || {};
    var seed = (opts.seed === undefined ? 1 : opts.seed) >>> 0;
    return {
      version: 1,
      seed: seed,
      rng: RPG.core.Rng.create(seed),
      lootRng: RPG.core.Rng.create(RPG.core.Rng.hashString('loot:' + seed)),
      time: 0,
      tick: 0,
      nextEntityId: 1,
      difficulty: opts.difficulty || null,
      area: null,
      player: null,
      monsters: [],
      projectiles: [],
      drops: [],
      effects: [],
      telegraphs: [],        // boss/monster ground warnings; boss.js reassigns its `effects`, so it gets this array, never world.effects
      enemyProjectiles: [],  // team-1 shots (kept apart from `projectiles`, which weapons.updateProjectiles resolves against monsters)
      progress: { bossesDefeated: {}, areasUnlocked: {}, kills: 0, deaths: 0, playtime: 0 },
      fx: { rng: RPG.core.Rng.create(RPG.core.Rng.hashString('fx:' + seed)), shake: 0, damageNumbers: [], particles: [], hurtFlash: 0, hurtFlashMax: 0 },
      grid: null
    };
  }

  /** Gameplay-relevant snapshot of the skeleton (excludes cosmetic/transient fx and grid). */
  function snapshot(world) {
    return {
      version: world.version, seed: world.seed, rngState: world.rng.getState(), time: world.time,
      tick: world.tick, nextEntityId: world.nextEntityId, progress: RPG.core.util.deepClone(world.progress)
    };
  }

  RPG.core.state = { createWorldState: createWorldState, snapshot: snapshot };
})(globalThis.RPG);

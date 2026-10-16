/**
 * Weapon definitions (DATA-DEFINITIONS.md §3): pure data, frozen, registered on RPG.data.weapons by id.
 * Three starter categories with different behaviour kinds: melee swing, ranged projectile, ground area.
 * Behaviour is implemented elsewhere (world/weapons.js); this file holds numbers and string keys only.
 */
(function (RPG) {
  'use strict';

  RPG.data = RPG.data || {};

  var weapons = {
    // Fast cone swing: short reach, small knockback, no energy cost.
    sword: {
      id: 'sword',
      name: 'Sword',
      category: 'melee',
      behavior: 'swing',
      baseDamage: [8, 12],
      damageType: 'phys',
      attackInterval: 0.5,
      windup: 0.08,
      active: 0.12,
      recover: 0.2,
      range: 56,
      arc: 1.9,
      aoeRadius: 0,
      projectile: null,
      projectileCount: 1,
      knockback: 240,
      critChanceBonus: 0.05,
      critMultBonus: 0,
      resourceCost: 0,
      moveSlowWhileAttacking: 0.2,
      status: [],
      tags: ['blade'],
      sfx: { attack: 'sword-swing', hit: 'sword-hit' },
      color: 'steel'
    },

    // Slower shot at long range: one arrow that pierces a single target, a crit bonus, and a small energy cost.
    'hunting-bow': {
      id: 'hunting-bow',
      name: 'Hunting Bow',
      category: 'ranged',
      behavior: 'projectile',
      baseDamage: [5, 8],
      damageType: 'phys',
      attackInterval: 0.8,
      windup: 0.1,
      active: 0,
      recover: 0.25,
      range: 420,
      arc: 0,
      aoeRadius: 0,
      projectile: { speed: 620, radius: 5, lifetime: 0.9, pierce: 1, bounce: 0, homing: 0, gravity: 0, shape: 'arrow', trail: false },
      projectileCount: 1,
      knockback: 90,
      critChanceBonus: 0.1,
      critMultBonus: 0.25,
      resourceCost: 2,
      moveSlowWhileAttacking: 0.4,
      status: [],
      tags: ['bow'],
      sfx: { attack: 'bow-release', hit: 'arrow-hit' },
      color: 'wood'
    },

    // Slow cast on a ground target: a lightning area that slows everything it catches, costs the most energy.
    'arc-staff': {
      id: 'arc-staff',
      name: 'Arc Staff',
      category: 'area',
      behavior: 'aoeGround',
      baseDamage: [10, 15],
      damageType: 'lightning',
      attackInterval: 1.1,
      windup: 0.3,
      active: 0.1,
      recover: 0.4,
      range: 300,
      arc: 0,
      aoeRadius: 90,
      projectile: null,
      projectileCount: 1,
      knockback: 60,
      critChanceBonus: 0,
      critMultBonus: 0,
      resourceCost: 8,
      moveSlowWhileAttacking: 0.6,
      status: [{ id: 'slow', chance: 1, duration: 2.5, strength: 0.4, stacks: 'refresh' }],
      tags: ['staff'],
      sfx: { attack: 'staff-cast', hit: 'zap-hit' },
      color: 'azure'
    }
  };

  RPG.data.weapons = RPG.core.util.deepFreeze(weapons);
})(globalThis.RPG);

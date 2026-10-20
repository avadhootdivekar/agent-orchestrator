'use strict';

// A weapon that comes out of loot.generateItem must be usable as-is: it carries a WeaponDefinition id, resolves in a
// weapon slot, and can attack once equipped. No test-side patching of weaponId.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'data/items.js', 'data/weapons.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/combat.js', 'world/weapons.js',
  'world/loot.js', 'world/equipment.js'
]);
const { StatBlock, weapons, loot, equipment } = RPG.world;
const { Rng } = RPG.core;
const DT = 1 / 60;

// Player at the origin facing +x with an empty equipment set; the test equips the looted item itself.
function makeWorld() {
  const stats = new StatBlock({ critChance: 0 });
  const player = {
    id: 1, x: 0, y: 0, vx: 0, vy: 0, r: 10, hp: stats.get('maxHp'), maxHp: stats.get('maxHp'), dead: false, flash: 0,
    stats, statBlock: stats, status: {}, cooldowns: {}, resource: 60, weaponSlots: [null, null, null], activeWeapon: 0,
    equipment: equipment.createEquipment(), attack: { state: 'idle', t: 0, dir: 0 }
  };
  return { rng: Rng.create(7), nextEntityId: 100, player, monsters: [], projectiles: [], effects: [], area: null,
    fx: { damageNumbers: [] } };
}
function addMonster(world, x, y) {
  const stats = new StatBlock({ armor: 0, critChance: 0 });
  const m = { id: world.nextEntityId++, x, y, vx: 0, vy: 0, r: 10, hp: 1000, maxHp: 1000, dead: false, flash: 0, stats,
    status: {}, cooldowns: {} };
  world.monsters.push(m);
  return m;
}
function runAttack(world) {
  for (let i = 0; i < 120; i++) {
    weapons.updateAttack(world, world.player, DT);
    weapons.updateProjectiles(world, DT);
    if (world.player.attack.state === 'idle' && i > 0) break;
  }
}

test('generated weapon items carry a weaponId that exists in RPG.data.weapons', () => {
  const rng = new Rng(21);
  for (const baseId of ['sword', 'hunting-bow', 'arc-staff']) {
    const item = loot.generateItem(rng, { ilvl: 5, rarity: 'rare', baseId });
    assert.equal(item.weaponId, baseId);
    assert.ok(RPG.data.weapons[item.weaponId], baseId + ' weaponId must be a WeaponDefinition id');
  }
});

test('non-weapon items get no weaponId', () => {
  const armor = loot.generateItem(new Rng(4), { ilvl: 5, rarity: 'rare', baseId: 'chain-mail' });
  assert.equal(Object.prototype.hasOwnProperty.call(armor, 'weaponId'), false);
});

test('a looted weapon equipped via equipment.equip can attack through weapons.tryAttack', () => {
  const w = makeWorld();
  const bow = loot.generateItem(new Rng(9), { ilvl: 3, rarity: 'uncommon', baseId: 'hunting-bow', uid: 501 });
  equipment.equip(w.player.equipment, w.player.statBlock, bow);
  w.player.weaponSlots[0] = bow;
  assert.equal(weapons.currentWeapon(w.player).def.id, 'hunting-bow');

  const m = addMonster(w, 200, 0);
  assert.equal(weapons.tryAttack(w, w.player, { x: 500, y: 0 }), true, 'a looted weapon must not be refused');
  for (let i = 0; i < 120 && w.projectiles.length === 0; i++) weapons.updateAttack(w, w.player, DT);
  assert.equal(w.projectiles.length, 1, 'the bow fires its projectile');
  for (let i = 0; i < 60 && m.hp === 1000; i++) weapons.updateProjectiles(w, DT);
  assert.ok(m.hp < 1000, 'the arrow lands on the monster');
});

test('a sword looted with only a baseId (no weaponId) still resolves to its weapon definition', () => {
  const w = makeWorld();
  const sword = loot.generateItem(new Rng(2), { ilvl: 1, rarity: 'common', baseId: 'sword' });
  delete sword.weaponId;
  w.player.weaponSlots[0] = sword;
  const m = addMonster(w, 40, 0);
  assert.equal(weapons.currentWeapon(w.player).def.id, 'sword');
  assert.equal(weapons.tryAttack(w, w.player, { x: 100, y: 0 }), true);
  runAttack(w);
  assert.ok(m.hp < 1000, 'the sword swing hits');
});

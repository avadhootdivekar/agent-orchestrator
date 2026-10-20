'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'core/rng.js', 'data/weapons.js',
  'world/stats.js', 'world/tilemap.js', 'world/collision.js', 'world/combat.js', 'world/weapons.js'
]);
const { StatBlock, weapons, tilemap } = RPG.world;
const DT = 1 / 60;

function ent(id, x, y, base, extra) {
  const stats = new StatBlock(base);
  return Object.assign({ id, x, y, vx: 0, vy: 0, r: 10, hp: stats.get('maxHp'), maxHp: stats.get('maxHp'), dead: false, flash: 0, stats, status: {}, cooldowns: {} }, extra);
}

// Hand-built world: player at the origin facing +x, three weapon slots (sword, bow, staff), no area unless asked.
function makeWorld(opts) {
  opts = opts || {};
  const player = ent(1, 0, 0, Object.assign({ critChance: 0 }, opts.stats), {
    resource: opts.resource === undefined ? 60 : opts.resource, weaponSlots: ['sword', 'hunting-bow', 'arc-staff'], activeWeapon: 0,
    attack: { state: 'idle', t: 0, dir: 0 }
  });
  const world = {
    rng: RPG.core.Rng.create(7), nextEntityId: 100, player, monsters: [], projectiles: [], effects: [], area: null,
    fx: { damageNumbers: [] }
  };
  if (opts.map) world.area = { map: opts.map };
  return world;
}
function addMonster(world, x, y, base) {
  const m = ent(world.nextEntityId++, x, y, Object.assign({ armor: 0, critChance: 0 }, base), { hp: 1000, maxHp: 1000 });
  world.monsters.push(m);
  return m;
}
// Runs updateAttack until the attack returns to idle (and the cooldown is not waited out).
function runAttack(world, steps) {
  for (let i = 0; i < (steps || 120); i++) {
    weapons.updateAttack(world, world.player, DT);
    weapons.updateProjectiles(world, DT);
    if (world.player.attack.state === 'idle' && i > 0) break;
  }
}
const lost = (m) => 1000 - m.hp;

test('swing hits only monsters inside the cone', () => {
  const w = makeWorld();
  const front = addMonster(w, 40, 0), diag = addMonster(w, 30, 30), side = addMonster(w, 0, 45);
  const behind = addMonster(w, -40, 0), far = addMonster(w, 120, 0);
  assert.equal(weapons.tryAttack(w, w.player, { x: 100, y: 0 }), true);
  assert.equal(w.player.attack.state, 'windup');
  runAttack(w);
  assert.ok(lost(front) >= 12 && lost(front) <= 16, 'sword 8-12 plus base damage 4, got ' + lost(front));
  assert.ok(lost(diag) > 0, '45deg is inside the cone');
  assert.equal(lost(side), 0, '90deg is outside the cone');
  assert.equal(lost(behind), 0);
  assert.equal(lost(far), 0);
  assert.ok(front.vx > 0, 'knocked away from the player');
  assert.equal(w.projectiles.length, 0);
});

test('damage lands at the start of the active phase, not during windup', () => {
  const w = makeWorld();
  const m = addMonster(w, 40, 0);
  weapons.tryAttack(w, w.player, { x: 1, y: 0 });
  for (let i = 0; i < 3; i++) weapons.updateAttack(w, w.player, DT);   // 0.05s < 0.08s windup
  assert.equal(lost(m), 0);
  assert.equal(w.player.attack.state, 'windup');
  for (let i = 0; i < 3; i++) weapons.updateAttack(w, w.player, DT);   // past 0.08s
  assert.equal(w.player.attack.state, 'active');
  assert.ok(lost(m) > 0);
  const once = m.hp;
  runAttack(w);
  assert.equal(m.hp, once, 'one hit per swing');
});

test('hits go through combat: armor mitigates, crit multiplies, knockback and statuses apply', () => {
  const plain = makeWorld(), armored = makeWorld(), crit = makeWorld({ stats: { critChance: 1 } });
  const a = addMonster(plain, 40, 0), b = addMonster(armored, 40, 0, { armor: 100 }), c = addMonster(crit, 40, 0);
  for (const w of [plain, armored, crit]) { weapons.tryAttack(w, w.player, { x: 1, y: 0 }); runAttack(w); }
  assert.ok(Math.abs(lost(b) - lost(a) / 2) < 1e-6, 'armor 100 halves physical damage: ' + lost(a) + ' vs ' + lost(b));
  // Same seed => same damage roll; the sword's +0.05 crit bonus and 1.5 crit multiplier make the crit exactly 1.5x.
  assert.ok(Math.abs(lost(c) - lost(a) * 1.5) < 1e-6, 'crit ' + lost(c) + ' vs ' + lost(a));
  assert.ok(a.vx > 0 && a.flash > 0);
  const staff = makeWorld(); staff.player.activeWeapon = 2;
  const s = addMonster(staff, 100, 0);
  weapons.tryAttack(staff, staff.player, { x: 100, y: 0 }); runAttack(staff);
  assert.ok(RPG.world.combat.hasStatus(s, 'slow'), 'staff applies slow');
});

test('projectile: bow spawns a projectile that travels and hits on contact', () => {
  const w = makeWorld(); w.player.activeWeapon = 1;
  const m = addMonster(w, 200, 0);
  assert.equal(weapons.tryAttack(w, w.player, { x: 500, y: 0 }), true);
  assert.equal(w.projectiles.length, 0, 'nothing spawns during windup');
  for (let i = 0; i < 8; i++) weapons.updateAttack(w, w.player, DT);
  assert.equal(w.projectiles.length, 1);
  const p = w.projectiles[0];
  assert.equal(p.vx, 620); assert.ok(Math.abs(p.vy) < 1e-9);
  const x0 = p.x;
  weapons.updateProjectiles(w, DT);
  assert.ok(p.x > x0, 'moves');
  assert.equal(lost(m), 0);
  for (let i = 0; i < 40 && lost(m) === 0; i++) weapons.updateProjectiles(w, DT);
  assert.ok(lost(m) >= 9 && lost(m) <= 21, 'arrow 5-8 plus 4 (x1.75 on a crit), got ' + lost(m));
  assert.equal(w.projectiles[0].pierceLeft, 0, 'the one allowed pierce is used up; the next hit would consume it');
  assert.deepEqual(Array.from(w.projectiles[0].hitIds), [m.id]);
});

test('projectile pierces one extra target, stops at a wall and expires at max range', () => {
  const w = makeWorld(); w.player.activeWeapon = 1;
  const m1 = addMonster(w, 100, 0), m2 = addMonster(w, 160, 0), m3 = addMonster(w, 220, 0);
  weapons.tryAttack(w, w.player, { x: 1, y: 0 }); runAttack(w, 20);
  for (let i = 0; i < 90; i++) weapons.updateProjectiles(w, DT);
  assert.ok(lost(m1) > 0 && lost(m2) > 0, 'pierce 1 passes through the first target');
  assert.equal(lost(m3), 0, 'stopped by the second');

  const map = tilemap.create(20, 5, 32);
  for (let y = 0; y < 5; y++) tilemap.setTile(map, 4, y, tilemap.TILE.WALL);
  const w2 = makeWorld({ map }); w2.player.activeWeapon = 1; w2.player.x = 40; w2.player.y = 80;
  const behindWall = addMonster(w2, 300, 80);
  weapons.tryAttack(w2, w2.player, { x: 500, y: 80 }); runAttack(w2, 20);
  for (let i = 0; i < 60; i++) weapons.updateProjectiles(w2, DT);
  assert.equal(w2.projectiles.length, 0);
  assert.equal(lost(behindWall), 0, 'wall blocks the arrow');

  const w3 = makeWorld(); w3.player.activeWeapon = 1;
  weapons.tryAttack(w3, w3.player, { x: 1, y: 0 }); runAttack(w3, 20);
  assert.equal(w3.projectiles.length, 1);
  for (let i = 0; i < 60; i++) weapons.updateProjectiles(w3, DT);
  assert.equal(w3.projectiles.length, 0, 'expired');
});

test('spread behaviour fans projectileCount projectiles across the arc', () => {
  const w = makeWorld();
  const def = { id: 'shotgun', behavior: 'spread', baseDamage: [1, 1], damageType: 'phys', attackInterval: 1, windup: 0, active: 0, recover: 0,
    range: 300, arc: 0.6, projectile: { speed: 400, radius: 4, lifetime: 1, pierce: 0 }, projectileCount: 3, knockback: 0, resourceCost: 0, status: [] };
  const original = RPG.data.weapons;
  RPG.data.weapons = Object.assign({}, original, { shotgun: def });   // the shipped table is frozen
  w.player.weaponSlots[0] = 'shotgun';
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), true);
  weapons.updateAttack(w, w.player, DT);
  assert.equal(w.projectiles.length, 3);
  const angles = w.projectiles.map((p) => p.angle).sort((a, b) => a - b);
  assert.ok(Math.abs(angles[0] + 0.3) < 1e-9 && Math.abs(angles[1]) < 1e-9 && Math.abs(angles[2] - 0.3) < 1e-9);
  RPG.data.weapons = original;
});

test('aoeGround: telegraphs, then hits every monster in the radius after the delay', () => {
  const w = makeWorld(); w.player.activeWeapon = 2;
  const center = addMonster(w, 200, 0), edge = addMonster(w, 200, 80), outside = addMonster(w, 200, 130), nearPlayer = addMonster(w, 20, 0);
  assert.equal(weapons.tryAttack(w, w.player, { x: 200, y: 0 }), true);
  assert.equal(w.effects.length, 1);
  assert.equal(w.effects[0].kind, 'telegraph');
  assert.equal(w.effects[0].radius, 90);
  assert.equal(lost(center), 0, 'no damage on the cast frame');
  for (let i = 0; i < 10; i++) weapons.updateAttack(w, w.player, DT);   // 0.167s < 0.3s windup
  assert.equal(lost(center), 0, 'still winding up');
  runAttack(w);
  assert.ok(lost(center) >= 14 && lost(edge) >= 14, 'lightning 10-15 plus 4');
  assert.equal(lost(outside), 0);
  assert.equal(lost(nearPlayer), 0, 'only the ground target is hit, not the caster surroundings');
  assert.ok(RPG.world.combat.hasStatus(center, 'slow') && RPG.world.combat.hasStatus(edge, 'slow'));
  assert.ok(w.effects.some((e) => e.kind === 'aoe' && e.hits.length === 2));
});

test('aoeGround target is clamped to the weapon range', () => {
  const w = makeWorld(); w.player.activeWeapon = 2;
  const m = addMonster(w, 300, 0);   // at max range 300
  weapons.tryAttack(w, w.player, { x: 900, y: 0 }); runAttack(w);
  assert.ok(lost(m) > 0);
  const e = w.effects.find((x) => x.kind === 'aoe');
  assert.equal(e.x, 300);
});

test('cooldown gating: a second attack is refused until the interval elapses', () => {
  const w = makeWorld();
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), true);
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), false, 'mid-attack');
  runAttack(w);
  assert.equal(w.player.attack.state, 'idle');
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), false, 'idle but 0.5s interval not over (0.4s anim)');
  for (let i = 0; i < 10; i++) weapons.updateAttack(w, w.player, DT);
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), true);
});

test('attackInterval divides by the attackSpeed stat and speeds the phases up', () => {
  const slow = makeWorld(), fast = makeWorld({ stats: { attackSpeed: 2 } });
  assert.equal(weapons.attackInterval(slow.player), 0.5);
  assert.equal(weapons.attackInterval(fast.player), 0.25);
  addMonster(slow, 40, 0); addMonster(fast, 40, 0);
  for (const w of [slow, fast]) { weapons.tryAttack(w, w.player, { x: 1, y: 0 }); }
  for (let i = 0; i < 3; i++) { weapons.updateAttack(slow, slow.player, DT); weapons.updateAttack(fast, fast.player, DT); }
  assert.equal(slow.player.attack.state, 'windup');
  assert.equal(fast.player.attack.state, 'active', 'windup 0.08 / 2 = 0.04s');
});

test('resource gating: costed weapons need enough resource and spend it; the sword is free', () => {
  const w = makeWorld({ resource: 1 });
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), true, 'sword costs 0');
  assert.equal(w.player.resource, 1);
  runAttack(w); w.player.attack.cd = 0;
  weapons.switchWeapon(w, w.player, 1);
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), false, 'bow needs 2');
  assert.equal(w.player.attack.state, 'idle');
  assert.equal(w.player.resource, 1, 'a refused attack spends nothing');
  w.player.resource = 2;
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), true);
  assert.equal(w.player.resource, 0);
  runAttack(w); w.player.attack.cd = 0;
  weapons.switchWeapon(w, w.player, 2);
  w.player.resource = 7;
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), false, 'staff needs 8');
  w.player.resource = 8;
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), true);
  assert.equal(w.player.resource, 0);
});

test('switching weapons changes the behaviour; three weapons give three different outcomes', () => {
  const w = makeWorld();
  const m = addMonster(w, 60, 0);
  assert.equal(weapons.currentWeapon(w.player).def.id, 'sword');
  weapons.tryAttack(w, w.player, { x: 1, y: 0 }); runAttack(w);
  const sword = { projectiles: w.projectiles.length, aoe: w.effects.filter((e) => e.kind === 'aoe').length, dmg: lost(m), slow: RPG.world.combat.hasStatus(m, 'slow'),
    tag: w.effects.map((e) => e.kind).join() };
  w.player.attack.cd = 0; m.hp = 1000; w.effects.length = 0;

  assert.equal(weapons.switchWeapon(w, w.player, 1), true);
  assert.equal(weapons.currentWeapon(w.player).def.behavior, 'projectile');
  weapons.tryAttack(w, w.player, { x: 1, y: 0 });
  for (let i = 0; i < 8; i++) weapons.updateAttack(w, w.player, DT);
  const bow = { projectiles: w.projectiles.length, tag: w.effects.map((e) => e.kind).join(), dmgAtCast: lost(m) };
  runAttack(w); for (let i = 0; i < 20; i++) weapons.updateProjectiles(w, DT);
  w.player.attack.cd = 0; m.hp = 1000; w.effects.length = 0; w.projectiles.length = 0;

  assert.equal(weapons.switchWeapon(w, w.player, 2), true);
  weapons.tryAttack(w, w.player, { x: 60, y: 0 }); runAttack(w);
  const staff = { projectiles: w.projectiles.length, tag: w.effects.map((e) => e.kind).join(), slow: RPG.world.combat.hasStatus(m, 'slow'), dmg: lost(m) };

  assert.deepEqual([sword.projectiles, sword.tag, sword.slow], [0, 'swing', false]);
  assert.deepEqual([bow.projectiles, bow.tag, bow.dmgAtCast], [1, '', 0]);
  assert.deepEqual([staff.projectiles, staff.tag, staff.slow], [0, 'telegraph,aoe', true]);
  assert.ok(sword.dmg > 0 && staff.dmg > 0);
});

test('switchWeapon: absolute index, relative delta wrapping over empty slots, refused mid-attack', () => {
  const w = makeWorld();
  assert.equal(weapons.switchWeapon(w, w.player, 0), false, 'already active');
  assert.equal(weapons.switchWeapon(w, w.player, 5), false);
  assert.equal(weapons.switchWeapon(w, w.player, { delta: 1 }), true);
  assert.equal(w.player.activeWeapon, 1);
  assert.equal(weapons.switchWeapon(w, w.player, -1), true);
  assert.equal(w.player.activeWeapon, 0);
  assert.equal(weapons.switchWeapon(w, w.player, -1), true);
  assert.equal(w.player.activeWeapon, 2, 'wraps');
  w.player.weaponSlots[0] = null;
  assert.equal(weapons.switchWeapon(w, w.player, 0), false, 'empty slot');
  assert.equal(weapons.switchWeapon(w, w.player, { delta: 1 }), true);
  assert.equal(w.player.activeWeapon, 1, 'skips the empty slot');
  weapons.tryAttack(w, w.player, { x: 1, y: 0 });
  assert.equal(weapons.switchWeapon(w, w.player, 2), false, 'mid-attack');
});

test('currentWeapon resolves ItemInstance slots and refuses to attack with no weapon', () => {
  const w = makeWorld();
  w.player.weaponSlots = [{ defId: 'hunting-bow', ilvl: 1 }, null, null];
  const cur = weapons.currentWeapon(w.player);
  assert.equal(cur.def.id, 'hunting-bow');
  assert.equal(cur.item.defId, 'hunting-bow');
  w.player.weaponSlots = [null, null, null];
  assert.equal(weapons.currentWeapon(w.player).def, null);
  assert.equal(weapons.tryAttack(w, w.player, { x: 1, y: 0 }), false);
});

test('moveFactor slows the player only while attacking', () => {
  const w = makeWorld();
  assert.equal(weapons.moveFactor(w.player), 1);
  weapons.tryAttack(w, w.player, { x: 1, y: 0 });
  assert.ok(Math.abs(weapons.moveFactor(w.player) - 0.8) < 1e-9);
});

test('dead monsters are ignored', () => {
  const w = makeWorld();
  const m = addMonster(w, 40, 0); m.dead = true;
  weapons.tryAttack(w, w.player, { x: 1, y: 0 }); runAttack(w);
  assert.equal(lost(m), 0);
});

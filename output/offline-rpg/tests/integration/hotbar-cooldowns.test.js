'use strict';
// Regression (w11-01): hotbar cooldowns were read from player.skills.cooldowns, which the sim never writes, so a cast
// skill never showed seconds or a sweep. These drive a REAL cast through the sim and feed the HUD model the live state.

const test = require('node:test');
const assert = require('node:assert/strict');
const { makeGame, DT } = require('./helpers.js');

const idle = () => ({ move: { x: 0, y: 0 }, aim: { x: 500, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });
const castSlot0 = () => ({ move: { x: 0, y: 0 }, aim: { x: 500, y: 0 }, attack: false, skills: [true], dash: false, toggles: {} });

async function castGame() {
  const g = await makeGame();
  g.newGame('hotbar-cd');
  const p = g.game.world.player;
  const SK = g.RPG.world.skills;
  p.skillPoints = p.skills.points = 5;
  // Pick any learnable active skill so the test follows the data rather than a hard-coded id.
  const id = Object.keys(g.RPG.data.skills).find((k) => g.RPG.data.skills[k].type === 'active' && g.RPG.data.skills[k].cooldown > 1);
  p.skills.learned[id] = 1;
  p.skills.bar[0] = id;
  p.resource = p.stats && p.stats.get ? p.stats.get('maxResource') : 100;
  return { g, p, id, SK, def: g.RPG.data.skills[id] };
}

test('a real skill cast shows remaining seconds and a partial sweep on its hotbar slot', async () => {
  const { g, p, id, def } = await castGame();
  g.drive(1, castSlot0);
  assert.ok(p.cooldowns['skill:' + id] > 0, 'the sim did not start a cooldown (cast failed)');
  g.drive(30, idle); // half a second of cooldown elapses

  const hud = g.RPG.render.hud;
  const base = {};
  base[id] = def.cooldown;
  const m = hud.buildHudModel(g.game.world, { skillCooldowns: base });
  const slot = m.skills[0];
  assert.equal(slot.skillId, id);
  assert.ok(slot.cooldown > 0 && slot.cooldown < def.cooldown, 'slot shows no remaining time: ' + slot.cooldown);
  assert.ok(slot.cooldownFrac > 0 && slot.cooldownFrac < 1, 'no partial sweep: ' + slot.cooldownFrac);
  assert.ok(Math.abs(slot.cooldown - p.cooldowns['skill:' + id]) < 1e-9, 'HUD disagrees with the sim cooldown');
});

test('base cooldowns taken from the data defs give a near-full sweep right after a real cast', async () => {
  const { g, p, id, def } = await castGame();
  g.drive(1, castSlot0);
  // Same derivation core/game.js hudInputs uses: base cooldown per skill id from RPG.data.skills.
  const base = {};
  for (const k of Object.keys(g.RPG.data.skills)) base[k] = g.RPG.data.skills[k].cooldown;
  const m = g.RPG.render.hud.buildHudModel(g.game.world, { skillCooldowns: base });
  const remaining = p.cooldowns['skill:' + id];
  assert.ok(m.skills[0].cooldown > 0);
  assert.ok(m.skills[0].cooldownFrac > 0.9 && m.skills[0].cooldownFrac <= 1, 'fresh cast should be almost fully swept');
  assert.ok(remaining <= def.cooldown);
});

test('the slot clears once the cooldown has elapsed', async () => {
  const { g, id, def } = await castGame();
  g.drive(1, castSlot0);
  g.drive(Math.ceil((def.cooldown + 0.5) / DT), idle);
  const m = g.RPG.render.hud.buildHudModel(g.game.world, { skillCooldowns: { [id]: def.cooldown } });
  assert.equal(m.skills[0].cooldown, 0);
  assert.equal(m.skills[0].cooldownFrac, 0);
});

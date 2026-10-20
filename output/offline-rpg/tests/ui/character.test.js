'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'core/rng.js', 'core/entities.js', 'data/items.js',
  'world/stats.js', 'world/equipment.js', 'sim/player.js', 'ui/screens.js', 'ui/character.js']);
const ch = RPG.ui.character;
const plain = (v) => JSON.parse(JSON.stringify(v));

let uid = 1;
const item = (baseId) => ({ baseId, ilvl: 1, rarity: 'common', mods: [], name: baseId, uid: uid++ });
const newPlayer = () => RPG.sim.player.create({ nextEntityId: 1 }, 0, 0);

const recordingCtx = () => {
  const texts = [];
  const ctx = new Proxy({}, {
    get: (_t, key) => (key === 'fillText' ? (s) => texts.push(String(s)) : () => {}),
    set: () => true,
  });
  return { ctx, texts };
};

test('the sheet lists every stat in the StatBlock, and the resistances in their own section', () => {
  const p = newPlayer();
  const data = ch.sheet(p);
  const listed = [...data.stats, ...data.resistances].map((r) => r.stat).sort();
  assert.deepEqual(listed, Object.keys(p.statBlock.snapshot()).sort());
  assert.deepEqual(data.resistances.map((r) => r.stat), ch.RESIST_STATS.slice().sort());
  assert.ok(data.stats.every((r) => ch.RESIST_STATS.indexOf(r.stat) === -1));
});

test('sheet values match the derived StatBlock and are formatted for display', () => {
  const p = newPlayer();
  const data = ch.sheet(p);
  const maxHp = data.stats.find((r) => r.stat === 'maxHp');
  assert.equal(maxHp.label, 'Max Hp');
  assert.equal(maxHp.value, p.statBlock.get('maxHp'));
  const crit = data.stats.find((r) => r.stat === 'critChance');
  assert.equal(crit.text, '5%');
});

test('resistances show the value of gear and modifiers as percentages', () => {
  const p = newPlayer();
  p.statBlock.addModifier({ source: 'test', stat: 'resFire', kind: 'flat', value: 0.25 });
  const fire = ch.sheet(p).resistances.find((r) => r.stat === 'resFire');
  assert.equal(fire.label, 'Fire');
  assert.equal(fire.text, '25%');
});

test('the sheet shows the equipped item in each slot, and a dash for an empty slot', () => {
  const p = newPlayer();
  RPG.world.equipment.equip(
    { slots: p.equipment }, p.statBlock, item('leather-vest'));
  const chest = ch.sheet(p).equipment.find((e) => e.slot === 'chest');
  assert.equal(chest.item, 'leather-vest');
  assert.equal(ch.sheet(p).equipment.find((e) => e.slot === 'head').item, null);
});

test('drawing the sheet paints the stats and resistances, and does not change the player', () => {
  const p = newPlayer();
  const before = plain(p);
  const { ctx, texts } = recordingCtx();
  ch.draw(ctx, p, { x: 0, y: 0 });
  assert.ok(texts.includes('Character'));
  assert.ok(texts.includes('Resistances'));
  assert.ok(texts.some((t) => t.startsWith('Max Hp: ')));
  assert.ok(texts.some((t) => t.startsWith('Fire: ')));
  assert.deepEqual(plain(p), before);
});

test('the character screen registers with the screen manager and opens as a modal', () => {
  const p = newPlayer();
  const mgr = RPG.ui.screens.create({ bindings: {} });
  mgr.register(ch.createScreen(p, { x: 0, y: 0 }));
  mgr.open('character');
  assert.equal(mgr.top(), 'character');
  assert.equal(mgr.paused(), true);
  const { ctx, texts } = recordingCtx();
  mgr.draw(ctx);
  assert.ok(texts.includes('Character'));
});

test('character sheet fits 640x480 below the HUD bars, with no line overlapping another', () => {
  const p = newPlayer();
  const size = ch.panelSize(p);
  assert.ok(ch.HUD_INSET + size.h <= 480, `height ${ch.HUD_INSET + size.h} fits 480`);
  assert.ok(size.w <= 640);
  const calls = [];
  const ctx = new Proxy({}, { get: (_t, k) => (k === 'fillText' ? (s, x, y) => calls.push({ s: String(s), x, y }) : () => {}), set: () => true });
  ch.createScreen(p, { x: 0, y: 0 }).draw(ctx);
  const keys = calls.map((c) => c.x + ',' + c.y);
  assert.equal(new Set(keys).size, keys.length, 'no two text lines share a position');
  assert.ok(Math.min(...calls.map((c) => c.y)) >= 74, 'text starts below the HUD bars');
  assert.ok(Math.max(...calls.map((c) => c.y)) <= 480);
});

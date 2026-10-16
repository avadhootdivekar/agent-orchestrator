'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'core/rng.js', 'core/entities.js', 'data/items.js',
  'world/stats.js', 'world/loot.js', 'world/equipment.js', 'sim/player.js', 'ui/inventory.js']);
const { StatBlock } = RPG.world;
const inv = RPG.ui.inventory;
const plain = (v) => JSON.parse(JSON.stringify(v));

// Hand-built items with exact stats (no seeded rolls), so every expected number here is written out by hand.
let uid = 1;
const item = (baseId, mods = []) => ({ baseId, ilvl: 1, rarity: 'common', mods, name: baseId, uid: uid++ });

const newPlayer = () => RPG.sim.player.create({ nextEntityId: 1 }, 0, 0);
const fill = (p, n) => { for (let i = 0; i < n; i++) inv.pickup(p, item('health-potion')); };

test('equip changes player stats and unequip restores them', () => {
  const p = newPlayer();
  const before = plain(p.statBlock.snapshot());
  const vest = item('leather-vest');
  assert.equal(inv.pickup(p, vest), 0);

  const r = inv.equipFromSlot(p, 0);
  assert.equal(r.ok, true);
  assert.equal(r.displaced, null);
  assert.equal(p.inventory[0], null);
  assert.equal(p.equipment.chest, vest);
  assert.equal(p.stats.armor, before.armor + 8);

  const back = inv.unequipToInventory(p, 'chest');
  assert.equal(back.ok, true);
  assert.equal(p.inventory[back.index], vest);
  assert.equal(p.equipment.chest, undefined);
  assert.deepEqual(plain(p.statBlock.snapshot()), before);
  assert.equal(p.stats.armor, new StatBlock().get('armor'));
});

test('equipping over an item puts the displaced item into the same inventory slot', () => {
  const p = newPlayer();
  const low = item('leather-vest');
  const high = item('chain-mail');
  inv.pickup(p, low);
  inv.equipFromSlot(p, 0);
  inv.pickup(p, high); // lands in slot 0, which the equip just emptied
  assert.equal(p.inventory[0], high);

  const r = inv.equipFromSlot(p, 0);
  assert.equal(r.displaced, low);
  assert.equal(p.inventory[0], low);
  assert.equal(p.equipment.chest, high);
  assert.equal(p.stats.armor, 21); // base armor 5 + chain mail 16
});

test('equipping a non-equippable item is refused and changes nothing', () => {
  const p = newPlayer();
  inv.pickup(p, item('health-potion'));
  assert.deepEqual(plain(inv.equipFromSlot(p, 0)), { ok: false, reason: 'not-equippable' });
  assert.equal(p.inventory[0].baseId, 'health-potion');
  assert.deepEqual(plain(inv.equipFromSlot(p, 5)), { ok: false, reason: 'empty' });
});

test('drop removes the item from its slot and hands it back', () => {
  const p = newPlayer();
  const potion = item('health-potion');
  inv.pickup(p, potion);
  assert.equal(inv.dropFromSlot(p, 0), potion);
  assert.equal(p.inventory[0], null);
  assert.equal(inv.dropFromSlot(p, 0), null);
});

test('unequip is refused when the inventory is full and the item stays equipped', () => {
  const p = newPlayer();
  const vest = item('leather-vest');
  inv.pickup(p, vest);
  inv.equipFromSlot(p, 0);
  fill(p, p.inventory.length);
  const armor = p.stats.armor;
  assert.deepEqual(plain(inv.unequipToInventory(p, 'chest')), { ok: false, reason: 'inventory-full' });
  assert.equal(p.equipment.chest, vest);
  assert.equal(p.stats.armor, armor);
});

test('comparison deltas: only changed stats are listed, signed and coloured by direction', () => {
  const p = newPlayer();
  inv.pickup(p, item('leather-vest'));
  inv.equipFromSlot(p, 0);
  // A flat +0.1 crit chance mod: 0.05 -> 0.15, shown as a percentage.
  inv.pickup(p, item('chain-mail', [{ stat: 'critChance', kind: 'flat', value: 0.1 }]));

  const cmp = inv.compareSlot(p, 0);
  assert.equal(cmp.slot, 'chest');
  assert.equal(cmp.current.baseId, 'leather-vest');
  assert.equal(cmp.deltas.armor.diff, 8);
  assert.ok(Math.abs(cmp.deltas.critChance.diff - 0.1) < 1e-9);
  assert.equal(p.inventory[0].baseId, 'chain-mail', 'comparing must not equip or move the item');

  const lines = inv.formatDeltas(cmp);
  assert.deepEqual(plain(lines.map((l) => l.stat)), ['armor', 'critChance']);
  assert.deepEqual(plain(lines[0]), { stat: 'armor', label: 'Armor', diff: 8, text: '+8 Armor', tone: 'up' });
  assert.equal(lines[1].label, 'Crit chance');
  assert.equal(lines[1].text, '+10% Crit chance');
  assert.equal(lines[1].tone, 'up');
});

test('comparison shows a red downgrade when the candidate is worse', () => {
  const p = newPlayer();
  inv.pickup(p, item('chain-mail'));
  inv.equipFromSlot(p, 0);
  inv.pickup(p, item('leather-vest'));
  const lines = inv.formatDeltas(inv.compareSlot(p, 0));
  assert.deepEqual(plain(lines), [{ stat: 'armor', label: 'Armor', diff: -8, text: '-8 Armor', tone: 'down' }]);
});

test('comparison is null for an empty or non-equippable slot', () => {
  const p = newPlayer();
  assert.equal(inv.compareSlot(p, 0), null);
  inv.pickup(p, item('health-potion'));
  assert.equal(inv.compareSlot(p, 0), null);
  assert.deepEqual(plain(inv.formatDeltas(null)), []);
});

test('a full inventory refuses pickup and leaves the inventory untouched', () => {
  const p = newPlayer();
  fill(p, p.inventory.length);
  const snapshot = p.inventory.slice();
  assert.equal(inv.pickup(p, item('leather-vest')), -1);
  assert.deepEqual(p.inventory, snapshot);
});

test('pickup takes the first free slot', () => {
  const p = newPlayer();
  fill(p, 3);
  inv.dropFromSlot(p, 1);
  assert.equal(inv.pickup(p, item('leather-vest')), 1);
  assert.equal(inv.pickup(p, null), -1);
});

test('hit-test maps a pixel inside a slot to that slot index', () => {
  const x = 100, y = 50;
  for (const index of [0, 5, 6, 13, 23]) {
    const r = inv.slotRect(index, x, y);
    assert.equal(inv.hitTest(r.x + 1, r.y + 1, x, y, 24), index);
    assert.equal(inv.hitTest(r.x + r.w - 1, r.y + r.h - 1, x, y, 24), index);
  }
});

test('hit-test returns -1 for padding, gaps and slots past the inventory size', () => {
  const x = 0, y = 0;
  const r0 = inv.slotRect(0, x, y);
  assert.equal(inv.hitTest(r0.x - 1, r0.y + 1, x, y, 24), -1); // left padding
  assert.equal(inv.hitTest(r0.x + r0.w + 1, r0.y + 1, x, y, 24), -1); // in the gap between columns
  assert.equal(inv.hitTest(r0.x + 1, r0.y + r0.h + 1, x, y, 24), -1); // in the gap between rows
  const last = inv.slotRect(23, x, y);
  assert.equal(inv.hitTest(last.x + 1, last.y + 1, x, y, 20), -1); // index 23 is past a 20-slot inventory
});

test('cursor moves within the grid and stops at the edges', () => {
  assert.equal(inv.moveCursor(0, -1, 0, 24), 0);
  assert.equal(inv.moveCursor(0, 0, -1, 24), 0);
  assert.equal(inv.moveCursor(5, 1, 0, 24), 5); // last column does not wrap to the next row
  assert.equal(inv.moveCursor(0, 1, 0, 24), 1);
  assert.equal(inv.moveCursor(0, 0, 1, 24), 6);
  assert.equal(inv.moveCursor(18, 0, 1, 24), 18); // row 3 is the last row
  assert.equal(inv.moveCursor(20, 0, 1, 22), 20); // would land on slot 26, past the 22-slot inventory
});

test('the screen definition moves its cursor and runs equip/unequip/drop from key actions', () => {
  const p = newPlayer();
  inv.pickup(p, item('leather-vest'));
  inv.pickup(p, item('chain-mail'));
  const screen = inv.createScreen(p, { x: 0, y: 0 });
  assert.equal(screen.id, 'inventory');
  assert.equal(typeof screen.draw, 'function');

  // Cursor on slot 1 (chain mail): equip it into an empty chest slot.
  screen.onKey('right');
  assert.deepEqual(plain(screen.onKey('equip')), { ok: true, displaced: null });
  assert.equal(p.equipment.chest.baseId, 'chain-mail');
  assert.equal(p.inventory[1], null);

  // Back to slot 0 (leather vest): equipping it swaps chain mail out into slot 0.
  screen.onKey('left');
  const swap = screen.onKey('equip');
  assert.equal(swap.ok, true);
  assert.equal(swap.displaced.baseId, 'chain-mail');
  assert.equal(p.equipment.chest.baseId, 'leather-vest');
  assert.equal(p.inventory[0].baseId, 'chain-mail');

  // Unequip the chest into the first free slot (slot 1), then drop the cursor's slot 0.
  assert.deepEqual(plain(screen.onKey('unequip', 'chest')), { ok: true, index: 1 });
  assert.equal(screen.onKey('drop').item.baseId, 'chain-mail');
  assert.equal(p.inventory[0], null);
  assert.equal(p.inventory[1].baseId, 'leather-vest');
});

// ---- layout regressions (w12-01): equipment slots visible, nothing overlapping the HUD or itself ----

const rectsOverlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test('inventory panel shows every equipment slot, inside the panel, clear of the bag grid, at 640x480 and 1280x720', () => {
  for (const [vw, vh] of [[640, 480], [1280, 720]]) {
    const p = newPlayer();
    const y0 = inv.HUD_INSET;
    const panel = inv.panelRect(p, 0, y0);
    assert.ok(panel.y + panel.h <= vh, `panel bottom ${panel.y + panel.h} fits ${vw}x${vh}`);
    assert.ok(panel.x + panel.w <= vw);
    const bagRows = Math.ceil(p.inventory.length / inv.GRID_COLS);
    const bagBottom = inv.slotRect((bagRows - 1) * inv.GRID_COLS, 0, y0 + 24).y + inv.SLOT_SIZE;
    const seen = [];
    for (const slot of RPG.data.itemSlots) {
      const r = inv.equipSlotRect(p, slot, 0, y0);
      assert.ok(r.y >= bagBottom, `${slot} below the bag grid`);
      assert.ok(r.x >= panel.x && r.x + r.w <= panel.x + panel.w && r.y + r.h <= panel.y + panel.h, `${slot} inside the panel`);
      seen.forEach((o) => assert.equal(rectsOverlap(r, o), false, `${slot} overlaps another equipment slot`));
      seen.push(r);
    }
  }
});

test('panel starts below the HUD bars so the HP/resource/XP lines stay visible', () => {
  const p = newPlayer();
  const calls = [];
  const ctx = new Proxy({}, { get: (_t, k) => (k === 'fillRect' ? (...a) => calls.push(a) : () => {}), set: () => true });
  inv.createScreen(p, { x: 0, y: 0 }).draw(ctx);
  assert.ok(calls.length > 0);
  assert.ok(Math.min(...calls.map((c) => c[1])) >= 74, 'no fill above the bottom of the XP bar (y 74)');
});

test('after equipping loot the item is drawn in its equipment slot, and clicking the slot unequips it', () => {
  const p = newPlayer();
  inv.pickup(p, item('leather-vest'));
  const screen = inv.createScreen(p, { x: 0, y: 0 });
  screen.onKey('equip');
  const texts = [];
  const ctx = new Proxy({}, { get: (_t, k) => (k === 'fillText' ? (s, x, y) => texts.push({ s: String(s), x, y }) : () => {}), set: () => true });
  screen.draw(ctx);
  const r = inv.equipSlotRect(p, 'chest', 0, inv.HUD_INSET);
  const drawn = texts.find((t) => t.s === 'leather-' && t.x >= r.x && t.x < r.x + r.w && t.y >= r.y && t.y < r.y + r.h);
  assert.ok(drawn, 'equipped item name drawn inside the chest slot');
  assert.ok(texts.some((t) => t.s === 'Equipment'));
  assert.equal(screen.onPointer(r.x + 5, r.y + 5), true);
  assert.equal(p.equipment.chest, undefined);
  assert.equal(p.inventory[0].baseId, 'leather-vest');
});

test('item names wrap onto up to three short lines that fit a cell, ending in an ellipsis when too long', () => {
  assert.deepEqual(plain(inv.nameLines('Sword')), ['Sword']);
  assert.deepEqual(plain(inv.nameLines('Iron Sword')), ['Iron', 'Sword']);
  assert.deepEqual(plain(inv.nameLines('Leather Vest of Might')), ['Leather', 'Vest of', 'Might']);
  const long = inv.nameLines('Extraordinarily Long Unbroken Name Here');
  assert.equal(long.length, inv.NAME_LINES);
  assert.ok(long.every((l) => l.length <= inv.NAME_CHARS));
  assert.ok(long[long.length - 1].endsWith('…'));
});

test('bag and equipment cells draw the readable item name, not a 6/8-character cut', () => {
  const p = newPlayer();
  const vest = item('leather-vest');
  vest.name = 'Gilded Leather Vest';
  inv.pickup(p, vest);
  inv.equipFromSlot(p, 0);
  const second = item('leather-vest');
  second.name = 'Gilded Leather Vest';
  inv.pickup(p, second);
  const texts = [];
  const ctx = new Proxy({}, { get: (_t, k) => (k === 'fillText' ? (s) => texts.push(String(s)) : () => {}), set: () => true });
  inv.createScreen(p, { x: 0, y: 0 }).draw(ctx);
  assert.equal(texts.filter((t) => t === 'Gilded').length, 2, 'bag cell and equipment cell both show the first name line');
  assert.equal(texts.filter((t) => t === 'Leather').length, 2);
  assert.ok(!texts.includes('Gilded '), 'no truncated slice');
});

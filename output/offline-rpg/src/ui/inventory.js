/* Inventory screen (ARCHITECTURE §4, ui/). The model functions are headless: they change `player.inventory` and
   `player.equipment` through RPG.world.equipment and nothing else. draw(ctx, player, view) only reads the player and
   the view ({ x, y, cursor }) and never mutates game state. Equip/unequip/drop never touch the DOM. */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var GRID_COLS = 6;
  var SLOT_SIZE = 44;
  var SLOT_GAP = 8;
  var PANEL_PAD = 12;
  var COMPARE_W = 220;
  var GOOD_COLOR = '#6fcf6f';
  var BAD_COLOR = '#e06c6c';
  var TEXT_COLOR = '#d8d2c4';
  var PANEL_COLOR = 'rgba(16, 14, 20, 0.92)';
  var SLOT_COLOR = '#2b2730';
  var CURSOR_COLOR = '#e8c15a';
  var LABEL_FONT = '12px system-ui, sans-serif';
  var TITLE_FONT = '16px system-ui, sans-serif';

  // Stats shown as percentages in comparison text. The StatBlock stores them as fractions (0.05 = 5%).
  var PERCENT_STATS = { critChance: true, cooldownReduction: true, lifeSteal: true,
    resFire: true, resCold: true, resLightning: true, resPoison: true, resArcane: true };
  var STAT_LABELS = { maxHp: 'HP', maxResource: 'Resource', damage: 'Damage', attackSpeed: 'Attack speed',
    moveSpeed: 'Move speed', armor: 'Armor', critChance: 'Crit chance', critDamage: 'Crit damage',
    lifeRegen: 'Life regen', resourceRegen: 'Resource regen', cooldownReduction: 'Cooldown reduction',
    lifeSteal: 'Life steal', skillDamage: 'Skill damage', pickupRadius: 'Pickup radius', goldFind: 'Gold find',
    xpGain: 'XP gain', thorns: 'Thorns' };

  function invSize(player) { return player.inventory.length; }

  /** Equipment in the save format is a plain slot map; equipment.js works on `{ slots }`. Shares the same object. */
  function eqOf(player) {
    if (!player.equipment) player.equipment = {};
    return { slots: player.equipment };
  }

  function refresh(player) {
    if (RPG.sim && RPG.sim.player && RPG.sim.player.refreshStats) RPG.sim.player.refreshStats(player);
    else player.stats = player.statBlock.snapshot();
  }

  /**
   * Places `item` in the first empty inventory slot (a dropped item, say). Returns the slot index, or -1 when the
   * inventory is full. A full inventory refuses the pickup and leaves the item where it lies.
   */
  function pickup(player, item) {
    if (!item) return -1;
    for (var i = 0; i < invSize(player); i++) {
      if (!player.inventory[i]) { player.inventory[i] = item; return i; }
    }
    return -1;
  }

  /**
   * Equips the item in inventory slot `index`. The displaced item (if any) takes the same inventory slot, so an equip
   * never needs free space. Returns { ok, reason?, displaced? }.
   */
  function equipFromSlot(player, index) {
    var item = player.inventory[index];
    if (!item) return { ok: false, reason: 'empty' };
    if (!RPG.world.equipment.isEquippable(item)) return { ok: false, reason: 'not-equippable' };
    var eq = eqOf(player);
    var displaced = RPG.world.equipment.equip(eq, player.statBlock, item);
    player.inventory[index] = displaced;
    refresh(player);
    return { ok: true, displaced: displaced };
  }

  /**
   * Moves the item in equipment `slot` into the first empty inventory slot. Refuses (ok: false) when the inventory is
   * full, so the item stays equipped and nothing is lost.
   */
  function unequipToInventory(player, slot) {
    var eq = eqOf(player);
    var item = eq.slots[slot];
    if (!item) return { ok: false, reason: 'empty' };
    var free = -1;
    for (var i = 0; i < invSize(player); i++) if (!player.inventory[i]) { free = i; break; }
    if (free < 0) return { ok: false, reason: 'inventory-full' };
    RPG.world.equipment.unequip(eq, player.statBlock, slot);
    player.inventory[free] = item;
    refresh(player);
    return { ok: true, index: free };
  }

  /** Removes the item in inventory slot `index` and returns it, so the caller can spawn it on the ground. */
  function dropFromSlot(player, index) {
    var item = player.inventory[index];
    if (!item) return null;
    player.inventory[index] = null;
    return item;
  }

  /** Stat deltas of the item in inventory slot `index` against what is equipped in its slot; null if it is not equippable. */
  function compareSlot(player, index) {
    var item = player.inventory[index];
    if (!item || !RPG.world.equipment.isEquippable(item)) return null;
    return RPG.world.equipment.compare(eqOf(player), player.statBlock, item);
  }

  function formatValue(stat, diff) {
    var sign = diff > 0 ? '+' : '-';
    var mag = Math.abs(diff);
    var text = PERCENT_STATS[stat] ? Math.round(mag * 1000) / 10 + '%' : String(Math.round(mag * 100) / 100);
    return sign + text;
  }

  /**
   * Turns a compare() result into display lines, sorted by stat key so the order is stable. Each line is
   * { stat, label, diff, text, tone: 'up' | 'down' }. Positive deltas are 'up' (drawn green), negative 'down' (red).
   */
  function formatDeltas(cmp) {
    if (!cmp) return [];
    return Object.keys(cmp.deltas).sort().map(function (stat) {
      var d = cmp.deltas[stat];
      var label = STAT_LABELS[stat] || stat;
      return { stat: stat, label: label, diff: d.diff, text: formatValue(stat, d.diff) + ' ' + label,
        tone: d.diff > 0 ? 'up' : 'down' };
    });
  }

  /** Grid geometry: slot i sits at column i % GRID_COLS, row floor(i / GRID_COLS), inside a panel at (x, y). */
  function slotRect(index, x, y) {
    var col = index % GRID_COLS, row = Math.floor(index / GRID_COLS);
    return { x: x + PANEL_PAD + col * (SLOT_SIZE + SLOT_GAP), y: y + PANEL_PAD + row * (SLOT_SIZE + SLOT_GAP),
      w: SLOT_SIZE, h: SLOT_SIZE };
  }

  /** Maps a pixel to an inventory slot index, or -1 when it lands in padding, a gap, or past the last slot. */
  function hitTest(px, py, x, y, count) {
    var n = count === undefined ? 24 : count;
    for (var i = 0; i < n; i++) {
      var r = slotRect(i, x, y);
      if (px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h) return i;
    }
    return -1;
  }

  /** Moves the cursor one step in grid space, clamped to the inventory. Returns the new index. */
  function moveCursor(cursor, dx, dy, count) {
    var n = count === undefined ? 24 : count;
    var col = cursor % GRID_COLS, row = Math.floor(cursor / GRID_COLS);
    col = Math.min(GRID_COLS - 1, Math.max(0, col + dx));
    row = Math.max(0, row + dy);
    var next = row * GRID_COLS + col;
    return next >= n ? cursor : next;
  }

  /**
   * Runs a keyboard/mouse action ('equip' | 'unequip' | 'drop') on the cursor's slot. `unequip` takes an equipment
   * slot name; the other two take an inventory index. Returns the underlying result.
   */
  function act(player, action, target) {
    if (action === 'equip') return equipFromSlot(player, target);
    if (action === 'unequip') return unequipToInventory(player, target);
    if (action === 'drop') {
      var item = dropFromSlot(player, target);
      refresh(player);
      return item ? { ok: true, item: item } : { ok: false, reason: 'empty' };
    }
    throw new Error('inventory: unknown action ' + action);
  }

  function drawSlot(ctx, item, r, selected) {
    ctx.fillStyle = SLOT_COLOR;
    ctx.fillRect(r.x, r.y, r.w, r.h);
    if (item) {
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = LABEL_FONT;
      ctx.fillText(String(item.name || item.baseId).slice(0, 6), r.x + 4, r.y + r.h / 2 + 4);
    }
    if (selected) {
      ctx.strokeStyle = CURSOR_COLOR;
      ctx.lineWidth = 2;
      ctx.strokeRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2);
    }
  }

  /** Canvas draw: the grid of inventory slots, plus the comparison text for the cursor's item. */
  function draw(ctx, player, view) {
    var x = view.x, y = view.y, cursor = view.cursor || 0;
    var rows = Math.ceil(invSize(player) / GRID_COLS);
    var gridW = GRID_COLS * SLOT_SIZE + (GRID_COLS - 1) * SLOT_GAP + PANEL_PAD * 2;
    var gridH = rows * SLOT_SIZE + (rows - 1) * SLOT_GAP + PANEL_PAD * 2 + 24;
    ctx.fillStyle = PANEL_COLOR;
    ctx.fillRect(x, y, gridW + COMPARE_W, gridH);
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = TITLE_FONT;
    ctx.fillText('Inventory', x + PANEL_PAD, y + PANEL_PAD + 12);
    for (var i = 0; i < invSize(player); i++) {
      drawSlot(ctx, player.inventory[i], slotRect(i, x, y + 24), i === cursor);
    }
    var lines = formatDeltas(compareSlot(player, cursor));
    var cx = x + gridW + PANEL_PAD;
    ctx.font = LABEL_FONT;
    for (var j = 0; j < lines.length; j++) {
      ctx.fillStyle = lines[j].tone === 'up' ? GOOD_COLOR : BAD_COLOR;
      ctx.fillText(lines[j].text, cx, y + 24 + PANEL_PAD + j * 16);
    }
  }

  /**
   * Screen definition for RPG.ui.screens (ui/screens.js): { id, draw, onKey }, the shape its register() takes.
   * Keys are already mapped to actions by the caller: 'left' | 'right' | 'up' | 'down' move the cursor; 'equip' and
   * 'drop' act on the cursor's slot; 'unequip' takes the equipment slot name as its second argument.
   * The registry is a factory with no global, so the owner of the screen manager calls register(createScreen(...)).
   */
  function createScreen(player, layout) {
    var origin = layout || { x: 0, y: 0 };
    var state = { cursor: 0 };
    return {
      id: 'inventory',
      draw: function (ctx) { draw(ctx, player, { x: origin.x, y: origin.y, cursor: state.cursor }); },
      onKey: function (action, arg) {
        if (action === 'left') state.cursor = moveCursor(state.cursor, -1, 0, invSize(player));
        else if (action === 'right') state.cursor = moveCursor(state.cursor, 1, 0, invSize(player));
        else if (action === 'up') state.cursor = moveCursor(state.cursor, 0, -1, invSize(player));
        else if (action === 'down') state.cursor = moveCursor(state.cursor, 0, 1, invSize(player));
        else if (action === 'equip' || action === 'drop') return act(player, action, state.cursor);
        else if (action === 'unequip') return act(player, action, arg);
        return null;
      }
    };
  }

  RPG.ui.inventory = {
    GRID_COLS: GRID_COLS, SLOT_SIZE: SLOT_SIZE, SLOT_GAP: SLOT_GAP,
    pickup: pickup, equipFromSlot: equipFromSlot, unequipToInventory: unequipToInventory,
    dropFromSlot: dropFromSlot, compareSlot: compareSlot, formatDeltas: formatDeltas,
    slotRect: slotRect, hitTest: hitTest, moveCursor: moveCursor, act: act, draw: draw,
    createScreen: createScreen
  };
})(globalThis.RPG = globalThis.RPG || {});

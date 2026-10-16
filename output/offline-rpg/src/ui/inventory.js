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
  var HEADER_H = 24; // the title line above the grid; slots are laid out from y + HEADER_H
  var HUD_INSET = 80; // the HUD bars (HP/resource/XP) occupy y 16..74 at the top left; panels start below them
  var EQ_COLS = 4;    // equipment slots are laid out in a block of EQ_COLS columns under the bag grid
  var EQ_HEAD_H = 18; // the 'Equipment' sub-title line
  var DROP_BTN = Object.freeze({ w: 60, h: 22 }); // pointer stand-in for the drop key, drawn under the comparison text
  var GOOD_COLOR = '#6fcf6f';
  var BAD_COLOR = '#e06c6c';
  var TEXT_COLOR = '#d8d2c4';
  var PANEL_COLOR = 'rgba(16, 14, 20, 0.92)';
  var SLOT_COLOR = '#2b2730';
  var CURSOR_COLOR = '#e8c15a';
  var EMPTY_COLOR = '#7a7584';
  var SLOT_LABEL_FONT = '10px system-ui, sans-serif';
  var NAME_FONT = '9px system-ui, sans-serif'; // item names in cells: about 8 characters per line across a SLOT_SIZE cell
  var NAME_CHARS = 8;
  var NAME_LINES = 3;
  var NAME_LINE_H = 10;
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

  /** Item name split into at most NAME_LINES lines of NAME_CHARS characters: breaks at spaces, hard-splits long words,
      and ends with an ellipsis when the name still does not fit (the cursor item's full name is in the panel text). */
  function nameLines(name) {
    var words = String(name).split(/\s+/).filter(Boolean), lines = [], cur = '';
    words.forEach(function (w) {
      while (w.length > NAME_CHARS) {
        if (cur) { lines.push(cur); cur = ''; }
        lines.push(w.slice(0, NAME_CHARS));
        w = w.slice(NAME_CHARS);
      }
      if (!cur) cur = w;
      else if ((cur + ' ' + w).length <= NAME_CHARS) cur += ' ' + w;
      else { lines.push(cur); cur = w; }
    });
    if (cur) lines.push(cur);
    if (lines.length > NAME_LINES) {
      lines = lines.slice(0, NAME_LINES);
      var last = lines[NAME_LINES - 1];
      lines[NAME_LINES - 1] = last.slice(0, NAME_CHARS - 1) + '\u2026';
    }
    return lines;
  }

  function drawName(ctx, name, r, dx) {
    var lines = nameLines(name), top = r.y + (r.h - lines.length * NAME_LINE_H) / 2 + NAME_LINE_H - 2;
    ctx.font = NAME_FONT;
    lines.forEach(function (l, i) { ctx.fillText(l, r.x + dx, top + i * NAME_LINE_H); });
  }

  function drawSlot(ctx, item, r, selected) {
    ctx.fillStyle = SLOT_COLOR;
    ctx.fillRect(r.x, r.y, r.w, r.h);
    if (item) {
      ctx.fillStyle = TEXT_COLOR;
      drawName(ctx, item.name || item.baseId, r, 3);
    }
    if (selected) {
      ctx.strokeStyle = CURSOR_COLOR;
      ctx.lineWidth = 2;
      ctx.strokeRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2);
    }
  }

  function gridWidth() { return GRID_COLS * SLOT_SIZE + (GRID_COLS - 1) * SLOT_GAP + PANEL_PAD * 2; }

  /** Whole-panel rectangle at (x, y); presses outside it are "back" to the screen manager. */
  function panelRect(player, x, y) {
    var rows = Math.ceil(invSize(player) / GRID_COLS);
    return { x: x, y: y, w: gridWidth() + COMPARE_W, h: bagHeight(rows) + equipBlockHeight() + PANEL_PAD * 2 + HEADER_H };
  }

  function bagHeight(rows) { return rows * SLOT_SIZE + (rows - 1) * SLOT_GAP; }

  function equipBlockHeight() {
    var rows = Math.ceil(RPG.data.itemSlots.length / EQ_COLS);
    return EQ_HEAD_H + rows * SLOT_SIZE + (rows - 1) * SLOT_GAP + SLOT_GAP;
  }

  /** Rectangle of the equipment slot `name` (one of RPG.data.itemSlots), inside a panel at (x, y). */
  function equipSlotRect(player, name, x, y) {
    var i = RPG.data.itemSlots.indexOf(name);
    var rows = Math.ceil(invSize(player) / GRID_COLS);
    var top = y + HEADER_H + PANEL_PAD + bagHeight(rows) + SLOT_GAP + EQ_HEAD_H;
    return { x: x + PANEL_PAD + (i % EQ_COLS) * (SLOT_SIZE + SLOT_GAP), y: top + Math.floor(i / EQ_COLS) * (SLOT_SIZE + SLOT_GAP),
      w: SLOT_SIZE, h: SLOT_SIZE };
  }

  /** The equipment slot name under a pixel, or null. */
  function equipHitTest(player, px, py, x, y) {
    var slots = RPG.data.itemSlots;
    for (var i = 0; i < slots.length; i++) {
      var r = equipSlotRect(player, slots[i], x, y);
      if (px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h) return slots[i];
    }
    return null;
  }

  function dropRect(player, x, y) {
    var p = panelRect(player, x, y);
    return { x: x + gridWidth() + PANEL_PAD, y: p.y + p.h - PANEL_PAD - DROP_BTN.h, w: DROP_BTN.w, h: DROP_BTN.h };
  }

  function inRect(r, px, py) { return px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h; }

  /** The equipped item per slot, under the bag grid; an empty slot shows its slot name dimmed. */
  function drawEquipment(ctx, player, x, y) {
    var rows = Math.ceil(invSize(player) / GRID_COLS);
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = LABEL_FONT;
    ctx.fillText('Equipment', x + PANEL_PAD, y + HEADER_H + PANEL_PAD + bagHeight(rows) + SLOT_GAP + 12);
    RPG.data.itemSlots.forEach(function (name) {
      var r = equipSlotRect(player, name, x, y);
      var item = player.equipment && player.equipment[name];
      ctx.fillStyle = SLOT_COLOR;
      ctx.fillRect(r.x, r.y, r.w, r.h);
      ctx.fillStyle = item ? TEXT_COLOR : EMPTY_COLOR;
      if (item) drawName(ctx, item.name || item.baseId, r, 3);
      else { ctx.font = SLOT_LABEL_FONT; ctx.fillText(name, r.x + 3, r.y + r.h / 2 + 3); }
    });
  }

  /** Canvas draw: the grid of inventory slots, plus the comparison text for the cursor's item. */
  function draw(ctx, player, view) {
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    var x = view.x, y = view.y, cursor = view.cursor || 0;
    var gridW = gridWidth();
    var panel = panelRect(player, x, y);
    ctx.fillStyle = PANEL_COLOR;
    ctx.fillRect(x, y, panel.w, panel.h);
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = TITLE_FONT;
    ctx.fillText('Inventory', x + PANEL_PAD, y + PANEL_PAD + 12);
    for (var i = 0; i < invSize(player); i++) {
      drawSlot(ctx, player.inventory[i], slotRect(i, x, y + HEADER_H), i === cursor);
    }
    drawEquipment(ctx, player, x, y);
    var lines = formatDeltas(compareSlot(player, cursor));
    var cx = x + gridW + PANEL_PAD;
    ctx.font = LABEL_FONT;
    for (var j = 0; j < lines.length; j++) {
      ctx.fillStyle = lines[j].tone === 'up' ? GOOD_COLOR : BAD_COLOR;
      ctx.fillText(lines[j].text, cx, y + HEADER_H + PANEL_PAD + j * 16);
    }
    var drop = dropRect(player, x, y);
    ctx.strokeStyle = TEXT_COLOR;
    ctx.lineWidth = 1;
    ctx.strokeRect(drop.x, drop.y, drop.w, drop.h);
    ctx.fillStyle = TEXT_COLOR;
    ctx.fillText('Drop', drop.x + 14, drop.y + 15);
  }

  /**
   * Screen definition for RPG.ui.screens (ui/screens.js): { id, draw, onKey }, the shape its register() takes.
   * Keys are already mapped to actions by the caller: 'left' | 'right' | 'up' | 'down' move the cursor; 'equip' and
   * 'drop' act on the cursor's slot; 'unequip' takes the equipment slot name as its second argument.
   * The registry is a factory with no global, so the owner of the screen manager calls register(createScreen(...)).
   */
  function createScreen(player, layout, report) {
    var origin = layout || { x: 0, y: 0 };
    var state = { cursor: 0 };
    function top() { return origin.y + HUD_INSET; }
    var noReport = function () {};
    var show = report || noReport;
    return {
      id: 'inventory',
      // A press on a slot selects it; a press on the selected slot equips it (Enter). The Drop button drops the
      // selected slot (X). Other presses inside the panel are ignored; outside it they are "back". Results go to `report`.
      onPointer: function (px, py) {
        if (!inRect(panelRect(player, origin.x, top()), px, py)) return false;
        if (inRect(dropRect(player, origin.x, top()), px, py)) { show(act(player, 'drop', state.cursor)); return true; }
        var eqSlot = equipHitTest(player, px, py, origin.x, top());
        if (eqSlot) { show(act(player, 'unequip', eqSlot)); return true; }
        var hit = hitTest(px, py, origin.x, top() + HEADER_H, invSize(player));
        if (hit < 0) return true;
        if (hit === state.cursor) show(act(player, 'equip', hit));
        else state.cursor = hit;
        return true;
      },
      draw: function (ctx) { draw(ctx, player, { x: origin.x, y: top(), cursor: state.cursor }); },
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
    slotRect: slotRect, equipSlotRect: equipSlotRect, equipHitTest: equipHitTest, panelRect: panelRect,
    HUD_INSET: HUD_INSET, nameLines: nameLines, NAME_LINES: NAME_LINES, NAME_CHARS: NAME_CHARS, hitTest: hitTest, moveCursor: moveCursor, act: act, draw: draw,
    createScreen: createScreen
  };
})(globalThis.RPG = globalThis.RPG || {});

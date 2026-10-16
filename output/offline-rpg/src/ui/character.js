/* Character sheet (ARCHITECTURE §4, ui/; INTEGRATION-PLAN W4-13b). sheet(player) is headless and only reads the player.
   It lists every derived stat in player.statBlock, with the resistances in their own section, then the equipped item
   in each slot. draw(ctx, player, view) only reads the player and never mutates game state. */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var PANEL_PAD = 12;
  var COL_W = 200;
  var HUD_INSET = 80; // the HUD bars occupy y 16..74 at the top left; the sheet starts below them
  var LINE_H = 16;
  var SECTION_GAP = 10;
  var TEXT_COLOR = '#d8d2c4';
  var HEAD_COLOR = '#e8c15a';
  var PANEL_COLOR = 'rgba(16, 14, 20, 0.92)';
  var LABEL_FONT = '12px system-ui, sans-serif';
  var TITLE_FONT = '16px system-ui, sans-serif';

  var RESIST_STATS = ['resFire', 'resCold', 'resLightning', 'resPoison', 'resArcane'];
  // Stored as fractions (0.05 = 5%), so they are shown as percentages.
  var PERCENT_STATS = { critChance: true, cooldownReduction: true, lifeSteal: true };
  RESIST_STATS.forEach(function (k) { PERCENT_STATS[k] = true; });

  /** camelCase stat key to a label: 'maxHp' -> 'Max Hp', 'critChance' -> 'Crit Chance'. */
  function labelOf(stat) {
    return stat.replace(/^res(?=[A-Z])/, '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, function (c) {
      return c.toUpperCase();
    });
  }

  function formatValue(stat, value) {
    if (PERCENT_STATS[stat]) return Math.round(value * 1000) / 10 + '%';
    return String(Math.round(value * 100) / 100);
  }

  function row(stat, value) {
    return { stat: stat, label: labelOf(stat), value: value, text: formatValue(stat, value) };
  }

  /** Stat rows (sorted by key), resistance rows, and the equipped item per slot. All read-only. */
  function sheet(player) {
    var snap = player.statBlock.snapshot(), keys = Object.keys(snap).sort();
    var stats = [], resistances = [];
    keys.forEach(function (k) {
      (RESIST_STATS.indexOf(k) !== -1 ? resistances : stats).push(row(k, snap[k]));
    });
    var equipment = RPG.data.itemSlots.map(function (slot) {
      var item = (player.equipment && player.equipment[slot]) || null;
      return { slot: slot, label: labelOf(slot), item: item ? item.name || item.baseId : null };
    });
    return { stats: stats, resistances: resistances, equipment: equipment };
  }

  /** Column contents of the sheet: stats on the left, resistances then equipment on the right. Pure, for layout and tests. */
  function columns(player) {
    var data = sheet(player);
    var left = [{ head: true, text: 'Stats' }];
    data.stats.forEach(function (r) { left.push({ text: r.label + ': ' + r.text }); });
    var right = [{ head: true, text: 'Resistances' }];
    data.resistances.forEach(function (r) { right.push({ text: r.label + ': ' + r.text }); });
    right.push({ gap: true });
    right.push({ head: true, text: 'Equipment' });
    data.equipment.forEach(function (e) { right.push({ text: e.label + ': ' + (e.item || '-') }); });
    return [left, right];
  }

  function columnHeight(lines) {
    return lines.reduce(function (h, l) { return h + (l.gap ? SECTION_GAP : LINE_H); }, 0);
  }

  /** Panel size for the sheet: two columns side by side, so it fits a 640x480 window below the HUD bars. */
  function panelSize(player) {
    var cols = columns(player);
    return { w: PANEL_PAD * 2 + COL_W * cols.length, h: PANEL_PAD * 2 + 24 + Math.max(columnHeight(cols[0]), columnHeight(cols[1])) };
  }

  /** Canvas draw of the sheet: stats in the left column; resistances and equipment in the right. */
  function draw(ctx, player, view) {
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    var x = view.x, y = view.y;
    var size = panelSize(player);
    ctx.fillStyle = PANEL_COLOR;
    ctx.fillRect(x, y, size.w, size.h);
    ctx.font = TITLE_FONT;
    ctx.fillStyle = TEXT_COLOR;
    ctx.fillText('Character', x + PANEL_PAD, y + PANEL_PAD + 12);

    columns(player).forEach(function (lines, c) {
      var cy = y + PANEL_PAD + 24;
      lines.forEach(function (line) {
        if (line.gap) { cy += SECTION_GAP; return; }
        ctx.font = LABEL_FONT;
        ctx.fillStyle = line.head ? HEAD_COLOR : TEXT_COLOR;
        ctx.fillText(line.text, x + PANEL_PAD + c * COL_W, cy + LINE_H - 4);
        cy += LINE_H;
      });
    });
  }

  /** Screen definition for RPG.ui.screens (ui/screens.js). The sheet takes no input; the manager closes it. */
  function createScreen(player, layout) {
    var origin = layout || { x: 0, y: 0 };
    return {
      id: 'character',
      draw: function (ctx) { draw(ctx, player, { x: origin.x, y: origin.y + HUD_INSET }); }
    };
  }

  RPG.ui.character = {
    RESIST_STATS: RESIST_STATS, labelOf: labelOf, formatValue: formatValue, sheet: sheet, columns: columns, panelSize: panelSize, HUD_INSET: HUD_INSET, draw: draw,
    createScreen: createScreen
  };
})(globalThis.RPG = globalThis.RPG || {});

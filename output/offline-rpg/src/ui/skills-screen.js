/* Skill tree screen (ARCHITECTURE §4, ui/; INTEGRATION-PLAN W4-13b). The model functions are headless: they change the
   player only through RPG.world.skills and never touch the DOM. draw(ctx, player, view) only reads the player and the
   view ({ x, y, cursor }). The tree is a flat list: each skill row is followed by its upgrade rows, so one cursor
   walks the whole tree. */
(function (RPG) {
  'use strict';

  RPG.ui = RPG.ui || {};

  var PANEL_PAD = 12;
  var PANEL_W = 460;
  var ROW_H = 18;
  var TITLE_H = 24;
  var DETAIL_H = 34;
  var BAR_H = 36;
  var BAR_SLOT_W = 60;
  var BAR_SLOT_H = 22;
  var INDENT = 16;
  var GOOD_COLOR = '#6fcf6f';
  var LOCKED_COLOR = '#7a7480';
  var TEXT_COLOR = '#d8d2c4';
  var PANEL_COLOR = 'rgba(16, 14, 20, 0.92)';
  var SLOT_COLOR = '#2b2730';
  var CURSOR_COLOR = '#e8c15a';
  var LABEL_FONT = '12px system-ui, sans-serif';
  var TITLE_FONT = '16px system-ui, sans-serif';

  // Reasons from world/skills.js canLearn / canUpgrade / canCast, as player-facing text.
  var REASON_TEXT = {
    'max-rank': 'Already at max rank',
    'level': 'Level too low',
    'prerequisite': 'Prerequisite skill not learned',
    'points-spent': 'Not enough points spent',
    'no-points': 'No skill points',
    'not-learned': 'Learn the skill first',
    'parent-rank': 'Parent skill rank too low',
    'not-slottable': 'Only learned active skills go on the bar',
    'no-gold': 'Not enough gold',
    'nothing-to-respec': 'No skills to respec'
  };

  function S() { return RPG.world.skills; }

  function skillDef(id) { return RPG.data.skills[id]; }

  /** Rows in tree order: skills by tier then id, each followed by its upgrades. Stable for a given data set. */
  function treeRows() {
    var defs = RPG.data.skills;
    var ids = Object.keys(defs).sort(function (a, b) {
      return (defs[a].tier - defs[b].tier) || (a < b ? -1 : a > b ? 1 : 0);
    });
    var rows = [];
    ids.forEach(function (id) {
      rows.push({ kind: 'skill', skillId: id, upgradeId: null });
      defs[id].upgrades.forEach(function (up) {
        rows.push({ kind: 'upgrade', skillId: id, upgradeId: up.id });
      });
    });
    return rows;
  }

  /** Learns the next rank of a skill. Returns { ok, reason? }; the reason is the canLearn gate that failed. */
  function learn(player, skillId) {
    var gate = S().canLearn(player, skillId);
    if (!gate.ok) return { ok: false, reason: gate.reason };
    S().learnSkill(null, player, skillId);
    return { ok: true };
  }

  /** Buys the next rank of one upgrade on a learned skill. Returns { ok, reason? }. */
  function upgrade(player, skillId, upgradeId) {
    var gate = S().canUpgrade(player, skillId, upgradeId);
    if (!gate.ok) return { ok: false, reason: gate.reason };
    S().upgradeSkill(null, player, skillId, upgradeId);
    return { ok: true };
  }

  /** Puts a learned active skill into hotkey slot `slotIndex` (0-based; the keymap's skill1..skill7). */
  function equipToBar(player, skillId, slotIndex) {
    if (!S().equipSkill(player, skillId, slotIndex)) return { ok: false, reason: 'not-slottable' };
    return { ok: true };
  }

  /** Refunds every spent point for gold (`respecCost`). Refused when nothing is spent or the player is short of gold. */
  function respec(player) {
    if (!player.skills || player.skills.spentPoints === 0) return { ok: false, reason: 'nothing-to-respec' };
    var r = S().respec(null, player, S().respecCost(player));
    return r.ok ? { ok: true, refunded: r.refunded } : { ok: false, reason: r.reason };
  }

  /** Human-readable unlock requirements for a skill, e.g. "Level 4, Shield 1". Empty string when there are none. */
  function requirementText(skillId) {
    var d = skillDef(skillId), req = d.requires || {}, parts = [];
    if (req.level && req.level > 1) parts.push('Level ' + req.level);
    for (var id in req.skills || {}) parts.push(skillDef(id).name + ' ' + req.skills[id]);
    if (req.points) parts.push(req.points + ' points spent');
    return parts.join(', ');
  }

  /** Why the next rank of this row cannot be bought right now, or null when it can. */
  function lockReason(player, row) {
    var gate = row.kind === 'skill'
      ? S().canLearn(player, row.skillId)
      : S().canUpgrade(player, row.skillId, row.upgradeId);
    return gate.ok ? null : REASON_TEXT[gate.reason] || gate.reason;
  }

  function findUpgrade(row) {
    return skillDef(row.skillId).upgrades.filter(function (u) { return u.id === row.upgradeId; })[0];
  }

  /** Current rank over max rank for a row: skill ranks, or upgrade ranks on that skill. */
  function rankOf(player, row) {
    if (row.kind === 'skill') return S().getSkillRank(player, row.skillId);
    return S().getUpgradeRanks(player, row.skillId)[row.upgradeId] || 0;
  }

  function rankText(player, row) {
    var max = row.kind === 'skill' ? skillDef(row.skillId).maxRank : findUpgrade(row).maxRank;
    return rankOf(player, row) + '/' + max;
  }

  function rowLabel(row) {
    var d = skillDef(row.skillId);
    if (row.kind === 'skill') return d.name + (d.type === 'passive' ? ' (passive)' : '');
    return '- ' + findUpgrade(row).name;
  }

  /** Skill points one rank of this row costs: 1 for a skill, the upgrade's `cost` for an upgrade. */
  function rowCost(row) {
    return row.kind === 'skill' ? 1 : findUpgrade(row).cost;
  }

  function panelHeight(rowCount) {
    return TITLE_H + rowCount * ROW_H + DETAIL_H + BAR_H + PANEL_PAD * 2;
  }

  function drawBar(ctx, player, x, y) {
    var bar = player.skills.bar;
    for (var i = 0; i < S().BAR_SIZE; i++) {
      var sx = x + i * (BAR_SLOT_W + 4);
      ctx.fillStyle = SLOT_COLOR;
      ctx.fillRect(sx, y, BAR_SLOT_W, BAR_SLOT_H);
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = LABEL_FONT;
      var label = bar[i] ? skillDef(bar[i]).name.slice(0, 7) : '-';
      ctx.fillText((i + 1) + ': ' + label, sx + 4, y + BAR_SLOT_H - 6);
    }
  }

  /** Canvas draw: skill rows with rank and lock state, a detail line for the cursor row, and the hotkey bar. */
  function draw(ctx, player, view) {
    var rows = treeRows(), x = view.x, y = view.y, cursor = view.cursor || 0;
    var cursorRow = rows[cursor];
    ctx.fillStyle = PANEL_COLOR;
    ctx.fillRect(x, y, PANEL_W, panelHeight(rows.length));
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = TITLE_FONT;
    ctx.fillText('Skills  (points: ' + player.skills.points + ')', x + PANEL_PAD, y + PANEL_PAD + 12);

    var rowsTop = y + PANEL_PAD + TITLE_H;
    ctx.font = LABEL_FONT;
    rows.forEach(function (row, i) {
      var ry = rowsTop + i * ROW_H;
      var indent = row.kind === 'upgrade' ? INDENT : 0;
      if (i === cursor) {
        ctx.strokeStyle = CURSOR_COLOR;
        ctx.lineWidth = 1;
        ctx.strokeRect(x + PANEL_PAD - 2, ry, PANEL_W - PANEL_PAD * 2 + 4, ROW_H);
      }
      // Owned rows are green, rows that cannot be bought yet are grey, everything else is plain text.
      if (rankOf(player, row) > 0) ctx.fillStyle = GOOD_COLOR;
      else ctx.fillStyle = lockReason(player, row) ? LOCKED_COLOR : TEXT_COLOR;
      ctx.fillText(rowLabel(row), x + PANEL_PAD + indent, ry + ROW_H - 5);
      ctx.fillText(rankText(player, row), x + PANEL_W - 110, ry + ROW_H - 5);
      ctx.fillText('cost ' + rowCost(row), x + PANEL_W - 60, ry + ROW_H - 5);
    });

    var detailY = rowsTop + rows.length * ROW_H + 14;
    ctx.fillStyle = TEXT_COLOR;
    if (cursorRow) {
      var detail = cursorRow.kind === 'skill' ? requirementText(cursorRow.skillId) : findUpgrade(cursorRow).desc;
      ctx.fillText(detail, x + PANEL_PAD, detailY);
      var why = lockReason(player, cursorRow);
      if (why) {
        ctx.fillStyle = LOCKED_COLOR;
        ctx.fillText(why, x + PANEL_PAD, detailY + 14);
      }
    }
    drawBar(ctx, player, x + PANEL_PAD, y + panelHeight(rows.length) - BAR_H - PANEL_PAD + 6);
  }

  /**
   * Screen definition for RPG.ui.screens (ui/screens.js): { id, draw, onKey }, the shape its register() takes.
   * Keys are mapped to actions by the caller: 'up' | 'down' move the cursor; 'learn' learns the cursor skill or buys
   * the cursor upgrade; 'equip' puts the cursor skill on the bar (arg = 0-based slot); 'respec' refunds for gold.
   * Results are returned for the caller to show; navigation returns null.
   */
  function createScreen(player, layout) {
    var origin = layout || { x: 0, y: 0 };
    var state = { cursor: 0 };
    var count = treeRows().length;
    return {
      id: 'skills',
      draw: function (ctx) { draw(ctx, player, { x: origin.x, y: origin.y, cursor: state.cursor }); },
      onKey: function (action, arg) {
        var row = treeRows()[state.cursor];
        if (action === 'up') state.cursor = Math.max(0, state.cursor - 1);
        else if (action === 'down') state.cursor = Math.min(count - 1, state.cursor + 1);
        else if (action === 'learn') {
          return row.kind === 'skill' ? learn(player, row.skillId) : upgrade(player, row.skillId, row.upgradeId);
        } else if (action === 'equip') {
          return row.kind === 'skill' ? equipToBar(player, row.skillId, arg) : { ok: false, reason: 'not-slottable' };
        } else if (action === 'respec') return respec(player);
        return null;
      }
    };
  }

  RPG.ui.skillsScreen = {
    REASON_TEXT: REASON_TEXT, treeRows: treeRows, learn: learn, upgrade: upgrade, equipToBar: equipToBar,
    respec: respec, requirementText: requirementText, lockReason: lockReason, draw: draw, createScreen: createScreen
  };
})(globalThis.RPG = globalThis.RPG || {});

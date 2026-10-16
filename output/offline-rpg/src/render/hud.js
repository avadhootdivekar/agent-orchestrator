/* Canvas HUD (ARCHITECTURE §4, render/hud). buildHudModel is pure: it turns the world state into a plain view model.
   draw(ctx, model, viewW, viewH) only reads that model, so the drawer never touches game state. Missing optional
   fields fall back to zero or empty values instead of throwing. */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  var SKILL_SLOTS = 7;
  var TWO_PI = Math.PI * 2;
  var START_ANGLE = -Math.PI / 2; // cooldown sweeps clockwise from 12 o'clock
  var MARGIN = 16;
  var BAR_W = 220;
  var BAR_H = 14;
  var BAR_GAP = 8;
  var SLOT_SIZE = 44;
  var SLOT_GAP = 8;
  var OUTLINE_W = 2;
  var TRACK_ALPHA = 0.85;
  var LABEL_FONT = '13px system-ui, sans-serif';
  var KEY_FONT = '12px system-ui, sans-serif';

  function clamp01(v) { return v > 0 ? (v < 1 ? v : 1) : 0; }
  function num(v, fallback) { return typeof v === 'number' && isFinite(v) ? v : fallback; }
  function fraction(cur, max) { return max > 0 ? clamp01(cur / max) : 0; }
  function slotRowWidth(count) { return count * SLOT_SIZE + Math.max(0, count - 1) * SLOT_GAP; }

  /** Final stat value first, then the flat player field (e.g. maxHp on the entity), then the fallback. */
  function statOf(player, key, fallback) {
    if (player.stats && typeof player.stats[key] === 'number') return player.stats[key];
    return num(player[key], fallback);
  }

  /**
   * buildHudModel(state, opts) -> view model. Pure: reads `state`, never mutates it.
   * opts.xpToNext(level) -> int gives the XP needed for the next level (0 when not supplied).
   * opts.skillCooldowns maps skillId -> base cooldown in seconds, so a slot's sweep can be a fraction.
   */
  function buildHudModel(state, opts) {
    var o = opts || {};
    var player = (state && state.player) || {};
    var level = num(player.level, 1);
    var skills = player.skills || {};
    var bar = Array.isArray(skills.bar) ? skills.bar : [];
    var cooldowns = skills.cooldowns || {};
    var baseCooldowns = o.skillCooldowns || {};

    var slots = [];
    for (var i = 0; i < SKILL_SLOTS; i++) {
      var skillId = bar[i] || null;
      var remaining = skillId ? Math.max(0, num(cooldowns[skillId], 0)) : 0;
      var total = skillId ? num(baseCooldowns[skillId], 0) : 0;
      slots.push({
        skillId: skillId,
        cooldown: remaining,
        // Unknown base cooldown with time left: show the slot as fully covered rather than guessing.
        cooldownFrac: total > 0 ? clamp01(remaining / total) : (remaining > 0 ? 1 : 0)
      });
    }

    var weapons = Array.isArray(player.weaponSlots) ? player.weaponSlots : [];
    var weapon = weapons[num(player.activeWeapon, 0)] || null;
    var difficulty = (state && state.difficulty) || null;

    return {
      hp: { cur: num(player.hp, 0), max: statOf(player, 'maxHp', 0) },
      resource: {
        kind: player.resourceKind || null,
        cur: num(player.resource, 0),
        max: statOf(player, 'maxResource', 0)
      },
      xp: { cur: num(player.xp, 0), next: typeof o.xpToNext === 'function' ? num(o.xpToNext(level), 0) : 0 },
      level: level,
      skills: slots,
      weapon: weapon ? (weapon.name || weapon.defId || null) : null,
      difficulty: difficulty ? (difficulty.name || difficulty.id || null) : null
    };
  }

  /** Outlined horizontal bar with a labelled fill. Fill width is proportional to cur / max. */
  function drawBar(ctx, pal, x, y, cur, max, color, label) {
    ctx.globalAlpha = TRACK_ALPHA;
    ctx.fillStyle = pal.bg;
    ctx.fillRect(x, y, BAR_W, BAR_H);
    ctx.globalAlpha = 1;
    ctx.fillStyle = color;
    ctx.fillRect(x, y, BAR_W * fraction(cur, max), BAR_H);
    ctx.lineWidth = OUTLINE_W;
    ctx.strokeStyle = pal.playerOutline;
    ctx.strokeRect(x, y, BAR_W, BAR_H);
    ctx.fillStyle = pal.ui;
    ctx.fillText(label, x + BAR_W + BAR_GAP, y + BAR_H / 2);
  }

  /** One hotkey slot: outlined box, key number, and a clockwise radial cooldown sweep over the icon. */
  function drawSlot(ctx, pal, x, y, slot, index) {
    ctx.fillStyle = slot.skillId ? pal.floor : pal.bg;
    ctx.fillRect(x, y, SLOT_SIZE, SLOT_SIZE);
    ctx.lineWidth = OUTLINE_W;
    ctx.strokeStyle = pal.playerOutline;
    ctx.strokeRect(x, y, SLOT_SIZE, SLOT_SIZE);

    var cx = x + SLOT_SIZE / 2, cy = y + SLOT_SIZE / 2, r = SLOT_SIZE / 2 - OUTLINE_W;
    if (slot.cooldownFrac > 0) {
      ctx.globalAlpha = 0.6;
      ctx.fillStyle = pal.shadow;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, START_ANGLE, START_ANGLE + TWO_PI * slot.cooldownFrac);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    ctx.fillStyle = pal.ui;
    ctx.font = KEY_FONT;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(String((index + 1) % 10), x + 4, y + 3);
    if (slot.cooldown > 0) {
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(Math.ceil(slot.cooldown) + 's', cx, cy);
    }
  }

  /** draw(ctx, model, viewW, viewH): HP, resource and XP bars top-left; weapon and difficulty top-right; skill slots bottom-centre. */
  function draw(ctx, model, viewW, viewH) {
    var pal = RPG.render.palette;
    var m = model || buildHudModel(null);
    var hp = m.hp || {}, res = m.resource || {}, xp = m.xp || {};

    ctx.save();
    ctx.font = LABEL_FONT;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    var y = MARGIN;
    drawBar(ctx, pal, MARGIN, y, num(hp.cur, 0), num(hp.max, 0), pal.enemy,
      'HP ' + Math.ceil(num(hp.cur, 0)) + '/' + Math.ceil(num(hp.max, 0)));
    y += BAR_H + BAR_GAP;
    drawBar(ctx, pal, MARGIN, y, num(res.cur, 0), num(res.max, 0), pal.player,
      (res.kind || 'RES') + ' ' + Math.ceil(num(res.cur, 0)) + '/' + Math.ceil(num(res.max, 0)));
    y += BAR_H + BAR_GAP;
    drawBar(ctx, pal, MARGIN, y, num(xp.cur, 0), num(xp.next, 0), pal.hazard,
      'XP ' + Math.floor(num(xp.cur, 0)) + '/' + Math.floor(num(xp.next, 0)) + '  Lv ' + num(m.level, 1));

    ctx.textAlign = 'right';
    ctx.fillStyle = pal.ui;
    var rightX = num(viewW, 0) - MARGIN;
    if (m.weapon) ctx.fillText(String(m.weapon), rightX, MARGIN + BAR_H / 2);
    if (m.difficulty) ctx.fillText(String(m.difficulty), rightX, MARGIN + BAR_H + BAR_GAP + BAR_H / 2);

    var slots = m.skills || [];
    var startX = (num(viewW, 0) - slotRowWidth(slots.length)) / 2;
    var slotY = num(viewH, 0) - SLOT_SIZE - MARGIN;
    for (var i = 0; i < slots.length; i++) {
      drawSlot(ctx, pal, startX + i * (SLOT_SIZE + SLOT_GAP), slotY, slots[i], i);
    }
    ctx.restore();
  }

  RPG.render.hud = { buildHudModel: buildHudModel, draw: draw };
})(globalThis.RPG);

/* Entity drawers (ARCHITECTURE §4, render/draw-entities): player, monsters, enemy and player projectiles, item drops.
   Reads entities, never mutates them. Positions are interpolated from prevX/prevY when both are finite, so an entity
   without them (or a caller passing alpha 1) draws at its current position. Built on RPG.render.shapes. */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  var QUARTER_TURN = Math.PI / 2;   // rotatedPolygon's vertex starts at 12 o'clock; this turns it to face `facing`
  var BOSS_SIDES = 8;
  var FLASH_COLOUR = '#f0f0f0';
  var EYE_COLOUR = '#f4f4f4';
  var HP_BAR_HEIGHT = 4;
  var HP_BAR_WIDTH_PER_R = 2.4;
  var HP_BAR_GAP = 10;
  var ELITE_RING_GAP = 3;
  var ELITE_RING_WIDTH = 2;
  var BOSS_RING_WIDTH = 3;
  var TINT_ALPHA = 0.3;
  var AFFIX_TINT_ALPHA = 0.25;
  var ITEM_BOB_AMPLITUDE = 1.5;
  var ITEM_BOB_RATE = 3;
  var DROP_RADIUS = 6;
  var GOLD_RADIUS = 4;

  // Colour tokens the palette does not define yet (monster body colours, loot rarity colours, affix tints). The palette
  // is looked up first, so a token added there later wins over these defaults.
  var FALLBACK_COLOURS = {
    bone: '#c9c3a8', moss: '#6b8f5e', slate: '#7b8494', ember: '#d0743c', violet: '#8f6fb0',
    'rarity-common': '#b8bcc6', 'rarity-uncommon': '#7fb76a', 'rarity-rare': '#6a9fd6',
    'rarity-epic': '#a880d0', 'rarity-legendary': '#e0a458'
  };

  // Tint drawn over an elite that carries the matching affix (ELITE_AFFIXES in world/encounters.js).
  var AFFIX_TINTS = {
    fast: '#6fb6c9', shielded: '#c9c3a8', vampiric: '#b04a6a', 'explosive-death': '#e0a458', regenerating: '#6b8f5e'
  };

  // Tint drawn over an entity while a status is active (status ids from world/combat.js and data/skills.js).
  var STATUS_TINTS = {
    burn: '#d0743c', poison: '#6b8f5e', freeze: '#8fd0e0', stun: '#e0d458', slow: '#6fb6c9'
  };

  function finite(v) { return typeof v === 'number' && isFinite(v); }

  function tone(pal, token, fallback) {
    if (!token) return fallback;
    return pal[token] || FALLBACK_COLOURS[token] || fallback;
  }

  /** Draw position for an entity: linear blend from prev to current by alpha when both prev values are known. */
  function position(e, alpha) {
    if (!finite(e.prevX) || !finite(e.prevY) || !finite(alpha)) return { x: e.x, y: e.y };
    return { x: e.prevX + (e.x - e.prevX) * alpha, y: e.prevY + (e.y - e.prevY) * alpha };
  }

  /** Glow tints for every status the entity currently has that has a tint defined. */
  function statusTints(ctx, e, x, y, r) {
    if (!e.status) return;
    for (var id in e.status) {
      if (STATUS_TINTS[id]) RPG.render.shapes.glow(ctx, x, y, r * 1.4, STATUS_TINTS[id], TINT_ALPHA);
    }
  }

  /** Small health bar above an entity. `frac` is clamped to 0..1. */
  function hpBar(ctx, x, y, r, hp, maxHp, pal) {
    var S = RPG.render.shapes;
    if (!(maxHp > 0)) return;
    var frac = Math.min(1, Math.max(0, hp / maxHp));
    var w = r * HP_BAR_WIDTH_PER_R;
    var top = y - r - HP_BAR_GAP;
    S.outlinedRect(ctx, x - w / 2, top, w, HP_BAR_HEIGHT, pal.floor, pal.playerOutline, 1);
    S.outlinedRect(ctx, x - w / 2, top, w * frac, HP_BAR_HEIGHT, pal.enemy, null);
  }

  /** Player: soft contact shadow, body disc, outline, and a facing wedge just inside the rim. */
  function drawPlayer(ctx, p, x, y, pal) {
    var S = RPG.render.shapes;
    pal = pal || RPG.render.palette;
    var r = p.r;
    S.softShadow(ctx, x, y + r * 0.55, r * 1.25, r * 0.62, 1);
    S.outlinedCircle(ctx, x, y, r, pal.player, pal.playerOutline, 2);
    statusTints(ctx, p, x, y, r);

    // Facing marker: a small dark wedge just inside the rim, pointing along `facing`.
    var c = Math.cos(p.facing), s = Math.sin(p.facing);
    var tipX = x + c * (r + 4), tipY = y + s * (r + 4);
    var baseX = x + c * (r * 0.35), baseY = y + s * (r * 0.35);
    var px = -s * r * 0.45, py = c * r * 0.45;
    ctx.beginPath();
    ctx.moveTo(tipX, tipY);
    ctx.lineTo(baseX + px, baseY + py);
    ctx.lineTo(baseX - px, baseY - py);
    ctx.closePath();
    ctx.fillStyle = pal.playerOutline;
    ctx.fill();
  }

  // One painter per def.shape. Each takes (ctx, x, y, r, facing, body, outline) and draws the silhouette. They differ in
  // which primitives they call, so each archetype reads as a different shape even when drawn at the same size.
  var MONSTER_SHAPES = {
    // Round blob with two eyes looking along facing.
    blob: function (ctx, x, y, r, facing, body, line) {
      var S = RPG.render.shapes;
      S.outlinedCircle(ctx, x, y, r, body, line, 2);
      for (var i = -1; i <= 1; i += 2) {
        var a = facing + i * 0.5;
        S.outlinedCircle(ctx, x + Math.cos(a) * r * 0.45, y + Math.sin(a) * r * 0.45, r * 0.18, EYE_COLOUR, line, 1);
      }
    },
    // Sharp triangle pointing along facing: fast and light.
    dart: function (ctx, x, y, r, facing, body, line) {
      RPG.render.shapes.rotatedPolygon(ctx, x, y, r * 1.1, 'triangle', facing + QUARTER_TURN, body, line, 2);
    },
    // Round body with a bow arc on the facing side.
    archer: function (ctx, x, y, r, facing, body, line, pal) {
      var S = RPG.render.shapes;
      S.outlinedCircle(ctx, x, y, r * 0.9, body, line, 2);
      S.arc(ctx, x, y, r * 1.3, facing - 0.9, facing + 0.9, pal.door, 2);
    },
    // Heavy hexagon with a thick outline.
    brute: function (ctx, x, y, r, facing, body, line) {
      RPG.render.shapes.rotatedPolygon(ctx, x, y, r * 1.15, 'hex', 0, body, line, 3);
    },
    // Round body with a lit fuse on top.
    bomb: function (ctx, x, y, r, facing, body, line, pal) {
      var S = RPG.render.shapes;
      S.outlinedCircle(ctx, x, y, r, body, line, 2);
      S.outlinedRect(ctx, x - 2, y - r - 5, 4, 6, pal.hazard, line, 1);
    },
    // Diamond with a glow at the core: ranged caster.
    caster: function (ctx, x, y, r, facing, body, line, pal) {
      var S = RPG.render.shapes;
      S.glow(ctx, x, y, r * 1.2, pal.portal, 0.35);
      S.rotatedPolygon(ctx, x, y, r, 'diamond', 0, body, line, 2);
    },
    // Large octagon with an inner ring and a glow.
    'boss-colossus': function (ctx, x, y, r, facing, body, line, pal) {
      var S = RPG.render.shapes;
      S.glow(ctx, x, y, r * 1.5, body, 0.2);
      S.rotatedPolygon(ctx, x, y, r, BOSS_SIDES, 0, body, line, BOSS_RING_WIDTH);
      S.ring(ctx, x, y, r * 0.5, 2, pal.hazard, 0.8);
    }
  };

  /** Monster: shadow, shape painter from def.shape, elite/boss outline, affix and status tints, hp bar for elites and bosses. */
  function drawMonster(ctx, m, alpha, pal) {
    var S = RPG.render.shapes;
    pal = pal || RPG.render.palette;
    var def = RPG.data.monsters[m.defId];
    var shape = (def && def.shape) || 'blob';
    var paint = MONSTER_SHAPES[shape] || MONSTER_SHAPES.blob;
    var isBoss = !!def && def.archetype === 'boss';
    var pos = position(m, alpha);
    var r = m.r;
    var body = m.flash > 0 ? FLASH_COLOUR : tone(pal, def && def.color, pal.enemy);
    var line = pal.playerOutline;

    S.softShadow(ctx, pos.x, pos.y + r * 0.55, r * 1.1, r * 0.5, 1);
    if (m.elite) S.ring(ctx, pos.x, pos.y, r + ELITE_RING_GAP, ELITE_RING_WIDTH, pal.hazard, 0.9);
    if (isBoss) S.ring(ctx, pos.x, pos.y, r + ELITE_RING_GAP, BOSS_RING_WIDTH, pal.hazard, 0.9);

    paint(ctx, pos.x, pos.y, r, m.facing || 0, body, line, pal);

    if (m.elite && m.affixes) {
      for (var i = 0; i < m.affixes.length; i++) {
        RPG.render.shapes.glow(ctx, pos.x, pos.y, r * 1.3, AFFIX_TINTS[m.affixes[i]] || pal.hazard, AFFIX_TINT_ALPHA);
      }
    }
    statusTints(ctx, m, pos.x, pos.y, r);
    if (m.elite || isBoss) hpBar(ctx, pos.x, pos.y, r, m.hp, m.maxHp, pal);
  }

  /** Projectile: 'arrow' is a triangle along its flight; anything else is an orb, glowing when it trails. */
  function drawProjectile(ctx, pr, alpha, pal) {
    var S = RPG.render.shapes;
    pal = pal || RPG.render.palette;
    var pos = position(pr, alpha);
    var colour = tone(pal, pr.color, pr.team === 1 ? pal.enemy : pal.player);
    if (pr.shape === 'arrow') {
      S.rotatedPolygon(ctx, pos.x, pos.y, pr.r + 2, 'triangle', pr.facing + QUARTER_TURN, colour, pal.playerOutline, 1);
      return;
    }
    if (pr.trail) S.glow(ctx, pos.x, pos.y, pr.r * 2.5, colour, 0.35);
    S.outlinedCircle(ctx, pos.x, pos.y, pr.r, colour, pal.playerOutline, 1);
  }

  /** Item or gold drop: a bobbing diamond in its rarity colour, with a glow for rare and better. Gold is a gold coin. */
  function drawDrop(ctx, d, t, pal) {
    var S = RPG.render.shapes;
    pal = pal || RPG.render.palette;
    var bob = Math.sin((t || 0) * ITEM_BOB_RATE) * ITEM_BOB_AMPLITUDE;
    var x = d.x, y = d.y + bob;
    if (d.gold) {
      S.outlinedCircle(ctx, x, y, GOLD_RADIUS, pal.hazard, pal.playerOutline, 1);
      return;
    }
    var rarity = d.item && RPG.data.rarities && RPG.data.rarities[d.item.rarity];
    var colour = tone(pal, rarity && rarity.color, pal.ui);
    if (rarity && rarity.rank >= 2) S.glow(ctx, x, y, DROP_RADIUS * 2.5, colour, 0.3);
    S.rotatedPolygon(ctx, x, y, DROP_RADIUS, 'diamond', 0, colour, pal.playerOutline, 1);
  }

  RPG.render.entities = Object.freeze({
    drawPlayer: drawPlayer,
    drawMonster: drawMonster,
    drawProjectile: drawProjectile,
    drawDrop: drawDrop
  });
})(globalThis.RPG = globalThis.RPG || {});

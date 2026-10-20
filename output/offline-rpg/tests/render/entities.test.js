'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');
const { recordingContext } = require('./fakectx.js');

const RPG = loadRPG([
  'core/namespace.js', 'core/util.js', 'render/palette.js', 'render/draw-shapes.js', 'render/draw-entities.js',
  'data/monsters.js', 'data/items.js'
]);
const E = RPG.render.entities;
const pal = RPG.render.palette;

const ARCHETYPE_SHAPES = ['blob', 'dart', 'archer', 'brute', 'bomb', 'caster', 'boss-colossus'];

/** Drawing ops only: save/restore bookkeeping is dropped so signatures reflect what was painted. */
const drawOps = (calls) => calls.filter((c) => c.op !== 'save' && c.op !== 'restore');

/** Op names in order, e.g. "beginPath,arc,fill". Used to compare call sequences between shapes. */
const opSignature = (calls) => drawOps(calls).map((c) => c.op).join(',');

/** Every colour that was used as a gradient stop, i.e. each glow/tint that was drawn. */
const gradientColours = (calls) => calls
  .filter((c) => c.op === 'createRadialGradient')
  .flatMap((c) => c.stops.map((s) => s.color));

/** A monster instance built the way world.spawnMonster builds one, with the fields the drawer reads. */
function monster(defId, over = {}) {
  const def = RPG.data.monsters[defId];
  return Object.assign({
    kind: 'monster', defId, x: 100, y: 50, r: def.radius, facing: 0, hp: 30, maxHp: 30, flash: 0,
    status: {}, elite: false, affixes: [], team: 1, dead: false
  }, over);
}

/** Drop with an item instance of the given rarity id (see RPG.data.rarities). */
function itemDrop(rarity) {
  return { kind: 'drop', x: 40, y: 40, item: { baseId: 'sword', ilvl: 1, rarity, mods: [], name: 'x', qty: 1 } };
}

test('each of the seven archetype shapes is used by exactly one monster definition', () => {
  const shapes = Object.values(RPG.data.monsters).map((d) => d.shape).sort();
  assert.deepEqual(shapes, [...ARCHETYPE_SHAPES].sort());
});

test('each archetype shape draws a distinct call sequence', () => {
  const signatures = ARCHETYPE_SHAPES.map((shape) => {
    const def = Object.values(RPG.data.monsters).find((d) => d.shape === shape);
    const rec = recordingContext();
    E.drawMonster(rec.ctx, monster(def.id), 1);
    return opSignature(rec.calls);
  });
  assert.equal(new Set(signatures).size, ARCHETYPE_SHAPES.length, 'signatures must all differ');
  for (const sig of signatures) assert.ok(sig.length > 0, 'every shape paints something');
});

test('a monster with no known definition falls back to the blob painter without throwing', () => {
  const blobRec = recordingContext();
  E.drawMonster(blobRec.ctx, monster('husk-grunt'), 1);
  // Built by hand: monster() needs a real definition for its radius, and this case is a defId with none.
  const orphan = { kind: 'monster', defId: 'no-such-monster', x: 100, y: 50, r: 14, facing: 0, hp: 1, maxHp: 1,
    flash: 0, status: {}, elite: false, affixes: [] };
  const rec = recordingContext();
  assert.doesNotThrow(() => E.drawMonster(rec.ctx, orphan, 1));
  assert.equal(opSignature(rec.calls), opSignature(blobRec.calls));
});

test('elite gets an outline ring and an hp bar; a plain monster gets neither', () => {
  const plain = recordingContext();
  E.drawMonster(plain.ctx, monster('husk-grunt'), 1);
  const elite = recordingContext();
  E.drawMonster(elite.ctx, monster('husk-grunt', { elite: true, hp: 40, maxHp: 75 }), 1);

  const hpBarFills = (calls) => drawOps(calls).filter((c) => c.op === 'fillRect' && c.style === pal.enemy);
  assert.equal(hpBarFills(plain.calls).length, 0, 'plain monster has no hp bar');
  assert.equal(hpBarFills(elite.calls).length, 1, 'elite has an hp bar');

  const ringOf = (calls) => calls.find((c) => c.op === 'stroke' && c.style === pal.hazard);
  assert.equal(ringOf(plain.calls), undefined, 'plain monster has no elite outline');
  assert.ok(ringOf(elite.calls), 'elite has a gold outline ring');
});

test('the hp bar fill is proportional to hp / maxHp and clamped to the bar', () => {
  const rec = recordingContext();
  E.drawMonster(rec.ctx, monster('husk-grunt', { elite: true, hp: 300, maxHp: 75 }), 1);
  const fill = drawOps(rec.calls).find((c) => c.op === 'fillRect' && c.style === pal.enemy);
  const outline = drawOps(rec.calls).find((c) => c.op === 'fillRect' && c.style === pal.floor);
  assert.equal(fill.w, outline.w, 'hp over max clamps to the full bar width');
});

test('boss gets an outline ring and an hp bar even when not elite', () => {
  const rec = recordingContext();
  E.drawMonster(rec.ctx, monster('warden-of-ash', { elite: false, hp: 900, maxHp: 1800 }), 1);
  assert.ok(drawOps(rec.calls).some((c) => c.op === 'fillRect' && c.style === pal.enemy), 'boss hp bar');
  assert.ok(drawOps(rec.calls).some((c) => c.op === 'stroke' && c.style === pal.hazard), 'boss outline ring');
});

test('rarity colours differ across all five rarities', () => {
  const rarities = ['common', 'uncommon', 'rare', 'epic', 'legendary'];
  const fills = rarities.map((r) => {
    const rec = recordingContext();
    E.drawDrop(rec.ctx, itemDrop(r), 0, pal);
    return drawOps(rec.calls).find((c) => c.op === 'fill').style;
  });
  assert.equal(new Set(fills).size, rarities.length, `fills were ${fills.join(', ')}`);
});

test('rare and better drops glow; common does not', () => {
  const common = recordingContext();
  E.drawDrop(common.ctx, itemDrop('common'), 0, pal);
  const legendary = recordingContext();
  E.drawDrop(legendary.ctx, itemDrop('legendary'), 0, pal);
  assert.equal(gradientColours(common.calls).length, 0);
  assert.ok(gradientColours(legendary.calls).length > 0);
});

test('gold drops draw as a gold coin', () => {
  const rec = recordingContext();
  E.drawDrop(rec.ctx, { kind: 'drop', x: 0, y: 0, gold: 12 }, 0, pal);
  assert.ok(drawOps(rec.calls).some((c) => c.op === 'fill' && c.style === pal.hazard));
});

test('a missing prevX/prevY does not throw and draws at the current position', () => {
  const rec = recordingContext();
  const m = monster('bone-archer', { x: 100, y: 50 });
  assert.equal('prevX' in m, false);
  assert.doesNotThrow(() => E.drawMonster(rec.ctx, m, 0.5));
  const shadow = drawOps(rec.calls).find((c) => c.op === 'ellipse');
  assert.equal(shadow.cx, 100, 'shadow sits under the current x');

  const p = { kind: 'projectile', x: 10, y: 10, r: 4, facing: 0, shape: 'orb', team: 1, status: {} };
  assert.doesNotThrow(() => E.drawProjectile(recordingContext().ctx, p, 0.3, pal));
  assert.doesNotThrow(() => E.drawMonster(recordingContext().ctx, monster('husk-grunt'), undefined, pal));
});

test('with prevX/prevY present, the draw position is interpolated by alpha', () => {
  const rec = recordingContext();
  const m = monster('husk-grunt', { x: 100, y: 50, prevX: 60, prevY: 30 });
  E.drawMonster(rec.ctx, m, 0.5);
  const shadow = drawOps(rec.calls).find((c) => c.op === 'ellipse');
  assert.equal(shadow.cx, 80, 'halfway between prevX and x');
});

test('an elite affix tints the body with the affix colour', () => {
  const rec = recordingContext();
  E.drawMonster(rec.ctx, monster('husk-grunt', { elite: true, affixes: ['vampiric'] }), 1);
  assert.ok(gradientColours(rec.calls).includes('#b04a6a'));
});

test('a burning monster gets the burn tint', () => {
  const rec = recordingContext();
  E.drawMonster(rec.ctx, monster('husk-grunt', { status: { burn: { remaining: 2 } } }), 1);
  assert.ok(gradientColours(rec.calls).includes('#d0743c'));
});

test('a hit flash replaces the body colour for that frame', () => {
  const rec = recordingContext();
  E.drawMonster(rec.ctx, monster('husk-grunt', { flash: 0.1 }), 1);
  assert.ok(drawOps(rec.calls).some((c) => c.op === 'fill' && c.style === '#f0f0f0'));
});

test('drawPlayer paints a body in the player colour with the outline, and leaves the context balanced', () => {
  const rec = recordingContext();
  E.drawPlayer(rec.ctx, { r: 12, facing: 0, status: {} }, 100, 50, pal);
  assert.ok(drawOps(rec.calls).some((c) => c.op === 'fill' && c.style === pal.player));
  assert.ok(drawOps(rec.calls).some((c) => c.op === 'stroke' && c.style === pal.playerOutline));
  assert.equal(rec.ctx.saveDepth, 0);
  assert.equal(rec.ctx.minSaveDepth, 0);
});

test('projectiles take team colour when they have no colour of their own', () => {
  const enemy = recordingContext();
  E.drawProjectile(enemy.ctx, { x: 0, y: 0, r: 4, facing: 0, shape: 'orb', team: 1, status: {} }, 1, pal);
  const mine = recordingContext();
  E.drawProjectile(mine.ctx, { x: 0, y: 0, r: 4, facing: 0, shape: 'orb', team: 0, status: {} }, 1, pal);
  assert.equal(drawOps(enemy.calls).find((c) => c.op === 'fill').style, pal.enemy);
  assert.equal(drawOps(mine.calls).find((c) => c.op === 'fill').style, pal.player);
});

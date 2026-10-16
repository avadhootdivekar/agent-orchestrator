'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG, RENDER_FILES } = require('./load.js');
const { recordingContext } = require('./fakectx.js');

const RPG = loadRPG(RENDER_FILES);
const { buildHudModel, draw } = RPG.render.hud;
const pal = RPG.render.palette;

// Objects built inside the vm context have another realm's prototypes, so compare them as plain JSON.
const plain = (v) => JSON.parse(JSON.stringify(v));

const VIEW_W = 800;
const VIEW_H = 600;
const TWO_PI = Math.PI * 2;

/** A full player/world state in the shapes ARCHITECTURE §5 and DATA-DEFINITIONS §1 describe. */
function fullState() {
  return {
    difficulty: { id: 'normal', name: 'Normal' },
    player: {
      hp: 50, resource: 30, xp: 120, level: 4, resourceKind: 'energy', activeWeapon: 1,
      maxHp: 100,
      stats: { maxHp: 100, maxResource: 60 },
      weaponSlots: [{ defId: 'sword', name: 'Iron Sword' }, { defId: 'bow', name: 'Hunter Bow' }, null],
      skills: {
        bar: ['fireball', 'whirlwind', null, null, null, 'mend', null],
        cooldowns: { fireball: 2, mend: 0 }
      }
    }
  };
}

const BASE_COOLDOWNS = { fireball: 4, whirlwind: 6, mend: 8 };
const XP_CURVE = (level) => level * 100;

/** Width of the fill drawn with `style`, as a fraction of its track (the first fillRect with the bg colour). */
function barRatio(calls, style) {
  const track = calls.find((c) => c.op === 'fillRect' && c.style === pal.bg && c.h === 14);
  const fill = calls.find((c) => c.op === 'fillRect' && c.style === style && c.h === 14);
  assert.ok(track && fill, `bar with style ${style} was not drawn`);
  return fill.w / track.w;
}

test('buildHudModel is pure: same input gives the same model and the state is not mutated', () => {
  const state = fullState();
  const before = JSON.stringify(state);
  const a = buildHudModel(state, { xpToNext: XP_CURVE, skillCooldowns: BASE_COOLDOWNS });
  const b = buildHudModel(state, { xpToNext: XP_CURVE, skillCooldowns: BASE_COOLDOWNS });
  assert.equal(JSON.stringify(state), before);
  assert.deepEqual(plain(a), plain(b));
});

test('buildHudModel copies hp, resource, xp, level, weapon and difficulty from the state', () => {
  const m = buildHudModel(fullState(), { xpToNext: XP_CURVE });
  assert.deepEqual(plain(m.hp), { cur: 50, max: 100 });
  assert.deepEqual(plain(m.resource), { kind: 'energy', cur: 30, max: 60 });
  assert.deepEqual(plain(m.xp), { cur: 120, next: 400 });
  assert.equal(m.level, 4);
  assert.equal(m.weapon, 'Hunter Bow');
  assert.equal(m.difficulty, 'Normal');
});

test('skill slots carry the cooldown fraction of remaining over base, with empty slots as null', () => {
  const m = buildHudModel(fullState(), { skillCooldowns: BASE_COOLDOWNS });
  assert.equal(m.skills.length, 7);
  assert.deepEqual(plain(m.skills[0]), { skillId: 'fireball', cooldown: 2, cooldownFrac: 0.5 });
  assert.deepEqual(plain(m.skills[1]), { skillId: 'whirlwind', cooldown: 0, cooldownFrac: 0 });
  assert.deepEqual(plain(m.skills[2]), { skillId: null, cooldown: 0, cooldownFrac: 0 });
  assert.deepEqual(plain(m.skills[5]), { skillId: 'mend', cooldown: 0, cooldownFrac: 0 });
});

test('a cooldown with no known base time shows as fully covered, and remaining is clamped at zero', () => {
  const state = fullState();
  state.player.skills.cooldowns = { fireball: 9, whirlwind: -3 };
  const m = buildHudModel(state, { skillCooldowns: {} });
  assert.equal(m.skills[0].cooldownFrac, 1);
  assert.equal(m.skills[1].cooldown, 0);
  assert.equal(m.skills[1].cooldownFrac, 0);
});

test('cooldown fraction is clamped to 1 when remaining exceeds the base time', () => {
  const state = fullState();
  state.player.skills.cooldowns = { fireball: 10 };
  const m = buildHudModel(state, { skillCooldowns: { fireball: 4 } });
  assert.equal(m.skills[0].cooldownFrac, 1);
});

test('missing optional fields do not throw and fall back to zero or empty values', () => {
  for (const state of [null, undefined, {}, { player: {} }, { player: { skills: {}, weaponSlots: 'x' } }]) {
    const m = buildHudModel(state);
    assert.deepEqual(plain(m.hp), { cur: 0, max: 0 });
    assert.equal(m.level, 1);
    assert.equal(m.xp.next, 0);
    assert.equal(m.weapon, null);
    assert.equal(m.difficulty, null);
    assert.equal(m.skills.length, 7);
  }
});

test('draw renders HP, resource and XP bars at their proportions', () => {
  const { ctx, calls } = recordingContext();
  draw(ctx, buildHudModel(fullState(), { xpToNext: XP_CURVE }), VIEW_W, VIEW_H);
  assert.equal(barRatio(calls, pal.enemy), 0.5); // hp 50 / 100
  assert.equal(barRatio(calls, pal.player), 0.5); // resource 30 / 60
  assert.equal(barRatio(calls, pal.hazard), 0.3); // xp 120 / 400
});

test('bar fill is clamped to the track when the value is above max or below zero', () => {
  const over = fullState();
  over.player.hp = 250;
  const under = fullState();
  under.player.hp = -20;
  const a = recordingContext();
  draw(a.ctx, buildHudModel(over), VIEW_W, VIEW_H);
  assert.equal(barRatio(a.calls, pal.enemy), 1);
  const b = recordingContext();
  draw(b.ctx, buildHudModel(under), VIEW_W, VIEW_H);
  assert.equal(barRatio(b.calls, pal.enemy), 0);
});

test('a bar with max 0 draws an empty fill instead of dividing by zero', () => {
  const { ctx, calls } = recordingContext();
  draw(ctx, buildHudModel({ player: { hp: 10, maxHp: 0 } }), VIEW_W, VIEW_H);
  assert.equal(barRatio(calls, pal.enemy), 0);
});

test('a skill with a cooldown draws a clockwise radial sweep covering its fraction', () => {
  const { ctx, calls } = recordingContext();
  draw(ctx, buildHudModel(fullState(), { skillCooldowns: BASE_COOLDOWNS }), VIEW_W, VIEW_H);
  const sweeps = calls.filter((c) => c.op === 'arc');
  assert.equal(sweeps.length, 1); // only fireball is cooling down
  const span = sweeps[0].a1 - sweeps[0].a0;
  assert.ok(Math.abs(span - TWO_PI * 0.5) < 1e-9, `sweep span ${span}`);
});

test('draw writes the weapon name and difficulty label as text', () => {
  const { ctx, calls } = recordingContext();
  draw(ctx, buildHudModel(fullState(), { xpToNext: XP_CURVE }), VIEW_W, VIEW_H);
  const texts = calls.filter((c) => c.op === 'fillText').map((c) => c.text);
  assert.ok(texts.includes('Hunter Bow'));
  assert.ok(texts.includes('Normal'));
  assert.ok(texts.includes('XP 120/400  Lv 4'));
});

test('draw does not throw with a null or partial model and no optional fields', () => {
  const partials = [null, undefined, {}, { hp: {} }, { skills: [{}] }, buildHudModel(null)];
  for (const model of partials) {
    const { ctx } = recordingContext();
    assert.doesNotThrow(() => draw(ctx, model, VIEW_W, VIEW_H));
  }
});

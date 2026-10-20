'use strict';
// Guards the "flaky under load" diagnosis (W8-05): the simulation uses no wall clock and no Math.random, and every game
// owns its vm realm, so a seeded forest run must be byte-identical no matter how many other games run alongside it.
// A step 06 / boss-combat failure in the full suite therefore means the sources or data changed under the run, never
// "bad luck": this test fails first and points at real non-determinism (shared module state, clock or RNG leakage).

const test = require('node:test');
const assert = require('node:assert/strict');
const { makeGame, makeBot, fingerprint } = require('./helpers.js');

async function start(seed) {
  const g = await makeGame();
  g.newGame(seed, 'normal');
  g.RPG.world.areas.enter(g.game.world, 'forest');
  const bot = makeBot(seed);
  const intent = (game) => { const p = game.world.player; p.hp = p.stats.maxHp; p.dead = false; p.resource = p.stats.maxResource; return bot(game); };
  return { g, intent };
}

test('two interleaved seeded forest games stay identical to a solo run (no shared state between realms)', async () => {
  const solo = await start('det-1');
  solo.g.drive(1800, solo.intent);
  const expected = fingerprint(solo.g.game.world);

  const a = await start('det-1'), b = await start('det-1');
  const other = await start('det-2'); // a different seed running in between must not leak into a or b
  for (let i = 0; i < 6; i++) {
    a.g.drive(300, a.intent);
    other.g.drive(300, other.intent);
    b.g.drive(300, b.intent);
  }
  assert.equal(fingerprint(a.g.game.world), expected);
  assert.equal(fingerprint(b.g.game.world), expected);
  assert.notEqual(fingerprint(other.g.game.world), expected);
  assert.equal(solo.g.game.stepErrors + a.g.game.stepErrors + b.g.game.stepErrors, 0);
});

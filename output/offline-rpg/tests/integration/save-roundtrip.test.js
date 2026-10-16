'use strict';
// Save/load and export/import round trips through core/game.js, with the real persistence layer (INTEGRATION-PLAN §3.3,
// acceptance steps 14-22).
// Two checks, kept separate on purpose:
//  - the saved snapshot itself is byte-equal after a load or import (what the save promises);
//  - continuation is compared in town. SAVE-SCHEMA says uncleared fights respawn after a load, so continuing in the
//    forest diverges by design (the restored game meets fresh monsters), and an exact comparison there would be wrong.
//    Town has no monsters, so continuing there must match exactly.

const test = require('node:test');
const assert = require('node:assert/strict');
const { makeGame, makeBot, makeWalker, memoryBackend, DT } = require('./helpers.js');

const MINUTE_TICKS = 60 / DT;
const CONTINUE_TICKS = 600;

// Keys sorted at every level: restore may rebuild an object with the same values in a different key order.
function canonical(v) {
  if (Array.isArray(v)) return v.map(canonical);
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v).sort()) out[k] = canonical(v[k]);
    return out;
  }
  return v;
}

// Gameplay snapshot as canonical JSON text (strings compare across vm realms). `savedAt` is the wall-clock stamp
// written at save time, not gameplay state, so it is left out.
function snapshotText(g) {
  const snap = g.RPG.persistence.snapshot(g.game.world, { settings: g.game.settings, name: g.game.playerName });
  delete snap.savedAt;
  return JSON.stringify(canonical(snap));
}

// The player as the save stores it. Transient combat fields (i-frames, flash, live status) are not saved, so comparing
// the raw player object would test those too; comparing the saved block tests what the save promises.
function savedPlayerText(g) {
  return JSON.stringify(canonical(g.RPG.persistence.snapshot(g.game.world, { settings: g.game.settings, name: g.game.playerName }).player));
}

// Moves both games to town through the real area transition, then runs the same walk on each.
function continueInTown(g1, g2, seed) {
  for (const g of [g1, g2]) g.RPG.world.areas.enter(g.game.world, 'town');
  g1.drive(CONTINUE_TICKS, makeWalker(seed));
  g2.drive(CONTINUE_TICKS, makeWalker(seed));
}

// Plays a little so the save carries real progress: kills, XP and the forest visited.
async function playedGame(seed, backend) {
  const g = await makeGame({ backend: backend });
  g.newGame(seed);
  g.RPG.world.areas.enter(g.game.world, 'forest');
  g.drive(MINUTE_TICKS, makeBot(seed));
  return g;
}

test('save -> load -> continue: the restored game is byte-equal on save fields and continues identically', async () => {
  const g1 = await playedGame('save-continue');
  const w1 = g1.game.world;
  assert.ok(w1.progress.kills > 0, 'nothing happened before the save');
  assert.equal(g1.game.saveToSlot('1').ok, true);
  const saved = snapshotText(g1);

  const g2 = await makeGame({ backend: g1.backend });
  const loaded = g2.game.loadFromSlot('1');
  assert.equal(loaded.ok, true, 'load failed: ' + JSON.stringify(loaded));
  assert.equal(snapshotText(g2), saved, 'restored state differs from the saved state');

  // Continue both in town, from the same point, with the same world-independent intents.
  continueInTown(g1, g2, 'continue');
  assert.equal(g2.game.stepErrors, 0);
  assert.equal(savedPlayerText(g2), savedPlayerText(g1), 'saved player fields diverged after continuing');
});

test('export -> delete -> import -> continue: the imported save matches the original and continues identically', async () => {
  const g1 = await playedGame('export-import');
  assert.equal(g1.game.saveToSlot('1').ok, true);
  const before = snapshotText(g1);

  const exported = g1.game.exportSave('current');
  assert.equal(exported.ok, true, 'export failed: ' + JSON.stringify(exported));
  assert.match(exported.filename, /\.json$/);
  const parsed = JSON.parse(exported.text); // human-readable JSON, not an opaque blob
  assert.equal(typeof parsed.seed, 'number');

  // Delete the local save: the slot is gone and loading it fails cleanly.
  assert.equal(g1.game.deleteSave('1').ok, true);
  const missing = g1.game.loadFromSlot('1');
  assert.equal(missing.ok, false, 'a deleted save still loads');

  // Import on a fresh "machine" (empty storage) and check it is the same game.
  const g2 = await makeGame({ backend: memoryBackend() });
  const imported = g2.game.importSave(exported.text, '1');
  assert.equal(imported.ok, true, 'import failed: ' + JSON.stringify(imported));
  assert.equal(snapshotText(g2), before, 'imported state differs from the exported state');

  continueInTown(g1, g2, 'after-import');
  assert.equal(savedPlayerText(g2), savedPlayerText(g1), 'saved player fields diverged after importing');
});

test('import rejects invalid input and leaves the running game untouched', async () => {
  const g = await playedGame('bad-import');
  assert.equal(g.game.saveToSlot('1').ok, true);
  const before = snapshotText(g);

  for (const bad of ['{not json', '{"schema":"something-else"}', '[]', '']) {
    const r = g.game.importSave(bad, '1');
    assert.equal(r.ok, false, `accepted invalid input: ${JSON.stringify(bad)}`);
    assert.equal(snapshotText(g), before, `running game changed after rejecting ${JSON.stringify(bad)}`);
  }
});

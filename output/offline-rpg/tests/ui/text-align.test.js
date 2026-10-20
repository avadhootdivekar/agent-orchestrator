'use strict';

// Overlays must not inherit textAlign/textBaseline from whatever drew before them (the HUD right-aligns, the toasts
// centre). A leaked 'right' clipped the Skills and Save/Load panels on the left edge, so every overlay sets it itself.

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadRPG } = require('./load.js');

const RPG = loadRPG(['core/namespace.js', 'core/util.js', 'core/rng.js', 'core/entities.js', 'core/events.js',
  'data/difficulties.js', 'data/areas.js', 'data/items.js', 'data/skills.js', 'input/keymap.js', 'persistence/storage.js',
  'world/stats.js', 'world/difficulty.js', 'world/skills.js', 'world/loot.js', 'world/equipment.js', 'sim/player.js',
  'ui/screens.js', 'ui/inventory.js', 'ui/skills-screen.js', 'ui/character.js', 'ui/saveload.js', 'ui/pause.js',
  'ui/mainmenu.js', 'ui/settings.js']);

// Context whose alignment starts leaked ('right', 'bottom') and records the state seen by every fillText.
function leakyCtx() {
  const seen = [];
  const ctx = {
    seen, textAlign: 'right', textBaseline: 'bottom', font: '', fillStyle: '', strokeStyle: '', lineWidth: 1,
    fillRect() {}, strokeRect() {},
    fillText(text) { seen.push({ text: String(text), align: this.textAlign, baseline: this.textBaseline }); },
  };
  return ctx;
}

const memoryBackend = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: (k) => { m.delete(k); } };
};

function overlays() {
  const player = RPG.sim.player.create({ nextEntityId: 1 }, 0, 0);
  player.skills = RPG.world.skills.createSkillState(2);
  player.stats = player.statBlock;
  const layout = { x: 0, y: 0, w: 1280, h: 661 };
  const screens = RPG.ui.screens.create();
  const storage = RPG.persistence.storage.createStorage({ backend: memoryBackend() });
  const game = { newGame() {}, loadFromSlot() {}, saveToSlot() {}, importSave() {}, exportSave() {}, deleteSave() {}, setPaused() {}, quitToMenu() {} };
  const saveload = RPG.ui.saveload.createScreen({ game, screens, storage, layout });
  const input = { bindings: RPG.input.keymap.defaultBindings(), setBinding() {}, resetBindings() {} };
  const settings = RPG.ui.settings.create({ storage: memoryBackend(), input, audio: { setVolume() {}, mute() {} } });
  return {
    skills: RPG.ui.skillsScreen.createScreen(player, layout),
    inventory: RPG.ui.inventory.createScreen(player, layout),
    character: RPG.ui.character.createScreen(player, layout),
    saveload,
    pause: RPG.ui.pause.createScreen({ game, screens, saveload, layout }),
    mainmenu: RPG.ui.mainmenu.createScreen({ game, screens, saveload, storage, layout }),
    settings: RPG.ui.settings.createScreen({ settings, layout })
  };
}

test('every overlay draws its text left-aligned on the alphabetic baseline even when the context starts leaked', () => {
  const screens = overlays();
  for (const id of Object.keys(screens)) {
    const ctx = leakyCtx();
    screens[id].draw(ctx);
    assert.ok(ctx.seen.length > 0, id + ' drew no text');
    for (const s of ctx.seen) {
      assert.equal(s.align, 'left', id + ' drew "' + s.text + '" with textAlign ' + s.align);
      assert.equal(s.baseline, 'alphabetic', id + ' drew "' + s.text + '" with textBaseline ' + s.baseline);
    }
  }
});

test('the screen manager hands each screen a clean text state and restores the caller\'s afterwards', () => {
  const mgr = RPG.ui.screens.create({ bindings: {} });
  const seen = [];
  mgr.register({ id: 'playing', draw(ctx) { ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; } });
  mgr.register({ id: 'skills', draw(ctx) { seen.push([ctx.textAlign, ctx.textBaseline]); } });
  mgr.open('playing');
  mgr.open('skills');
  const stack = [];
  const ctx = leakyCtx();
  ctx.save = () => stack.push([ctx.textAlign, ctx.textBaseline]);
  ctx.restore = () => { const s = stack.pop(); ctx.textAlign = s[0]; ctx.textBaseline = s[1]; };
  mgr.draw(ctx);
  assert.deepEqual(seen, [['left', 'alphabetic']]);
  assert.equal(ctx.textAlign, 'right');
  assert.equal(ctx.textBaseline, 'bottom');
});

test('the Skills panel stays on screen: its text is left-aligned from the panel origin at 1280x661', () => {
  const ctx = leakyCtx();
  const xs = [];
  ctx.fillText = function (text, x) { xs.push({ x, align: this.textAlign }); };
  overlays().skills.draw(ctx);
  assert.ok(xs.length > 0);
  assert.ok(xs.every((s) => s.align === 'left' && s.x >= 0 && s.x < 1280));
});

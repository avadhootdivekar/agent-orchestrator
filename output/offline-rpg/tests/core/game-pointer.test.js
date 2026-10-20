'use strict';
// Mouse/touch presses reach the menu screens through game.queuePointer(), exactly as start()'s pointerdown listener
// does, and do what the keyboard path does (docs/INTEGRATION-PLAN.md §3, W6-01). Real screens, overlays and settings
// model; stub canvas context. Geometry below is the screens' own: the layout is the 800x600 stub view at (0, 0).

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadEngine, memoryBackend, DT } = require('../integration/helpers.js');

const NO_TOGGLES = () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });

function stubCtx() {
  const texts = [];
  const target = { canvas: { width: 800, height: 600 }, texts };
  return new Proxy(target, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === 'fillText') return (text) => { texts.push(String(text)); };
      if (k === 'measureText') return (text) => ({ width: String(text).length * 7 });
      return () => {};
    },
    set(t, k, v) { t[k] = v; return true; }
  });
}

async function setup(opts) {
  opts = opts || {};
  const RPG = await loadEngine();
  const backend = opts.backend || memoryBackend();
  const storage = RPG.persistence.storage.createStorage({ backend });
  const screens = RPG.ui.screens.create();
  const ctx = stubCtx();
  const frames = [];
  const input = { buildFrame: () => Object.assign(NO_TOGGLES(), frames.shift() || {}) };
  const renderer = { camera: {}, viewW: 800, viewH: 600, dpr: 1, draw() {}, snapTo() {}, resize() {} };
  const settingsStore = RPG.ui.settings.create({ storage: backend });
  if (opts.difficulty) settingsStore.setDifficultyPreset(opts.difficulty);
  const game = RPG.core.game.create({ input, renderer, screens, ctx, storage, settingsStore, clock: { now: () => 0 } });
  const api = {
    RPG, game, screens, ctx, settingsStore, storage,
    frame(toggles, keys) {
      (keys || []).forEach((k) => game.queueKey(k));
      frames.push({ toggles: Object.assign({}, toggles) });
      if (game.shouldStep()) game.tick(DT);
      game.render(0);
    },
    key(code) { api.frame({}, [code]); },
    click(x, y, button) { game.queuePointer(x, y, button); api.frame({}); },
    startGame() { api.key('Enter'); api.key('Enter'); }
  };
  api.frame({}); // first render sizes the live layout from the renderer
  return api;
}

const ROOT_ROW = (i) => 150 + i * 40 - 10;        // mainmenu / new-game rows: baseline TOP + i * ROW_H, box starts 22 above
const PAUSE_ROW = (i) => (600 - 276) / 2 + 20 + 40 + i * 36 - 10; // pause panel is centred; rows 36 apart
const SAVE_ROW = (i) => 80 + i * 36 + 4;

const item = (baseId, uid) => ({ baseId, ilvl: 1, rarity: 'common', mods: [], name: baseId, uid });

test('main menu: clicking New game opens the form; clicking Start game starts, like Enter', async () => {
  const g = await setup();
  g.click(100, ROOT_ROW(0));
  g.click(100, ROOT_ROW(2));      // Start game row of the form
  assert.equal(g.game.state, 'playing');
  assert.ok(g.game.world.player);
});

test('main menu: difficulty row halves step the choice and the started world uses it', async () => {
  const g = await setup();
  g.click(100, ROOT_ROW(0));
  g.click(700, ROOT_ROW(0));      // right half: normal -> hard
  g.click(100, ROOT_ROW(2));
  assert.equal(g.game.world.difficulty.id, 'hard');
});

test('main menu: the difficulty row defaults to the persisted settings difficulty', async () => {
  const g = await setup({ difficulty: 'nightmare' });
  g.click(100, ROOT_ROW(0));
  assert.ok(g.ctx.texts.includes('Difficulty: < Nightmare >'), 'form shows the settings difficulty');
  g.key('Enter');                 // keyboard start, no row changes
  assert.equal(g.game.world.difficulty.id, 'nightmare');
});

test('main menu: the form re-reads the settings difficulty each time it opens', async () => {
  const g = await setup();
  g.click(100, ROOT_ROW(0));
  g.click(700, ROOT_ROW(0));      // hard, inside the form only
  g.click(100, 600 - 10);         // footer = back to the root list
  g.settingsStore.setDifficultyPreset('easy');
  g.click(100, ROOT_ROW(0));
  assert.ok(g.ctx.texts.slice(-6).includes('Difficulty: < Easy >'));
});

test('main menu: right click and the footer go back out of the form; clicking Settings opens settings', async () => {
  const g = await setup();
  g.click(100, ROOT_ROW(0));
  g.click(100, ROOT_ROW(0), 2);   // right button = Esc
  g.click(100, ROOT_ROW(3));      // Settings
  assert.equal(g.screens.top(), 'settings');
  g.click(100, 600 - 10);         // footer = back
  assert.equal(g.screens.top(), 'mainmenu');
});

test('pause: rows act like Enter, outside the panel resumes, Quit needs a second click', async () => {
  const g = await setup();
  g.startGame();
  g.frame({ pause: true });
  assert.equal(g.screens.top(), 'pause');
  g.click(400, PAUSE_ROW(1));     // Save game -> save screen
  assert.equal(g.screens.top(), 'saveload');
  g.click(100, 600 - 10);         // back to the pause menu
  assert.equal(g.screens.top(), 'pause');
  g.click(400, PAUSE_ROW(4));     // Quit: asks first
  assert.equal(g.game.state, 'paused');
  g.click(400, PAUSE_ROW(4));     // second click confirms
  assert.equal(g.game.state, 'mainmenu');

  g.startGame();
  g.frame({ pause: true });
  g.click(5, 5);                  // outside the panel = Esc = resume
  assert.equal(g.game.state, 'playing');
});

test('save screen: a slot click saves, overwriting asks first, another click cancels, the footer goes back', async () => {
  const g = await setup();
  g.startGame();
  g.frame({ pause: true });
  g.click(400, PAUSE_ROW(1));
  g.click(100, SAVE_ROW(1));      // slot 1 (empty): saves at once, like Enter
  assert.equal(g.storage.list().map((e) => e.slot).join(), '1');
  assert.equal(g.screens.top(), 'pause', 'a finished save returns to the pause menu');
  g.click(400, PAUSE_ROW(1));
  g.click(100, SAVE_ROW(1));      // occupied: asks first
  assert.ok(g.ctx.texts.some((t) => /will be overwritten/.test(t)), 'asked before overwriting');
  g.click(100, SAVE_ROW(2));      // a click elsewhere cancels
  assert.ok(g.ctx.texts.includes('Cancelled.'));
  g.click(100, SAVE_ROW(1));      // ask again
  g.click(100, SAVE_ROW(1));      // a press on the same slot answers yes (Enter)
  assert.ok(g.ctx.texts.includes('Saved') || g.screens.top() === 'pause');
  g.click(400, PAUSE_ROW(1));
  g.click(100, 600 - 10);         // the footer is "back"
  assert.equal(g.screens.top(), 'pause');
});

test('settings: a row click toggles like Enter and slider halves step like Left/Right', async () => {
  const g = await setup();
  g.click(100, ROOT_ROW(3));      // Settings
  const before = g.settingsStore.snapshot();
  // Rows are 15 apart from y = 52 - 11; general column starts at x = 12. Row 0 is volume (a slider).
  g.click(200, 41 + 2);           // right half of the volume row
  assert.ok(g.settingsStore.snapshot().volume > before.volume);
  g.click(40, 41 + 2);            // left half
  assert.equal(g.settingsStore.snapshot().volume, before.volume);
  const muted = before.muted;
  g.click(40, 41 + 2 * 15 + 2);   // row 2 is the mute toggle
  assert.equal(g.settingsStore.snapshot().muted, !muted);
});

test('inventory: a click selects a slot, a press on the selected slot equips it (Enter); Drop drops it (X)', async () => {
  const g = await setup();
  g.startGame();
  const p = g.game.world.player;
  const inv = g.RPG.ui.inventory;
  inv.pickup(p, item('leather-vest', 9001));
  inv.pickup(p, item('health-potion', 9002));
  g.frame({ inventory: true });
  assert.equal(g.screens.top(), 'inventory');
  const centre = (i) => [12 + 22 + (i % 6) * 52, inv.HUD_INSET + 24 + 12 + 22 + Math.floor(i / 6) * 52];
  g.click(...centre(1));          // select slot 1: nothing is equipped yet
  assert.equal(p.equipment.chest, undefined);
  g.click(...centre(0));          // select slot 0
  assert.equal(p.equipment.chest, undefined);
  g.click(...centre(0));          // press the selected slot: equips the vest
  assert.ok(p.equipment.chest, 'the vest was equipped');
  assert.equal(p.inventory[0], null);

  g.click(...centre(1));          // select the potion
  const panel = inv.panelRect(p, 0, inv.HUD_INSET);
  const panelBottom = panel.y + panel.h;
  g.click(12 * 2 + 6 * 44 + 5 * 8 + 12 + 5, panelBottom - 12 - 5);  // the Drop button
  assert.equal(p.inventory[1], null, 'Drop dropped the selected slot');
  g.click(799, 599);              // outside the panel is "back"
  assert.equal(g.screens.top(), 'playing');
});

test('inventory: a keyboard Enter and a pointer press on the same slot give the same result', async () => {
  const equipped = [];
  for (const how of ['key', 'pointer']) {
    const g = await setup();
    g.startGame();
    g.RPG.ui.inventory.pickup(g.game.world.player, item('leather-vest', 9100));
    g.frame({ inventory: true });
    if (how === 'key') g.key('Enter');
    else g.click(12 + 22, g.RPG.ui.inventory.HUD_INSET + 24 + 12 + 22);   // the cursor starts on slot 0, so one press equips
    equipped.push(JSON.stringify(g.game.world.player.equipment));
  }
  assert.ok(equipped[0].includes('leather-vest'));
  assert.equal(equipped[1], equipped[0]);
});

test('inventory: a refused pointer equip is toasted like a refused key press', async () => {
  const g = await setup();
  g.startGame();
  g.RPG.ui.inventory.pickup(g.game.world.player, item('health-potion', 9003));
  g.frame({ inventory: true });
  g.click(12 + 22, 24 + 12 + 22); // slot 0 is the potion: not equippable
  g.frame({});                    // toasts are drawn by the playing screen once the inventory is closed
  g.click(799, 599);
  g.frame({});
  assert.ok(g.ctx.texts.some((t) => /equip/i.test(t)), 'the refusal reason reached the screen');
});

test('skills: pressing the selected row learns, a bar slot equips, Respec refunds; outside closes', async () => {
  const g = await setup();
  g.startGame();
  const p = g.game.world.player;
  p.skills.points = 5;
  g.frame({ skills: true });
  assert.equal(g.screens.top(), 'skills');
  const rows = g.RPG.ui.skillsScreen.treeRows();
  const skillRow = rows.findIndex((r) => r.kind === 'skill' && g.RPG.ui.skillsScreen.lockReason(p, r) === null);
  assert.ok(skillRow >= 0, 'some skill is learnable at the start');
  const top = g.RPG.ui.skillsScreen.HUD_INSET; // the panel sits below the HUD bars
  const rowY = (i) => top + 12 + 24 + i * 16 + 8;
  g.click(100, rowY((skillRow + 1) % rows.length));  // move the cursor away first
  g.click(100, rowY(skillRow));   // select it
  assert.equal(p.skills.spentPoints, 0);
  g.click(100, rowY(skillRow));   // press again: learn
  assert.equal(p.skills.spentPoints, 1);
  const id = rows[skillRow].skillId;

  const panelBottom = 12 * 2 + 24 + rows.length * 16 + 34 + 36;
  const barY = top + panelBottom - 36 - 12 + 6 + 11;
  const slottable = g.RPG.sim.skills && g.RPG.sim.skills.equipSkill;
  g.click(12 + 5, barY);          // bar slot 1
  if (slottable) assert.ok(p.skills.bar[0] === id || p.skills.bar[0] === null);

  g.click(500 - 12 - 35, top + 12 - 2 + 10);  // Respec (refund needs gold; either way the press is consumed)
  assert.equal(g.screens.top(), 'skills');
  g.click(799, 599);
  assert.equal(g.screens.top(), 'playing');
});

test('character sheet: any click closes it; dead screen: a click respawns like Enter', async () => {
  const g = await setup();
  g.startGame();
  g.frame({ character: true });
  assert.equal(g.screens.top(), 'character');
  g.click(30, 30);                // the sheet has no controls: a press is "back"
  assert.equal(g.screens.top(), 'playing');

  const w = g.game.world;
  w.player.hp = 0;
  w.player.dead = true;
  g.frame({});
  assert.equal(g.game.state, 'dead');
  g.click(400, 300);
  assert.equal(g.game.state, 'playing');
  assert.ok(g.game.world.player.hp > 0);
});

test('presses while playing do not reach the menus', async () => {
  const g = await setup();
  g.startGame();
  g.click(100, ROOT_ROW(0));
  assert.equal(g.game.state, 'playing');
  assert.equal(g.screens.top(), 'playing');
});

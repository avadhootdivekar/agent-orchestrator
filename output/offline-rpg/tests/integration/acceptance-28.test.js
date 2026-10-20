'use strict';
// Prompt §28 acceptance walk, headless (W6-02). One test per step, run in order against ONE continuing game: each step
// builds on the state the previous one left, exactly as a player's session would. Everything goes through the real
// Game / sim / persistence stack. No starting-weapon or looted-weapon stopgap: the weapons are whatever
// sim.createWorld grants a fresh player (sword, hunting-bow, arc-staff). Steps 15-16 "close the browser / reopen with no
// internet" are modelled by dropping the whole engine realm and booting a new one that only shares the storage backend.
// Test-side helpers only place the player or top up HP/resource so a fight is decided by the mechanic under test;
// XP, level-ups, skill points, drops and pickups all come from the real kill pipeline.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { makeGame, makeBot, makeWalker, memoryBackend, DT, BOSS_ID } = require('./helpers.js');

const TICKS_60S = 60 / DT;
const IDLE = () => ({ move: { x: 0, y: 0 }, aim: { x: 0, y: 0 }, attack: false, skills: [], dash: false, toggles: {} });

// Canonical gameplay snapshot text (sorted keys, no wall-clock stamp); strings compare across vm realms.
function canonical(v) {
  if (Array.isArray(v)) return v.map(canonical);
  if (v && typeof v === 'object') { const o = {}; for (const k of Object.keys(v).sort()) o[k] = canonical(v[k]); return o; }
  return v;
}
function snapshotText(g) {
  const s = g.RPG.persistence.snapshot(g.game.world, { settings: g.game.settings, name: g.game.playerName });
  delete s.savedAt;
  return JSON.stringify(canonical(s));
}
function savedPlayerText(g) {
  return JSON.stringify(canonical(g.RPG.persistence.snapshot(g.game.world, { settings: g.game.settings, name: g.game.playerName }).player));
}

function nearestLiving(w) {
  const p = w.player; let best = null, bd = Infinity;
  for (const m of w.monsters) {
    if (m.dead) continue;
    const d = (m.x - p.x) ** 2 + (m.y - p.y) ** 2;
    if (d < bd) { bd = d; best = m; }
  }
  return best;
}
// Stands the player 60 px from the nearest living monster (test-side placement; no stat is touched). The spot is chosen
// among points around the monster so that neither the player nor the straight line to the monster touches a wall:
// a fixed offset can land inside a wall, where arrows die at once and a wall-bound monster never closes in.
function standNextToMonster(g) {
  const w = g.game.world, m = nearestLiving(w);
  assert.ok(m, 'no living monster in ' + w.area.id);
  const col = g.RPG.world.collision, map = w.area.map, r = w.player.r;
  const open = (x, y) => !col.circleOverlapsSolid(map, x, y, r);
  const clearLine = (x, y) => {
    for (let t = 0; t <= 1; t += 0.1) if (!open(x + (m.x - x) * t, y + (m.y - y) * t)) return false;
    return true;
  };
  for (let k = 0; k < 16; k++) {
    const a = Math.PI + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (Math.PI / 8);
    const x = m.x + Math.cos(a) * 60, y = m.y + Math.sin(a) * 60;
    if (open(x, y) && clearLine(x, y)) { w.player.x = x; w.player.y = y; return m; }
  }
  w.player.x = m.x - 60; w.player.y = m.y;
  return m;
}
// The bot, with HP and energy topped up before every step so a fight is decided by damage dealt alone.
function godBot(seed) {
  const bot = makeBot(seed);
  return (game) => {
    const p = game.world.player;
    p.hp = p.stats.maxHp; p.dead = false; p.resource = p.stats.maxResource;
    return bot(game);
  };
}
function sumLevels(w) { return w.monsters.filter((m) => !m.dead).reduce((s, m) => s + (m.level || 0), 0); }
function meanMaxHp(w) {
  const l = w.monsters.filter((m) => !m.dead);
  return l.reduce((s, m) => s + m.maxHp, 0) / l.length;
}

// A throwaway game for spawn comparisons, so the main session's cleared areas do not matter.
async function scratchGame(difficulty) {
  const g = await makeGame();
  const r = g.game.newGame({ seed: 'acc-scratch', difficulty: difficulty || 'normal' });
  assert.equal(r.ok, true);
  return g;
}
function livingRegular(w) { return w.monsters.filter((m) => !m.dead && m.defId !== BOSS_ID); }

// Session state shared by the steps below.
const S = { g: null, backend: memoryBackend(), kills: [], levelUps: [], drops: [], pickups: [], savedText: null, exported: null };

test('step 01: open the game locally (engine boots offline in a bare realm and lands on the main menu)', async () => {
  const g = await makeGame({ backend: S.backend });
  S.g = g;
  assert.equal(g.game.state, 'mainmenu');
  assert.equal(g.game.world, null, 'no world before a game is started');
  // The bare vm realm has no window, document or network API, so booting here proves the engine needs none of them.
  // And the shipped single-file build is built (npm test runs `build --verify` first) and references nothing remote.
  const dist = path.resolve(__dirname, '..', '..', 'dist', 'index.html');
  assert.ok(fs.existsSync(dist), 'dist/index.html is missing: run npm run build');
  const html = fs.readFileSync(dist, 'utf8');
  assert.equal(/(src|href)\s*=\s*["']https?:\/\//i.test(html), false, 'the build references a remote URL');
});

test('step 02: start a new game (fresh level-1 player in town with all three starter weapons, no stopgap)', async () => {
  const g = S.g;
  g.game.on(g.NAMES.KILL, (e) => S.kills.push(e));
  g.game.on(g.NAMES.LEVEL_UP, (e) => S.levelUps.push(e.level));
  g.game.on(g.NAMES.ITEM_DROPPED, (e) => S.drops.push(e));
  g.game.on(g.NAMES.PICKUP, (e) => S.pickups.push(e));
  // Deliberately NOT g.newGame(): that helper grants a weapon. This is the real call.
  const r = g.game.newGame({ seed: 'acceptance-28', difficulty: 'normal' });
  assert.equal(r.ok, true, JSON.stringify(r));
  const w = g.game.world, p = w.player;
  assert.equal(g.game.state, 'playing');
  assert.equal(w.area.id, 'town');
  assert.equal(p.level, 1);
  assert.equal(p.xp, 0);
  assert.equal(w.difficulty.id, 'normal');
  assert.equal(JSON.stringify(p.weaponSlots.map((s) => s && s.baseId)), JSON.stringify(['sword', 'hunting-bow', 'arc-staff']));
  assert.equal(p.hp, p.stats.maxHp);
});

test('step 03: move around a 2D world (position follows the move intent and walls stop it)', async () => {
  const g = S.g, p = g.game.world.player;
  const x0 = p.x, y0 = p.y;
  g.drive(60, () => ({ ...IDLE(), move: { x: 0, y: 1 }, aim: { x: p.x, y: p.y + 10 } }));
  assert.ok(Math.abs(p.y - y0) > 20, `player did not move south (dy=${p.y - y0})`);
  g.drive(60, () => ({ ...IDLE(), move: { x: 1, y: 0 }, aim: { x: p.x + 10, y: p.y } }));
  assert.ok(Math.abs(p.x - x0) > 20, `player did not move east (dx=${p.x - x0})`);
  // Walk a long way in one direction: collision keeps the player inside the map.
  g.drive(1200, () => ({ ...IDLE(), move: { x: -1, y: 0 }, aim: { x: p.x - 10, y: p.y } }));
  const map = g.game.world.area.map;
  assert.ok(p.x > 0 && p.x < map.w * map.tileSize, 'player left the map');
  assert.equal(g.game.stepErrors, 0);
});

test('step 04: fight monsters (the real attack kills monsters in the forest)', async () => {
  const g = S.g;
  g.RPG.world.areas.enter(g.game.world, 'forest');
  const kills0 = S.kills.length;
  g.drive(TICKS_60S, godBot('acc-fight'));
  assert.equal(g.game.stepErrors, 0);
  assert.ok(S.kills.length > kills0, 'no monster died in 60 s of fighting');
  assert.equal(g.game.world.progress.kills, S.kills.length);
});

test('step 05: use different weapons (switch through all three slots; each one hurts a monster)', async () => {
  const g = S.g, w = g.game.world, p = w.player;
  const seen = [];
  for (let i = 0; i < 3; i++) {
    // Cycle with the real switch intent until the target slot is active.
    for (let tries = 0; tries < 4 && p.activeWeapon !== i; tries++) g.drive(40, () => ({ ...IDLE(), switchWeapon: 1 }));
    g.drive(40, IDLE); // let the swap settle
    assert.equal(p.activeWeapon, i, `could not switch to slot ${i}`);
    seen.push(p.weaponSlots[i].baseId);
    // Attack the nearest monster from point-blank range; the weapon must lower its HP (or kill it).
    const m = standNextToMonster(g);
    const hp0 = m.hp, id = m.id, kills0 = w.progress.kills;
    g.drive(180, (game) => { p.hp = p.stats.maxHp; const t = nearestLiving(game.world); return { ...IDLE(), aim: { x: t.x, y: t.y }, attack: true }; });
    const after = w.monsters.find((x) => x.id === id && !x.dead);
    assert.ok(!after || after.hp < hp0 || w.progress.kills > kills0, `${p.weaponSlots[i].baseId} dealt no damage`);
  }
  assert.deepEqual(seen, ['sword', 'hunting-bow', 'arc-staff']);
  assert.equal(g.game.stepErrors, 0);
});

test('step 06: use offensive, defensive and regenerative skills (learn with earned points, slot, cast)', async () => {
  const g = S.g, w = g.game.world, p = w.player, SK = g.RPG.world.skills;
  // Fight on for real XP: the forest alone supplies enough to bank three points (level 4, the caves gate).
  g.RPG.world.areas.enter(w, 'forest');
  for (let i = 0; i < 10 && p.level < 4; i++) g.drive(TICKS_60S, godBot('acc-pts' + i));
  // The forest's real kills clear the caves gate (level 4); no top-up is needed.
  assert.ok(p.level >= 4, `forest play only reached level ${p.level} (xp ${p.xp}, kills ${w.progress.kills}, alive ${w.monsters.filter((m) => !m.dead).length}, cleared ${JSON.stringify(w.cleared)})`);
  assert.equal(g.RPG.world.areas.isUnlocked(w, 'caves'), true, 'caves gate still closed after the forest');
  assert.ok(p.skills.points >= 3, `only ${p.skills.points} skill points earned from play so far (level ${p.level})`);
  for (const id of ['fireball', 'shield', 'mend']) {
    assert.equal(SK.learnSkill(w, p, id), true, `could not learn ${id}`);
  }
  assert.equal(SK.equipSkill(p, 'fireball', 0), true);
  assert.equal(SK.equipSkill(p, 'shield', 1), true);
  assert.equal(SK.equipSkill(p, 'mend', 2), true);
  const cast = (slot) => {
    p.resource = p.stats.maxResource;
    const t = nearestLiving(w) || p;
    const used = [];
    const off = g.game.on(g.NAMES.SKILL_USED, (e) => used.push(e.skillId));
    const skills = []; skills[slot] = true;
    g.drive(1, () => ({ ...IDLE(), aim: { x: t.x, y: t.y }, skills }));
    off && off();
    return used;
  };
  // Offensive: fireball at a monster in front of the player damages it.
  const m = standNextToMonster(g);
  const hp0 = m.hp, id = m.id;
  assert.deepEqual(cast(0), ['fireball']);
  g.drive(90, IDLE);
  const mAfter = w.monsters.find((x) => x.id === id && !x.dead);
  assert.ok(!mAfter || mAfter.hp < hp0, 'fireball did no damage');
  // Defensive: shield puts a damage-absorbing status on the player.
  assert.deepEqual(cast(1), ['shield']);
  assert.ok(p.status && p.status.shield && p.status.shield.strength > 0, 'shield status missing');
  // Regenerative: mend raises HP of a wounded player over time.
  p.hp = Math.floor(p.stats.maxHp / 2);
  const hpLow = p.hp;
  assert.deepEqual(cast(2), ['mend']);
  g.drive(180, IDLE);
  assert.ok(p.hp > hpLow, `mend did not heal (hp ${hpLow} -> ${p.hp})`);
  assert.equal(g.game.stepErrors, 0);
});

test('step 07: gain XP (every kill paid out XP and the player total equals the payouts)', async () => {
  const g = S.g, p = g.game.world.player, P = g.RPG.world.progression;
  assert.ok(S.kills.length > 0);
  assert.ok(S.kills.every((k) => k.xp > 0), 'a kill paid no XP');
  const paid = S.kills.reduce((s, k) => s + k.xp, 0);
  assert.ok(paid > 0);
  assert.equal(P.totalXpForLevel(p.level) + p.xp, paid, 'player XP is not the sum of the kill payouts');
});

test('step 08: level up (LEVEL_UP events match the level, stats grow, HP refilled)', async () => {
  const g = S.g, p = g.game.world.player;
  assert.ok(S.levelUps.length >= 1, 'no LEVEL_UP event so far');
  assert.equal(p.level, 1 + S.levelUps.length, 'level and LEVEL_UP count disagree');
  assert.deepEqual(S.levelUps, S.levelUps.map((_, i) => i + 2), 'levels did not rise one by one');
  const base = new g.RPG.world.StatBlock().snapshot().maxHp;
  assert.ok(p.stats.maxHp > base, `maxHp ${p.stats.maxHp} did not grow past the level-1 base ${base}`);
});

test('step 09: spend skill points (an upgrade and a second rank cost points, balance stays consistent)', async () => {
  const g = S.g, w = g.game.world, p = w.player, SK = g.RPG.world.skills, st = p.skills;
  // Cave kills supply the rest: two spare points beyond the three spent in step 06 (level 6, the arena gate).
  for (let i = 0; i < 20 && p.level < 6; i++) {
    g.RPG.world.areas.enter(w, i % 2 === 0 ? 'caves' : 'forest'); // slots left partly cleared refill on re-entry
    g.drive(TICKS_60S, godBot('acc-caves' + i));
  }
  assert.ok(st.points >= 2, `only ${st.points} points (level ${p.level}, xp ${p.xp}, kills ${w.progress.kills}, alive ${w.monsters.filter((m) => !m.dead).length})`);
  const total = st.points + st.spentPoints;
  const before = st.points;
  assert.equal(SK.upgradeSkill(w, p, 'fireball', 'bigger-explosion'), true, 'upgrade refused');
  assert.equal(st.points, before - 1);
  assert.equal(SK.learnSkill(w, p, 'fireball'), true, 'rank 2 refused');
  assert.equal(SK.getSkillRank(p, 'fireball'), 2);
  assert.equal(st.points + st.spentPoints, total, 'points were created or lost');
  // With no points left a further spend is refused.
  while (st.points > 0) assert.equal(SK.learnSkill(w, p, 'mend'), true);
  assert.equal(SK.learnSkill(w, p, 'fireball'), false);
});

test('step 10: obtain and equip loot (a real drop is picked up and equipping it changes stats)', async () => {
  const g = S.g, w = g.game.world, p = w.player, INV = g.RPG.ui.inventory;
  const equippable = () => p.inventory.findIndex((it) => it && g.RPG.world.equipment.isEquippable(it) && g.RPG.data.items[it.baseId].slot !== 'weapon');
  for (let i = 0; i < 20 && equippable() < 0; i++) { g.RPG.world.areas.enter(w, 'forest'); g.drive(TICKS_60S, godBot('acc-loot' + i)); }
  assert.ok(S.drops.length > 0, 'nothing ever dropped');
  assert.ok(S.pickups.some((e) => e.kind === 'item'), 'no item was picked up');
  const idx = equippable();
  assert.ok(idx >= 0, 'no equippable item in the inventory after 20 minutes of play');
  const item = p.inventory[idx];
  const slot = g.RPG.data.items[item.baseId].slot;
  const stats0 = JSON.stringify(p.stats);
  const r = INV.equipFromSlot(p, idx);
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(p.equipment.slots ? p.equipment.slots[slot].uid : p.equipment[slot].uid, item.uid);
  assert.notEqual(JSON.stringify(p.stats), stats0, 'equipping changed no stat');
});

test('step 11: fight increasingly difficult enemies (monster level and HP rise from forest to caves to arena)', async () => {
  const g = await scratchGame('normal'), w = g.game.world, A = g.RPG.world.areas;
  const hp = {}, lvl = {};
  for (const id of ['forest', 'caves']) {
    A.enter(w, id);
    assert.equal(w.area.id, id);
    const l = livingRegular(w);
    assert.ok(l.length > 0, `${id} has no regular monsters`);
    hp[id] = l.reduce((s, m) => s + m.maxHp, 0) / l.length;
    lvl[id] = l.reduce((s, m) => s + m.level, 0) / l.length;
  }
  assert.ok(lvl.caves > lvl.forest, JSON.stringify(lvl));
  assert.ok(hp.caves > hp.forest, JSON.stringify(hp));
  // The arena is the boss encounter: its boss out-levels and out-HPs every cave monster.
  A.enter(w, 'arena');
  const boss = w.monsters.find((m) => m.defId === BOSS_ID);
  assert.ok(boss, 'no boss in the arena');
  assert.ok(boss.level > lvl.caves && boss.maxHp > hp.caves, `boss ${boss.level}/${boss.maxHp} vs caves ${lvl.caves}/${hp.caves}`);
});

test('step 12: defeat a boss (warden-of-ash dies, is recorded as defeated and drops loot)', async () => {
  const g = S.g, w = g.game.world;
  g.RPG.world.areas.enter(w, 'arena');
  assert.ok(w.monsters.some((m) => m.defId === BOSS_ID && !m.dead), 'no boss in the arena');
  const bot = godBot('acc-boss');
  let t = 0;
  for (; t < 20 * TICKS_60S && !w.progress.bossesDefeated[BOSS_ID]; t++) g.drive(1, bot);
  assert.equal(g.game.stepErrors, 0);
  assert.ok(w.progress.bossesDefeated[BOSS_ID], `boss not defeated in ${t} ticks`);
  assert.ok(S.kills.some((k) => k.boss), 'no KILL event flagged boss');
  // Killing the final boss opens the victory screen and halts the sim until the player picks "continue".
  assert.equal(g.game.state, 'victory');
  g.game.continuePlaying();
  assert.equal(g.game.state, 'playing');
});

test('step 13: change difficulty (the running game switches, bad ids are refused, spawns get tougher)', async () => {
  const g = S.g, w = g.game.world;
  assert.equal(g.game.setDifficulty('hard').ok, true);
  assert.equal(w.difficulty.id, 'hard');
  assert.equal(g.game.setDifficulty('not-a-difficulty').ok, false);
  assert.equal(w.difficulty.id, 'hard', 'a rejected difficulty changed the world');
  // Spawn scaling, measured on a throwaway game so the main session's cleared forest does not matter.
  const mean = async (d) => {
    const s = await scratchGame('normal');
    assert.equal(s.game.setDifficulty(d).ok, true);
    s.RPG.world.areas.enter(s.game.world, 'forest');
    const l = livingRegular(s.game.world);
    return l.reduce((a, m) => a + m.maxHp, 0) / l.length;
  };
  const normalHp = await mean('normal'), hardHp = await mean('hard');
  assert.ok(hardHp > normalHp, `hard monsters (${hardHp}) are not tougher than normal (${normalHp})`);
});

test('step 14: save the game (slot 1 written, snapshot captured)', async () => {
  const g = S.g;
  g.RPG.world.areas.enter(g.game.world, "town");
  // An attack swing in progress is transient combat state (like projectiles and monsters) and is not saved; let it finish
  // so the save is taken at rest, as a player would by opening the menu in town.
  g.drive(90, IDLE);
  assert.equal(g.game.world.player.attack.state, 'idle');
  const r = g.game.saveToSlot('1');
  assert.equal(r.ok, true, JSON.stringify(r));
  S.savedText = snapshotText(g);
  assert.ok(g.backend.getItem && S.backend === g.backend);
});


test('step 15: close the browser (the whole engine realm is dropped; only storage survives)', async () => {
  S.old = S.g; // kept only so step 18 can compare the original's continuation; the new game never touches it
  S.g = null;
  assert.ok(S.backend.getItem('rpg.nonexistent') === null);
});

test('step 16: reopen with no internet (a fresh engine boots from nothing but the stored data)', async () => {
  const g = await makeGame({ backend: S.backend });
  S.g = g;
  assert.notEqual(g.RPG, S.old.RPG, 'the reopened game must be a new engine realm');
  assert.equal(g.game.state, 'mainmenu');
  assert.equal(g.game.world, null);
  const slots = g.RPG.persistence.listSlots ? g.RPG.persistence.listSlots(g.storage) : null;
  if (slots) assert.ok(JSON.stringify(slots).includes('1'));
});

test('step 17: load the previous game (state is byte-equal to what was saved)', async () => {
  const g = S.g;
  const r = g.game.loadFromSlot('1');
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(g.game.state, 'playing');
  assert.equal(snapshotText(g), S.savedText, 'loaded state differs from the saved state');
  assert.equal(g.game.world.difficulty.id, 'hard');
  assert.equal(g.game.world.player.level, S.old.game.world.player.level);
  assert.equal(JSON.stringify(g.game.world.player.weaponSlots.map((s) => s && s.baseId)), JSON.stringify(['sword', 'hunting-bow', 'arc-staff']));
});

test('step 18: continue playing exactly where they left off (same walk from the same save gives the same saved player)', async () => {
  const g = S.g;
  // The old game and the reloaded one are in town at the same moment; the same walk must lead to the same player.
  assert.equal(S.old.game.world.area.id, 'town');
  assert.equal(g.game.world.area.id, 'town');
  S.old.drive(600, makeWalker('acc-continue'));
  g.drive(600, makeWalker('acc-continue'));
  assert.equal(g.game.stepErrors, 0);
  // Position and velocity are saved, so the continuation matches the original exactly (no tolerance).
  const A = JSON.parse(savedPlayerText(g)), B = JSON.parse(savedPlayerText(S.old));
  assert.equal(JSON.stringify(canonical(A)), JSON.stringify(canonical(B)), 'the restored game diverged from the original');
  // And it is still a game: the restored player can go fight and kill.
  g.RPG.world.areas.enter(g.game.world, 'forest');
  const k0 = g.game.world.progress.kills;
  g.drive(TICKS_60S, godBot('acc-after-load'));
  assert.ok(g.game.world.progress.kills > k0, 'restored player could not fight');
});

test('step 19: export the save (human-readable JSON file text with a .json filename)', async () => {
  const g = S.g;
  assert.equal(g.game.saveToSlot('1').ok, true);
  S.savedText = snapshotText(g);
  const e = g.game.exportSave('current');
  assert.equal(e.ok, true, JSON.stringify(e));
  assert.match(e.filename, /\.json$/);
  const parsed = JSON.parse(e.text);
  assert.equal(typeof parsed, 'object');
  S.exported = e;
});

test('step 20: delete the local save (slot gone, loading it fails cleanly)', async () => {
  const g = S.g;
  assert.equal(g.game.deleteSave('1').ok, true);
  const r = g.game.loadFromSlot('1');
  assert.equal(r.ok, false, 'a deleted save still loads');
});

test('step 21: import the exported save (restores the exact exported state)', async () => {
  const g = S.g;
  const r = g.game.importSave(S.exported.text, '1');
  assert.equal(r.ok, true, JSON.stringify(r));
  assert.equal(snapshotText(g), S.savedText, 'imported state differs from the exported state');
  assert.equal(g.game.world.difficulty.id, 'hard');
});

test('step 22: continue playing from the restored state (the imported game fights, levels and saves again)', async () => {
  const g = S.g, w = g.game.world, p = w.player;
  assert.equal(g.game.state, 'playing');
  const xp0 = p.xp, lvl0 = p.level, k0 = w.progress.kills;
  g.RPG.world.areas.enter(w, 'forest');
  // The forest was cleared earlier in the run, so few monsters respawn and the bot walks straight at the nearest one even
  // through a wall: start it beside a monster it can see.
  standNextToMonster(g);
  g.drive(TICKS_60S, godBot('acc-final'));
  assert.equal(g.game.stepErrors, 0);
  assert.ok(w.progress.kills > k0, 'no kills after import');
  assert.ok(p.level > lvl0 || p.xp > xp0, 'no XP after import');
  assert.equal(g.game.saveToSlot('1').ok, true, 'could not save after import');
});

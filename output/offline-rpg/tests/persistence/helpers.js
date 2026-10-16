'use strict';
// Loads the persistence files into a fresh vm context with only {RPG:{}}-style globals (headless rule).
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', '..', 'src', 'persistence');
const FILES = ['migrate.js', 'save.js', 'storage.js', 'exportimport.js'];

function load(extraGlobals) {
  const ctx = vm.createContext(Object.assign({ RPG: {} }, extraGlobals || {}));
  ctx.globalThis = ctx;
  for (const f of FILES) vm.runInContext(fs.readFileSync(path.join(SRC, f), 'utf8'), ctx, { filename: f });
  return ctx.RPG.persistence;
}

function item(uid, extra) {
  return Object.assign({ uid, baseId: 'sword-iron', ilvl: 5, rarity: 'rare', name: 'Iron Sword', mods: [{ id: 'm1', stat: 'damage', kind: 'flat', value: 3.5 }], seedTag: 's' + uid }, extra);
}

function sampleState() {
  const inv = Array(24).fill(null);
  inv[0] = item(10); inv[5] = item(11, { qty: 3 });
  return {
    gameVersion: '1.0.0', savedAt: '2026-10-16T12:00:00.000Z', seed: 123456789,
    rngState: { world: 987654321, loot: 1122334455 },
    meta: { level: 7, area: 'forest', difficulty: 'normal', playtime: 1834.5, name: 'Wayfarer' },
    difficulty: { id: 'normal', enemyHp: 1, enemyDamage: 1 },
    player: {
      classId: 'wayfarer', level: 7, xp: 340, gold: 512, attrs: { might: 8, agility: 6, spirit: 5 }, hp: 96.5, resource: 41,
      pos: { x: 1204, y: 880.5 }, weaponSlots: [item(1), null, null], activeWeapon: 0,
      equipment: { head: null, chest: item(2) }, inventory: inv,
      skills: { learned: { fireball: 2 }, upgrades: { fireball: { 'bigger-explosion': 1 } }, bar: ['fireball', null, null, null, null, null, null], points: 3, spentPoints: 4 }
    },
    area: { id: 'forest', visited: { town: true, forest: true }, cleared: { forest: ['slot-3'] } },
    progress: { bossesDefeated: {}, areasUnlocked: { town: true, forest: true }, kills: 211, deaths: 2 },
    settings: { volume: 0.6, muted: false, keys: { skills: ['KeyK'] } }
  };
}

// localStorage-shaped backend whose writes can be made to fail (optionally only after N successful writes, or only for some keys).
function flakyBackend() {
  const m = new Map();
  const b = {
    map: m, failAll: false, failKey: null, failAfter: Infinity, writes: 0,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem(k, v) {
      if (b.failAll || (b.failKey && k.includes(b.failKey)) || b.writes >= b.failAfter) throw new Error('QuotaExceededError');
      b.writes++; m.set(k, String(v));
    },
    removeItem: (k) => { m.delete(k); }
  };
  return b;
}

module.exports = { load, sampleState, item, flakyBackend };

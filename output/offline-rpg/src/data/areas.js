/**
 * Area definitions (DATA-DEFINITIONS §7). Pure data, registered on RPG.data.areas keyed by id.
 *
 * Four v1 areas in escalating order: Town (authored, safe) -> Whispering Forest (open) -> Hollow Caves (cave)
 * -> Boss Arena (authored, holds the Warden of Ash). Levels rise strictly along `order`, both ends.
 *
 * `spawnTable` entries: `min`/`max` bound how many of that monster one encounter may roll; `weight` is the
 * selection weight. Authored layouts use the glyphs of RPG.world.tilemap.DEFAULT_GLYPHS ('S' is the spawn).
 *
 * `RPG.data.areas.validate(areas?, monsters?)` returns a list of problems (empty means valid). It is attached
 * non-enumerable so iterating the map still yields definitions only.
 */
(function (RPG) {
  'use strict';

  RPG.data = RPG.data || {};

  const freeze = RPG.core.util.deepFreeze;

  const THEMES = ['town', 'forest', 'caves', 'ruins', 'dungeon', 'arena'];
  const KINDS = ['authored', 'cave', 'rooms', 'open'];
  // Glyphs an authored layout may use; must stay in step with RPG.world.tilemap.DEFAULT_GLYPHS.
  const LAYOUT_GLYPHS = '.#~+>S,';
  const PALETTE_KEYS = ['floor', 'floorAlt', 'wall', 'accent', 'ambient'];
  const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
  const BOSS_SLOT = 'arena-center';

  const isInt = (v) => Number.isInteger(v);
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

  // Structural and cross-reference checks. Monster and area lookups default to the registered data.
  function validate(areas, monsters) {
    areas = areas || RPG.data.areas;
    monsters = monsters || RPG.data.monsters;
    const errors = [];
    const err = (msg) => errors.push(msg);

    for (const key of Object.keys(areas)) {
      const def = areas[key];
      const where = `area ${key}`;
      if (def.id !== key) err(`${where}: id field is '${def.id}'`);
      if (typeof def.name !== 'string' || def.name.length === 0) err(`${where}: name`);
      if (!isInt(def.order) || def.order < 0) err(`${where}: order must be a non-negative int`);
      if (!THEMES.includes(def.theme)) err(`${where}: theme '${def.theme}'`);
      if (!KINDS.includes(def.kind)) err(`${where}: kind '${def.kind}'`);
      if (typeof def.safe !== 'boolean') err(`${where}: safe must be boolean`);

      const level = def.level || [];
      if (level.length !== 2 || !isInt(level[0]) || !isInt(level[1]) || level[0] < 1 || level[0] > level[1]) {
        err(`${where}: level must be [min, max] ints with 1 <= min <= max`);
      }

      const size = def.size || [];
      if (size.length !== 2 || !isInt(size[0]) || !isInt(size[1]) || size[0] < 1 || size[1] < 1) {
        err(`${where}: size must be [w, h] positive ints`);
      }

      if (def.kind === 'authored') {
        validateLayout(def, where, err);
      } else if (def.layout !== undefined) {
        err(`${where}: only authored areas carry a layout`);
      }

      if (!isNum(def.budget) || def.budget < 0) err(`${where}: budget must be a non-negative number`);
      if (!isNum(def.eliteChance) || def.eliteChance < 0 || def.eliteChance > 1) err(`${where}: eliteChance in [0, 1]`);

      if (!Array.isArray(def.spawnTable)) err(`${where}: spawnTable must be an array`);
      else if (def.safe && def.spawnTable.length > 0) err(`${where}: safe area must not spawn monsters`);
      for (const entry of def.spawnTable || []) {
        const monster = monsters[entry.monsterId];
        if (!monster) {
          err(`${where}: spawnTable names unknown monster '${entry.monsterId}'`);
        } else if (!(monster.areas || []).includes(key)) {
          err(`${where}: monster '${entry.monsterId}' is not allowed in this area`);
        }
        if (!isNum(entry.weight) || entry.weight <= 0) err(`${where}: spawn weight for '${entry.monsterId}'`);
        if (!isInt(entry.min) || !isInt(entry.max) || entry.min < 0 || entry.min > entry.max) {
          err(`${where}: spawn min/max for '${entry.monsterId}'`);
        }
      }

      if (def.boss !== undefined) {
        const boss = monsters[def.boss.bossId];
        if (!boss || boss.archetype !== 'boss') err(`${where}: boss '${def.boss.bossId}' is not a boss monster`);
        if (def.boss.slot !== BOSS_SLOT) err(`${where}: boss slot must be '${BOSS_SLOT}'`);
      }

      if (!def.palette || PALETTE_KEYS.some((k) => !HEX_COLOR.test(def.palette[k] || ''))) {
        err(`${where}: palette needs hex colours for ${PALETTE_KEYS.join(', ')}`);
      }
      for (const d of def.decor || []) {
        if (typeof d.kind !== 'string' || !isNum(d.density) || d.density < 0 || d.density > 1) {
          err(`${where}: decor entry ${JSON.stringify(d)}`);
        }
      }

      for (const exit of def.exits || []) {
        if (!areas[exit.to]) err(`${where}: exit to unknown area '${exit.to}'`);
        if (exit.to === key) err(`${where}: exit points back to itself`);
        const req = exit.requires || {};
        if (req.level !== undefined && (!isInt(req.level) || req.level < 1)) err(`${where}: exit level requirement`);
        for (const bossId of req.bossesDefeated || []) {
          if (!monsters[bossId] || monsters[bossId].archetype !== 'boss') {
            err(`${where}: exit requires unknown boss '${bossId}'`);
          }
        }
      }
    }

    // Escalation: along `order`, both ends of the level range must strictly rise.
    const ordered = Object.values(areas).sort((a, b) => a.order - b.order);
    ordered.slice(1).forEach((cur, i) => {
      const prev = ordered[i];
      if (!(cur.level[0] > prev.level[0] && cur.level[1] > prev.level[1])) {
        err(`area ${cur.id}: level ${cur.level} does not escalate from ${prev.id} ${prev.level}`);
      }
    });

    return errors;
  }

  // Authored layouts: rectangular, match `size`, use only known glyphs, contain a spawn, and a boss sits on a
  // passable centre tile.
  function validateLayout(def, where, err) {
    const rows = def.layout;
    if (!Array.isArray(rows) || rows.length === 0) {
      err(`${where}: authored area needs a layout`);
      return;
    }
    const w = rows[0].length;
    rows.forEach((row, y) => {
      if (row.length !== w) err(`${where}: layout row ${y} has ${row.length} columns, expected ${w}`);
      for (const ch of row) {
        if (!LAYOUT_GLYPHS.includes(ch)) err(`${where}: layout glyph '${ch}' at row ${y}`);
      }
    });
    if (def.size && (def.size[0] !== w || def.size[1] !== rows.length)) {
      err(`${where}: size ${def.size} does not match layout ${w}x${rows.length}`);
    }
    if (!rows.some((row) => row.includes('S'))) err(`${where}: layout has no spawn tile 'S'`);
    if (def.boss !== undefined && rows[Math.floor(rows.length / 2)]) {
      const centre = rows[Math.floor(rows.length / 2)][Math.floor(w / 2)];
      if (centre === '#') err(`${where}: boss centre tile is a wall`);
    }
  }

  const defs = [
    {
      id: 'town', name: 'Hollow Hearth', order: 0, level: [1, 2], theme: 'town', kind: 'authored', safe: true,
      size: [36, 18],
      layout: [
        '####################################',
        '#..................................#',
        '#..####.........,,.........#####...#',
        '#..#..#...............,,...#...#...#',
        '#..#..#....................#...#...#',
        '#..####....................#####...#',
        '#..................................#',
        '#..................................>',
        '#..................................#',
        '#.........,,.......,,..............#',
        '#..................................#',
        '#.....S............................#',
        '#..................................#',
        '#..................................#',
        '#..................................#',
        '#..................................#',
        '#..................................#',
        '####################################',
      ],
      spawnTable: [],
      budget: 0,
      eliteChance: 0,
      exits: [{ to: 'forest' }],
      palette: { floor: '#3a4a3f', floorAlt: '#40533f', wall: '#4a4540', accent: '#c9a86a', ambient: '#1b1f1c' },
      decor: [{ kind: 'flowers', density: 0.04 }, { kind: 'lantern', density: 0.02 }],
    },
    {
      id: 'forest', name: 'Whispering Forest', order: 1, level: [2, 4], theme: 'forest', kind: 'open', safe: false,
      size: [40, 30],
      spawnTable: [
        { monsterId: 'husk-grunt', weight: 10, min: 1, max: 4 },
        { monsterId: 'skitter', weight: 8, min: 2, max: 5 },
        { monsterId: 'bone-archer', weight: 4, min: 1, max: 2 },
      ],
      budget: 40,
      eliteChance: 0.08,
      exits: [{ to: 'town' }, { to: 'caves', requires: { level: 4 } }],
      palette: { floor: '#2c3b2f', floorAlt: '#30412f', wall: '#2a3a33', accent: '#8fb573', ambient: '#141a16' },
      decor: [{ kind: 'tuft', density: 0.3 }, { kind: 'stone', density: 0.05 }],
    },
    {
      id: 'caves', name: 'Hollow Caves', order: 2, level: [4, 6], theme: 'caves', kind: 'cave', safe: false,
      size: [48, 36],
      spawnTable: [
        { monsterId: 'skitter', weight: 6, min: 2, max: 5 },
        { monsterId: 'bone-archer', weight: 5, min: 1, max: 2 },
        { monsterId: 'stone-brute', weight: 4, min: 1, max: 1 },
        { monsterId: 'cinder-bomb', weight: 5, min: 1, max: 3 },
        { monsterId: 'hexcaller', weight: 3, min: 1, max: 1 },
      ],
      budget: 60,
      eliteChance: 0.12,
      exits: [{ to: 'forest' }, { to: 'arena', requires: { level: 6 } }],
      palette: { floor: '#2e2b33', floorAlt: '#34303a', wall: '#1f1d24', accent: '#7d8aa0', ambient: '#0f0f14' },
      decor: [{ kind: 'crystal', density: 0.04 }, { kind: 'bones', density: 0.06 }],
    },
    {
      id: 'arena', name: 'Ashen Arena', order: 3, level: [6, 8], theme: 'arena', kind: 'authored', safe: false,
      size: [24, 18],
      layout: [
        '########################',
        '#......................#',
        '#..##..............##..#',
        '#..##..............##..#',
        '#......................#',
        '#......................#',
        '#......................#',
        '#......................#',
        '#......................#',
        '#......................#',
        '#......................#',
        '#......................#',
        '#......................#',
        '#..##..............##..#',
        '#..##..............##..#',
        '#......................#',
        '#...........S..........#',
        '###########>###########.',
      ],
      boss: { bossId: 'warden-of-ash', slot: BOSS_SLOT },
      spawnTable: [],
      budget: 0,
      eliteChance: 0,
      exits: [{ to: 'caves' }],
      palette: { floor: '#3a2a26', floorAlt: '#44302a', wall: '#2a1f1c', accent: '#e0a458', ambient: '#1a1210' },
      decor: [{ kind: 'ash', density: 0.2 }, { kind: 'brazier', density: 0.02 }],
    },
  ];

  const areas = {};
  for (const def of defs) areas[def.id] = freeze(def);
  // Non-enumerable so `Object.values(RPG.data.areas)` yields definitions only.
  Object.defineProperty(areas, 'validate', { value: validate, enumerable: false });
  RPG.data.areas = Object.freeze(areas);
})(globalThis.RPG = globalThis.RPG || {});

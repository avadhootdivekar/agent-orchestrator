/* StatBlock: base stats plus modifiers keyed by source (DATA-DEFINITIONS §1). Headless: no DOM.
   derived[s] = (base[s] + Σ flat) * (1 + Σ inc) * Π (1 + more_i), then clamped to the stat's [min, max].
   Modifier = { source, stat, kind: 'flat'|'inc'|'more', value }; 'percent' is accepted as an alias of 'inc'. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var KINDS = { flat: 'flat', inc: 'inc', percent: 'inc', more: 'more' };

  // Level-1 bases and caps from DATA-DEFINITIONS §1. A stat without an entry here has base 0 and no cap, so the
  // model stays extensible; `max: undefined` means uncapped, and every listed stat floors at `min` (default 0).
  var STAT_DEFS = {
    maxHp: { base: 100 }, maxResource: { base: 60 }, damage: { base: 4 },
    attackSpeed: { base: 1, min: 0.1 }, moveSpeed: { base: 170, max: 320 }, armor: { base: 5 },
    critChance: { base: 0.05, max: 0.75 }, critDamage: { base: 1.5 }, lifeRegen: { base: 1 }, resourceRegen: { base: 6 },
    cooldownReduction: { base: 0, max: 0.5 }, lifeSteal: { base: 0, max: 0.25 }, skillDamage: { base: 0 },
    resFire: { base: 0, max: 0.75 }, resCold: { base: 0, max: 0.75 }, resLightning: { base: 0, max: 0.75 },
    resPoison: { base: 0, max: 0.75 }, resArcane: { base: 0, max: 0.75 }, thorns: { base: 0 },
    pickupRadius: { base: 40 }, goldFind: { base: 0 }, xpGain: { base: 0 }
  };

  function defaultBase() {
    var b = {};
    for (var k in STAT_DEFS) b[k] = STAT_DEFS[k].base;
    return b;
  }

  function checkNumber(v, what) {
    if (typeof v !== 'number' || !isFinite(v)) throw new TypeError('StatBlock: ' + what + ' must be a finite number');
  }

  function clampStat(stat, v) {
    var d = STAT_DEFS[stat];
    if (!d) return v;
    var lo = d.min === undefined ? 0 : d.min;
    if (v < lo) v = lo;
    if (d.max !== undefined && v > d.max) v = d.max;
    return v;
  }

  /** `baseOverrides` (partial StatMap) replaces the level-1 defaults, e.g. a class's baseStats or a monster's. */
  function StatBlock(baseOverrides) {
    this.base = defaultBase();
    this.mods = [];
    if (baseOverrides) for (var k in baseOverrides) this.setBase(k, baseOverrides[k]);
  }

  StatBlock.prototype.setBase = function (stat, value) {
    checkNumber(value, 'base value for ' + stat);
    this.base[stat] = value;
  };

  /** Adds a modifier; returns the stored copy. */
  StatBlock.prototype.addModifier = function (mod) {
    var kind = KINDS[mod && mod.kind];
    if (!kind) throw new TypeError('StatBlock: unknown modifier kind ' + (mod && mod.kind));
    if (!mod.source || typeof mod.source !== 'string') throw new TypeError('StatBlock: modifier needs a string source');
    if (!mod.stat || typeof mod.stat !== 'string') throw new TypeError('StatBlock: modifier needs a stat key');
    checkNumber(mod.value, 'modifier value');
    var m = { source: mod.source, stat: mod.stat, kind: kind, value: mod.value };
    this.mods.push(m);
    return m;
  };

  /** Removes every modifier from exactly `source` (no prefix matching). Returns how many were removed. */
  StatBlock.prototype.removeModifiersBySource = function (source) {
    var before = this.mods.length;
    this.mods = this.mods.filter(function (m) { return m.source !== source; });
    return before - this.mods.length;
  };

  StatBlock.prototype.get = function (stat) {
    var flat = 0, inc = 0, more = 1;
    for (var i = 0; i < this.mods.length; i++) {
      var m = this.mods[i];
      if (m.stat !== stat) continue;
      if (m.kind === 'flat') flat += m.value;
      else if (m.kind === 'inc') inc += m.value;
      else more *= 1 + m.value;
    }
    var b = this.base[stat] === undefined ? 0 : this.base[stat];
    return clampStat(stat, (b + flat) * (1 + inc) * more);
  };

  /** Plain object of every derived stat (all known stats plus any with a base or modifier). */
  StatBlock.prototype.snapshot = function () {
    var out = {}, k, i;
    for (k in this.base) out[k] = this.get(k);
    for (i = 0; i < this.mods.length; i++) if (!(this.mods[i].stat in out)) out[this.mods[i].stat] = this.get(this.mods[i].stat);
    return out;
  };

  StatBlock.prototype.serialize = function () {
    return {
      base: Object.assign({}, this.base),
      mods: this.mods.map(function (m) { return { source: m.source, stat: m.stat, kind: m.kind, value: m.value }; })
    };
  };

  StatBlock.restore = function (data) {
    var sb = new StatBlock();
    sb.base = Object.assign({}, data.base);
    sb.mods = [];
    for (var i = 0; i < data.mods.length; i++) sb.addModifier(data.mods[i]);
    return sb;
  };

  RPG.world.StatBlock = StatBlock;
  RPG.world.stats = { StatBlock: StatBlock, STAT_DEFS: STAT_DEFS, clampStat: clampStat };
})(globalThis.RPG = globalThis.RPG || {});

// Save serialization: serialize / deserialize / validate per docs/SAVE-SCHEMA.md. Pure data in, pure data out; no DOM, no storage.
// `state` is the save body (everything except format/version/checksum). Building it from a World is the caller's job.
(function (RPG) {
  'use strict';
  var P = RPG.persistence = RPG.persistence || {};
  var FORMAT = 'offline-rpg-save';
  var MAX_BYTES = 2 * 1024 * 1024; // import cap from SAVE-SCHEMA.md
  var KNOWN = ['format', 'version', 'gameVersion', 'savedAt', 'seed', 'rngState', 'checksum', 'meta', 'difficulty',
    'player', 'area', 'progress', 'settings'];
  var ORDER = ['format', 'version', 'gameVersion', 'savedAt', 'seed', 'rngState', 'checksum', 'meta', 'difficulty',
    'player', 'area', 'progress', 'settings'];

  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function isInt(v) { return isNum(v) && Math.floor(v) === v; }
  function isStr(v) { return typeof v === 'string'; }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }

  function canonical(v) {
    if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
    if (isObj(v)) {
      return '{' + Object.keys(v).sort().filter(function (k) { return v[k] !== undefined; }).map(function (k) {
        return JSON.stringify(k) + ':' + canonical(v[k]);
      }).join(',') + '}';
    }
    return JSON.stringify(v);
  }

  // FNV-1a 32-bit: a corruption/tamper hint, not security.
  function checksum(doc) {
    var body = {};
    Object.keys(doc).forEach(function (k) { if (k !== 'checksum') body[k] = doc[k]; });
    var s = canonical(body), h = 0x811c9dc5;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return 'fnv1a:' + ('00000000' + (h >>> 0).toString(16)).slice(-8);
  }

  function checkItem(it, path, errs) {
    if (it === null) return;
    if (!isObj(it)) { errs.push(path + ': item must be an object or null'); return; }
    if (!isInt(it.uid)) errs.push(path + '.uid: integer required');
    if (!isStr(it.baseId) || !it.baseId) errs.push(path + '.baseId: non-empty string required');
    if (!isInt(it.ilvl) || it.ilvl < 0) errs.push(path + '.ilvl: non-negative integer required');
    if (!isStr(it.rarity)) errs.push(path + '.rarity: string required');
    if (!isStr(it.name)) errs.push(path + '.name: string required');
    if (!isStr(it.seedTag)) errs.push(path + '.seedTag: string required');
    if (it.qty !== undefined && (!isInt(it.qty) || it.qty < 1)) errs.push(path + '.qty: positive integer required');
    if (!Array.isArray(it.mods)) errs.push(path + '.mods: array required');
    else it.mods.forEach(function (m, i) {
      if (!isObj(m) || !isStr(m.id) || !isStr(m.stat) || !isStr(m.kind) || !isNum(m.value)) errs.push(path + '.mods[' + i + ']: {id,stat,kind,value} required');
    });
  }

  function checkNumMap(o, path, errs) {
    if (!isObj(o)) { errs.push(path + ': object required'); return; }
    Object.keys(o).forEach(function (k) { if (!isNum(o[k])) errs.push(path + '.' + k + ': finite number required'); });
  }

  // validate(doc) -> string[] (empty means valid). Structural/range checks on a full save document (or a body, if format/version absent).
  function validate(doc, opts) {
    var errs = [], full = !(opts && opts.body);
    if (!isObj(doc)) return ['save: object required'];
    if (full) {
      if (doc.format !== FORMAT) errs.push('format: must be "' + FORMAT + '"');
      if (!isInt(doc.version) || doc.version < 0) errs.push('version: non-negative integer required');
    }
    if (!isStr(doc.gameVersion)) errs.push('gameVersion: string required');
    if (!isStr(doc.savedAt) || isNaN(Date.parse(doc.savedAt))) errs.push('savedAt: ISO date string required');
    if (!isInt(doc.seed) || doc.seed < 0 || doc.seed > 0xffffffff) errs.push('seed: uint32 required');
    checkNumMap(doc.rngState, 'rngState', errs);
    if (!isObj(doc.meta)) errs.push('meta: object required');
    else {
      if (!isInt(doc.meta.level)) errs.push('meta.level: integer required');
      if (!isStr(doc.meta.area)) errs.push('meta.area: string required');
      if (!isNum(doc.meta.playtime) || doc.meta.playtime < 0) errs.push('meta.playtime: non-negative number required');
    }
    if (!isObj(doc.difficulty) || !isStr(doc.difficulty.id)) errs.push('difficulty: object with string id required');
    var p = doc.player;
    if (!isObj(p)) errs.push('player: object required');
    else {
      if (!isStr(p.classId) || !p.classId) errs.push('player.classId: non-empty string required');
      if (!isInt(p.level) || p.level < 1 || p.level > 30) errs.push('player.level: integer 1..30 required');
      if (!isInt(p.xp) || p.xp < 0) errs.push('player.xp: non-negative integer required');
      if (!isInt(p.gold) || p.gold < 0) errs.push('player.gold: non-negative integer required');
      checkNumMap(p.attrs, 'player.attrs', errs);
      if (!isNum(p.hp) || p.hp < 0) errs.push('player.hp: non-negative finite number required');
      if (!isNum(p.resource) || p.resource < 0) errs.push('player.resource: non-negative finite number required');
      if (!isObj(p.pos) || !isNum(p.pos.x) || !isNum(p.pos.y)) errs.push('player.pos: {x,y} finite numbers required');
      if (p.vel !== undefined && (!isObj(p.vel) || !isNum(p.vel.x) || !isNum(p.vel.y))) errs.push('player.vel: {x,y} finite numbers required when present');
      if (!Array.isArray(p.weaponSlots) || p.weaponSlots.length !== 3) errs.push('player.weaponSlots: array of length 3 required');
      else p.weaponSlots.forEach(function (it, i) { checkItem(it, 'player.weaponSlots[' + i + ']', errs); });
      if (!isInt(p.activeWeapon) || p.activeWeapon < 0 || p.activeWeapon > 2) errs.push('player.activeWeapon: integer 0..2 required');
      if (!isObj(p.equipment)) errs.push('player.equipment: object required');
      else Object.keys(p.equipment).forEach(function (k) { checkItem(p.equipment[k], 'player.equipment.' + k, errs); });
      if (!Array.isArray(p.inventory) || p.inventory.length !== 24) errs.push('player.inventory: array of length 24 required');
      else p.inventory.forEach(function (it, i) { checkItem(it, 'player.inventory[' + i + ']', errs); });
      var sk = p.skills;
      if (!isObj(sk)) errs.push('player.skills: object required');
      else {
        checkNumMap(sk.learned, 'player.skills.learned', errs);
        if (!isObj(sk.upgrades)) errs.push('player.skills.upgrades: object required');
        else Object.keys(sk.upgrades).forEach(function (k) { checkNumMap(sk.upgrades[k], 'player.skills.upgrades.' + k, errs); });
        if (!Array.isArray(sk.bar) || sk.bar.length !== 7 || !sk.bar.every(function (b) { return b === null || isStr(b); })) errs.push('player.skills.bar: array of 7 (string|null) required');
        if (!isInt(sk.points) || sk.points < 0) errs.push('player.skills.points: non-negative integer required');
        if (!isInt(sk.spentPoints) || sk.spentPoints < 0) errs.push('player.skills.spentPoints: non-negative integer required');
      }
      if (p.cooldowns !== undefined) checkNumMap(p.cooldowns, 'player.cooldowns', errs);
    }
    if (!isObj(doc.area) || !isStr(doc.area.id) || !isObj(doc.area.visited) || !isObj(doc.area.cleared)) errs.push('area: {id, visited, cleared} required');
    if (!isObj(doc.progress)) errs.push('progress: object required');
    else {
      if (!isObj(doc.progress.bossesDefeated) || !isObj(doc.progress.areasUnlocked)) errs.push('progress: bossesDefeated and areasUnlocked objects required');
      if (!isInt(doc.progress.kills) || doc.progress.kills < 0) errs.push('progress.kills: non-negative integer required');
      if (doc.progress.time !== undefined && (!isNum(doc.progress.time) || doc.progress.time < 0)) errs.push('progress.time: non-negative number required when present');
      if (!isInt(doc.progress.deaths) || doc.progress.deaths < 0) errs.push('progress.deaths: non-negative integer required');
    }
    if (!isObj(doc.settings)) errs.push('settings: object required');
    return errs;
  }

  // serialize(state, opts?) -> JSON string. Throws {code:'shape'} if state is invalid, so a bad state is never written.
  // opts.indent (default 0), opts.now (ISO string used only if state.savedAt is absent).
  function serialize(state, opts) {
    opts = opts || {};
    if (!isObj(state)) { var e0 = new Error('state must be an object'); e0.code = 'shape'; throw e0; }
    var body = clone(state), extra = body.extra;
    delete body.extra;
    if (body.savedAt === undefined) body.savedAt = opts.now || new Date().toISOString();
    var doc = { format: FORMAT, version: P.SAVE_VERSION };
    Object.keys(body).forEach(function (k) { if (k !== 'format' && k !== 'version' && k !== 'checksum') doc[k] = body[k]; });
    if (isObj(extra)) Object.keys(extra).forEach(function (k) { if (KNOWN.indexOf(k) < 0 && doc[k] === undefined) doc[k] = extra[k]; });
    var errs = validate(doc);
    if (errs.length) { var e = new Error('invalid state: ' + errs[0]); e.code = 'shape'; e.details = errs; throw e; }
    doc.checksum = checksum(doc);
    var out = {};
    ORDER.forEach(function (k) { if (doc[k] !== undefined) out[k] = doc[k]; });
    Object.keys(doc).forEach(function (k) { if (out[k] === undefined) out[k] = doc[k]; });
    return JSON.stringify(out, null, opts.indent || 0);
  }

  function err(code, error, details) { var r = { ok: false, code: code, error: error }; if (details) r.details = details; return r; }

  // deserialize(json) -> {ok:true, state, warnings, version} | {ok:false, code:'parse'|'shape'|'version'|'size', error}
  // Never throws, never touches storage. state is the body (no format/version/checksum; unknown top-level keys under `extra`).
  function deserialize(json) {
    if (typeof json !== 'string') return err('shape', 'save must be a JSON string');
    if (json.length > MAX_BYTES) return err('size', 'save exceeds ' + MAX_BYTES + ' bytes');
    var raw;
    try { raw = JSON.parse(json); } catch (e) { return err('parse', 'save is not valid JSON (' + e.message + ')'); }
    if (!isObj(raw) || raw.format !== FORMAT || typeof raw.version !== 'number') return err('shape', 'not an offline-rpg save');
    var warnings = [], migrated;
    var origVersion = raw.version;
    if (raw.version > P.SAVE_VERSION) return err('version', 'saved by a newer game version (' + raw.version + ')');
    try { migrated = P.migrate(raw); } catch (e) { return err(e.code || 'shape', e.message); }
    if (origVersion === P.SAVE_VERSION && raw.checksum !== undefined && raw.checksum !== checksum(raw)) {
      warnings.push('checksum mismatch (save was modified or damaged); loaded anyway');
    }
    var errs = validate(migrated);
    if (errs.length) return err('shape', 'invalid save: ' + errs[0], errs);
    var state = {}, extra = {}, hasExtra = false;
    Object.keys(migrated).forEach(function (k) {
      if (k === 'format' || k === 'version' || k === 'checksum') return;
      if (KNOWN.indexOf(k) >= 0) state[k] = migrated[k]; else { extra[k] = migrated[k]; hasExtra = true; }
    });
    if (hasExtra) state.extra = extra;
    return { ok: true, state: state, warnings: warnings, version: origVersion };
  }

  P.FORMAT = FORMAT;
  P.MAX_BYTES = MAX_BYTES;
  P.checksum = checksum;
  P.validate = validate;
  P.serialize = serialize;
  P.deserialize = deserialize;
})(globalThis.RPG = globalThis.RPG || {});

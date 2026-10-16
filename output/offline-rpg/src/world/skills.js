/* Skill tree: point spending, prerequisites, respec, passives, active-cast gating (DATA-DEFINITIONS §4). Headless: no DOM.
   Player state used (matches SAVE-SCHEMA): player.level, player.resource, player.gold, player.stats (StatBlock),
   player.cooldowns, and player.skills = { learned:{id:rank}, upgrades:{id:{upgradeId:rank}}, bar:[id|null x 7],
   points, spentPoints }. Skill points per rank = 1 for the skill itself, `upgrade.cost` per upgrade rank. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  var BAR_SIZE = 7;
  var RESPEC_GOLD_PER_LEVEL = 50;

  function defs() { return RPG.data.skills; }
  function def(id) { return Object.prototype.hasOwnProperty.call(defs(), id) ? defs()[id] : null; }

  function createSkillState(points) {
    var bar = [];
    for (var i = 0; i < BAR_SIZE; i++) bar.push(null);
    return { learned: {}, upgrades: {}, bar: bar, points: points || 0, spentPoints: 0 };
  }

  function state(player) {
    if (!player.skills) player.skills = createSkillState(0);
    return player.skills;
  }

  function getSkillRank(player, skillId) { return (player.skills && player.skills.learned[skillId]) || 0; }
  function getUpgradeRanks(player, skillId) {
    var u = player.skills && player.skills.upgrades[skillId];
    return u ? Object.assign({}, u) : {};
  }
  function upgradeRank(player, skillId, upId) {
    var u = player.skills && player.skills.upgrades[skillId];
    return (u && u[upId]) || 0;
  }
  function findUpgrade(d, upId) {
    for (var i = 0; i < d.upgrades.length; i++) if (d.upgrades[i].id === upId) return d.upgrades[i];
    return null;
  }

  function fail(reason) { return { ok: false, reason: reason }; }

  /** Checks level / prerequisite-skill ranks / spent-points gate, then points and rank cap, for the next rank. */
  function canLearn(player, skillId) {
    var d = def(skillId);
    if (!d) return fail('unknown-skill');
    var st = state(player), rank = getSkillRank(player, skillId), req = d.requires || {};
    if (rank >= d.maxRank) return fail('max-rank');
    if ((player.level || 1) < (req.level || 1)) return fail('level');
    if (req.skills) {
      for (var id in req.skills) if (getSkillRank(player, id) < req.skills[id]) return fail('prerequisite');
    }
    if (req.points && st.spentPoints < req.points) return fail('points-spent');
    if (st.points < 1) return fail('no-points');
    return { ok: true };
  }

  function learnSkill(world, player, skillId) {
    var r = canLearn(player, skillId);
    if (!r.ok) return false;
    var st = state(player);
    st.points -= 1;
    st.spentPoints += 1;
    st.learned[skillId] = getSkillRank(player, skillId) + 1;
    applyPassives(player);
    return true;
  }

  function canUpgrade(player, skillId, upgradeId) {
    var d = def(skillId);
    if (!d) return fail('unknown-skill');
    var up = findUpgrade(d, upgradeId);
    if (!up) return fail('unknown-upgrade');
    if (getSkillRank(player, skillId) < 1) return fail('not-learned');
    if (getSkillRank(player, skillId) < up.requiresRank) return fail('parent-rank');
    if (upgradeRank(player, skillId, upgradeId) >= up.maxRank) return fail('max-rank');
    if (state(player).points < up.cost) return fail('no-points');
    return { ok: true };
  }

  function upgradeSkill(world, player, skillId, upgradeId) {
    if (!canUpgrade(player, skillId, upgradeId).ok) return false;
    var st = state(player), up = findUpgrade(def(skillId), upgradeId);
    st.points -= up.cost;
    st.spentPoints += up.cost;
    if (!st.upgrades[skillId]) st.upgrades[skillId] = {};
    st.upgrades[skillId][upgradeId] = upgradeRank(player, skillId, upgradeId) + 1;
    applyPassives(player);
    return true;
  }

  /** Puts a learned active skill in a bar slot (a skill occupies at most one slot). Passives cannot be slotted. */
  function equipSkill(player, skillId, slotIndex) {
    var st = state(player);
    if (slotIndex < 0 || slotIndex >= BAR_SIZE || slotIndex !== Math.floor(slotIndex)) return false;
    var d = def(skillId);
    if (!d || d.type !== 'active' || getSkillRank(player, skillId) < 1) return false;
    for (var i = 0; i < BAR_SIZE; i++) if (st.bar[i] === skillId) st.bar[i] = null;
    st.bar[slotIndex] = skillId;
    return true;
  }

  /** Refunds every spent point, forgets all skills and bar entries, drops passive modifiers. `cost` is gold (0 = free). */
  function respec(world, player, cost) {
    var st = state(player), gold = cost || 0;
    if (gold > 0 && (player.gold || 0) < gold) return { ok: false, refunded: 0, reason: 'no-gold' };
    if (gold > 0) player.gold -= gold;
    var refunded = st.spentPoints;
    st.points += refunded;
    st.spentPoints = 0;
    st.learned = {};
    st.upgrades = {};
    for (var i = 0; i < BAR_SIZE; i++) st.bar[i] = null;
    applyPassives(player);
    return { ok: true, refunded: refunded };
  }

  function respecCost(player) { return RESPEC_GOLD_PER_LEVEL * (player.level || 1); }

  /** Passive modifier value: rank-1 value + rankScaling[stat] per extra rank, plus per-rank upgrade values. */
  function passiveModifiers(player, skillId) {
    var d = def(skillId), rank = getSkillRank(player, skillId), sums = {}, order = [];
    if (!d || d.type !== 'passive' || rank < 1) return [];
    function add(m, amount) {
      var key = m.stat + '|' + m.kind;
      if (!(key in sums)) { sums[key] = { stat: m.stat, kind: m.kind, value: 0 }; order.push(key); }
      sums[key].value += amount;
    }
    (d.passiveMods || []).forEach(function (m) {
      add(m, m.value + ((d.rankScaling && d.rankScaling[m.stat]) || 0) * (rank - 1));
    });
    d.upgrades.forEach(function (up) {
      var r = upgradeRank(player, skillId, up.id);
      if (r > 0 && up.passiveMods) up.passiveMods.forEach(function (m) { add(m, m.value * r); });
    });
    return order.map(function (k) { return sums[k]; });
  }

  /** Rebuilds every 'skill:<id>' modifier on player.stats from the learned state. Idempotent. */
  function applyPassives(player) {
    if (!player.stats) return;
    var id, i, mods;
    var all = defs();
    for (id in all) if (all[id].type === 'passive') player.stats.removeModifiersBySource('skill:' + id);
    for (id in all) {
      mods = passiveModifiers(player, id);
      for (i = 0; i < mods.length; i++) {
        player.stats.addModifier({ source: 'skill:' + id, stat: mods[i].stat, kind: mods[i].kind, value: mods[i].value });
      }
    }
  }

  /** Base params + rankScaling per rank above 1 + upgrade paramDeltas per upgrade rank ({mult} compounds per rank). */
  function resolveSkillParams(player, d) {
    if (typeof d === 'string') d = def(d);
    var rank = Math.max(getSkillRank(player, d.id), 1), out = Object.assign({}, d.params), k;
    for (k in d.rankScaling || {}) if (k in out) out[k] += d.rankScaling[k] * (rank - 1);
    d.upgrades.forEach(function (up) {
      var r = upgradeRank(player, d.id, up.id), delta;
      if (!r || !up.paramDeltas) return;
      for (k in up.paramDeltas) {
        delta = up.paramDeltas[k];
        if (typeof delta === 'object') out[k] = (out[k] || 0) * Math.pow(delta.mult, r);
        else out[k] = (out[k] || 0) + delta * r;
      }
    });
    return out;
  }

  function cdKey(id) { return 'skill:' + id; }
  function cooldownRemaining(player, skillId) {
    return (player.cooldowns && player.cooldowns[cdKey(skillId)]) > 0 ? player.cooldowns[cdKey(skillId)] : 0;
  }

  /** Gate for casting a skill by id: learned active, off cooldown, enough resource. */
  function canCast(player, skillId) {
    var d = def(skillId);
    if (!d || d.type !== 'active') return fail('not-active');
    if (getSkillRank(player, skillId) < 1) return fail('not-learned');
    if (cooldownRemaining(player, skillId) > 0) return fail('cooldown');
    if ((player.resource || 0) < d.cost) return fail('resource');
    return { ok: true };
  }

  // Optional effect executor (world/skill-effects.js registers itself): execute(world, player, def, rank, aim, params).
  var executor = null;
  function setExecutor(fn) { executor = typeof fn === 'function' ? fn : null; }

  /** Casts the skill in bar slot `slotIndex`. On success spends resource, starts the (CDR-reduced) cooldown and returns
      { ok, skillId, def, params }; with an executor registered and a successful cast the result also carries
      `applied` (the executor's result). `aim` is the world-space target {x,y}. On failure returns { ok:false, reason }. */
  function tryCastSkill(world, player, slotIndex, aim) {
    var st = state(player), id = st.bar[slotIndex];
    if (!id) return fail('empty-slot');
    var r = canCast(player, id);
    if (!r.ok) return r;
    var d = def(id), cdr = player.stats ? player.stats.get('cooldownReduction') : 0;
    player.resource -= d.cost;
    if (!player.cooldowns) player.cooldowns = {};
    player.cooldowns[cdKey(id)] = d.cooldown * (1 - cdr);
    var out = { ok: true, skillId: id, def: d, params: resolveSkillParams(player, d) };
    if (executor) out.applied = executor(world, player, d, getSkillRank(player, id), aim, out.params);
    return out;
  }

  /** Ticks skill cooldowns down by dt seconds, dropping finished ones. */
  function updateSkills(world, player, dt) {
    if (!player.cooldowns) return;
    for (var k in player.cooldowns) {
      if (k.indexOf('skill:') !== 0) continue;
      player.cooldowns[k] -= dt;
      if (player.cooldowns[k] <= 0) delete player.cooldowns[k];
    }
  }

  function serialize(player) {
    var st = state(player), learned = {}, upgrades = {}, id, up;
    for (id in st.learned) learned[id] = st.learned[id];
    for (id in st.upgrades) { up = Object.assign({}, st.upgrades[id]); upgrades[id] = up; }
    return { learned: learned, upgrades: upgrades, bar: st.bar.slice(), points: st.points, spentPoints: st.spentPoints };
  }

  /** Restores onto `player`. Unknown ids / over-cap ranks (removed content) are dropped and their cost refunded as points;
      then passives are re-applied. */
  function restore(player, data) {
    var st = createSkillState(Math.max(0, (data && data.points) | 0)), id, d, rank, up, r, upId;
    var learned = (data && data.learned) || {}, upgrades = (data && data.upgrades) || {};
    var spent = Math.max(0, (data && data.spentPoints) | 0), kept = 0;
    for (id in learned) {
      d = def(id);
      if (!d) continue;
      rank = Math.min(Math.max(learned[id] | 0, 0), d.maxRank);
      if (rank < 1) continue;
      st.learned[id] = rank;
      kept += rank;
      for (upId in (upgrades[id] || {})) {
        up = findUpgrade(d, upId);
        if (!up) continue;
        r = Math.min(Math.max(upgrades[id][upId] | 0, 0), up.maxRank);
        if (r < 1) continue;
        if (!st.upgrades[id]) st.upgrades[id] = {};
        st.upgrades[id][upId] = r;
        kept += r * up.cost;
      }
    }
    st.spentPoints = kept;
    st.points += Math.max(0, spent - kept);
    var bar = (data && data.bar) || [];
    for (var i = 0; i < BAR_SIZE; i++) st.bar[i] = bar[i] && getValidBar(st, bar[i]) ? bar[i] : null;
    player.skills = st;
    applyPassives(player);
    return player.skills;
  }
  function getValidBar(st, id) { var d = def(id); return !!d && d.type === 'active' && (st.learned[id] || 0) > 0; }

  RPG.world.skills = {
    BAR_SIZE: BAR_SIZE, createSkillState: createSkillState,
    getSkillRank: getSkillRank, getUpgradeRanks: getUpgradeRanks,
    canLearn: canLearn, learnSkill: learnSkill, canUpgrade: canUpgrade, upgradeSkill: upgradeSkill,
    equipSkill: equipSkill, respec: respec, respecCost: respecCost,
    applyPassives: applyPassives, resolveSkillParams: resolveSkillParams,
    setExecutor: setExecutor, canCast: canCast, tryCastSkill: tryCastSkill, updateSkills: updateSkills, cooldownRemaining: cooldownRemaining,
    serialize: serialize, restore: restore
  };
})(globalThis.RPG = globalThis.RPG || {});

/* Equipment: one item per slot, applied to a StatBlock as modifiers from source 'item:<uid>' (DATA-DEFINITIONS §6).
   An item contributes its base def stats plus its rolled mods, all as 'flat'/'inc' modifiers. Unequip removes exactly
   that source, so stats return to what they were. Headless: no DOM. */
(function (RPG) {
  'use strict';

  RPG.world = RPG.world || {};

  function sourceOf(item) { return 'item:' + item.uid; }

  function createEquipment(slots) {
    var eq = { slots: {} };
    if (slots) for (var s in slots) eq.slots[s] = slots[s];
    return eq;
  }

  function slotOf(item) {
    var def = RPG.data.items[item.baseId];
    if (!def) throw new Error('equipment: unknown base item ' + item.baseId);
    return def.slot;
  }

  function isEquippable(item) {
    return !!item && RPG.data.itemSlots.indexOf(slotOf(item)) >= 0;
  }

  /** Every modifier an item grants, as StatBlock modifiers. */
  function itemModifiers(item) {
    var def = RPG.data.items[item.baseId], src = sourceOf(item), out = [], k;
    for (k in def.base.stats) out.push({ source: src, stat: k, kind: 'flat', value: def.base.stats[k] });
    for (var i = 0; i < item.mods.length; i++) {
      var m = item.mods[i];
      out.push({ source: src, stat: m.stat, kind: m.kind, value: m.value });
    }
    return out;
  }

  function applyItem(statBlock, item) {
    var mods = itemModifiers(item);
    for (var i = 0; i < mods.length; i++) statBlock.addModifier(mods[i]);
  }

  /** Equips `item` into its slot, swapping out whatever was there. Returns the displaced item or null. */
  function equip(eq, statBlock, item) {
    if (!isEquippable(item)) throw new Error('equipment: ' + item.baseId + ' cannot be equipped');
    var slot = slotOf(item);
    var previous = unequip(eq, statBlock, slot);
    applyItem(statBlock, item);
    eq.slots[slot] = item;
    return previous;
  }

  /** Empties `slot`, removing the item's modifiers. Returns the item, or null if the slot was empty. */
  function unequip(eq, statBlock, slot) {
    var item = eq.slots[slot];
    if (!item) return null;
    statBlock.removeModifiersBySource(sourceOf(item));
    delete eq.slots[slot];
    return item;
  }

  /** Re-applies every equipped item to a fresh StatBlock (e.g. after loading a save). */
  function applyAll(eq, statBlock) {
    for (var slot in eq.slots) applyItem(statBlock, eq.slots[slot]);
  }

  /**
   * Compares `candidate` against what is equipped in its slot, on a copy of the stat block (nothing is mutated).
   * Returns { slot, current, deltas: { stat: { before, after, diff } } } with only changed stats listed.
   */
  function compare(eq, statBlock, candidate) {
    var slot = slotOf(candidate);
    var current = eq.slots[slot] || null;
    var trial = RPG.world.StatBlock.restore(statBlock.serialize());
    if (current) trial.removeModifiersBySource(sourceOf(current));
    var before = statBlock.snapshot();
    applyItem(trial, candidate);
    var after = trial.snapshot();
    var deltas = {};
    for (var stat in after) {
      var b = before[stat] === undefined ? 0 : before[stat];
      var diff = after[stat] - b;
      if (Math.abs(diff) > 1e-9) deltas[stat] = { before: b, after: after[stat], diff: diff };
    }
    return { slot: slot, current: current, deltas: deltas };
  }

  RPG.world.equipment = {
    createEquipment: createEquipment, isEquippable: isEquippable, itemModifiers: itemModifiers,
    equip: equip, unequip: unequip, swap: equip, applyAll: applyAll, compare: compare
  };
})(globalThis.RPG);

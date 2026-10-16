# Save schema (v1)

Human-readable JSON, one document per slot, also the export/import format (`game-save.json`).
Storage: `localStorage` keys `offline-rpg:v1:slot:<auto|1|2|3>` (saves) and `offline-rpg:v1:settings` (settings, a separate
document, see "Settings key" below). Slot values are `JSON.stringify(save, null, 0)`; export uses indent 2. A write goes to
`<key>:tmp` first, is read back, then swapped into `<key>`; any failure restores the previous value, and an interrupted write is
recovered from `:tmp` on the next load. A save is a few KB: a real browser save was ~2 KB, `sample-game-save.json` is 2.7 KB compact (4.7 KB pretty-printed, 6 of 24 inventory slots filled, ~150 bytes per item), so a full 24-slot inventory is roughly 6 KB. That is arithmetic from the sample, not a measured full save; either way far under localStorage quotas. IndexedDB is not needed.

## Top level

```jsonc
{
  "format": "offline-rpg-save",          // magic; import rejects anything else
  "version": 1,                           // SAVE_VERSION, integer, bumped on any breaking shape change
  "gameVersion": "1.0.0",                 // informational
  "savedAt": "2026-10-16T12:00:00.000Z",  // ISO
  "seed": 123456789,                      // uint32 world seed (new-game seed)
  "rngState": { "world": 987654321, "loot": 1122334455 },   // current stream states so play continues deterministically
  "checksum": "fnv1a:9a3c1f02",           // FNV-1a over canonical JSON of everything except `checksum` (tamper/corruption hint, NOT security)
  "meta": { "level": 7, "area": "forest", "difficulty": "normal", "playtime": 1834.5, "name": "Wayfarer" },
  "difficulty": { "id":"normal", "enemyHp":1, "enemyDamage":1, "enemySpeed":1, "spawnDensity":1, "eliteChance":0.1,
                  "bossHp":1, "bossTelegraph":1, "lootQuality":1, "xpMultiplier":1, "goldMultiplier":1, "playerDeathPenalty":"gold10" },
  "player": {
    "classId": "wayfarer", "level": 7, "xp": 340, "gold": 512, "attrs": {"might":..,"agility":..,"spirit":..},
    "hp": 96.5, "resource": 41,
    "pos": { "x": 1204.0, "y": 880.5 }, "vel": { "x": 0, "y": 0 },     // vel optional (px/s); absent = at rest
    "weaponSlots": [ItemInstance|null, ItemInstance|null, ItemInstance|null], "activeWeapon": 0,
    "equipment": { "head": ItemInstance|null, "chest": ..., "legs": ..., "boots": ..., "ring1": ..., "ring2": ..., "amulet": ..., "offhand": ... },
    "inventory": [ ItemInstance|null x 24 ],
    "skills": { "learned": {"fireball":2,"whirlwind":1}, "upgrades": {"fireball":{"bigger-explosion":1}},
                "bar": ["fireball","whirlwind",null,null,null,"mend",null], "points": 3, "spentPoints": 4 },
    "cooldowns": {}                         // optional; omitted/empty means all ready
  },
  "area": { "id": "forest", "visited": {"town":true,"forest":true}, "cleared": {"forest": [3,5]} },
  "progress": { "bossesDefeated": {}, "areasUnlocked": {"town":true,"forest":true,"caves":true}, "kills": 211, "deaths": 2, "time": 48.9 /* optional; world clock seconds, absent = 0 */ },
  "settings": { "v": 1, "volume": 0.6, "sfx": 0.8, "muted": false, "quality": "high", "showDamageNumbers": true, "screenShake": true,
                "difficulty": "normal", "custom": null,
                "keys": { "toggleSkills": ["KeyL"] /* action -> codes; only non-default entries are present */ } }
}
```

`ItemInstance` = `{ "uid":int, "baseId":string, "ilvl":int, "rarity":"rare", "name":string, "mods":[{"id","stat","kind","value"}], "seedTag":string, "qty"?:int }`
(see DATA-DEFINITIONS §6). The `settings` block is only checked to be an object; it is a copy of the game's settings at save time
(the same fields as the settings key below, and `screenShake` is a boolean; older numeric values are read as `> 0`). It is
**not authoritative**: when the settings store is present, loading a save does not apply the embedded copy, so a save never
overrides the player's current settings. Only `meta.name`, the world and the difficulty come back from the save. Only **IDs and resolved numbers** are stored — never definitions — so balance tuning does not corrupt saves,
and unknown IDs (removed content) are handled by the loader (item dropped to gold value, skill refunded as points, area → `town`).

**Not saved (transient):** monsters, projectiles, effects, drops-on-ground, particles, cooldown animation, camera. On load the current area is
regenerated from `hash(seed, areaId)`, so “where you left off” = same layout, same area, same position. This keeps saves tiny and
deterministic. `area.cleared` maps an area id to the numeric indexes of its cleared encounter slots; `restore()` installs it on the world *before* the area is
populated, so those slots stay empty on load (covered by `tests/persistence/resume-exact.test.js`). The player's velocity is saved as the
optional `player.vel` ({x,y}, px/s); a save without it (older files) loads at rest, so the shape change is additive and `SAVE_VERSION` stays 1.
Together with `pos` and the rng states this makes a restored walk match an uninterrupted one exactly. Still not saved (transient, rebuilt on
load): live monsters of uncleared slots, projectiles, and an attack swing or dash in progress (a swing slows movement, so save at rest for an exact continuation). A boss fight in progress is not saved: a boss that is not in `progress.bossesDefeated` starts again.

**Autosave:** slot `auto` (`core/game.js`) is written on area change, level up, boss defeat, victory, and every 60 s of world time once no
monster has been within its aggro range for 5 s. It is skipped while dead, paused or in a menu. Quick save (`F5`) writes slot `1`.

## Settings key

`offline-rpg:v1:settings` holds one JSON document written by `ui/settings.js` whenever the Settings screen changes a value, and read
once at startup. It is independent of the slots, so *Delete save* never touches it:

```jsonc
{ "v": 1, "volume": 0.5, "sfx": 0.8, "muted": false, "quality": "high" /* low|med|high */,
  "showDamageNumbers": true, "screenShake": true,
  "difficulty": "normal" /* preset id or "custom" */, "custom": null /* or the ten clamped multipliers */,
  "keys": {} /* action -> [code]; non-default bindings only, first code is used */ }
```

Loading is per field: a missing or invalid field keeps its default and adds a warning, and an unparsable document is ignored, so a damaged
key cannot block the game. The default key actions are in `src/input/keymap.js`.

## Load procedure (`persistence.deserialize`)

1. `JSON.parse` in try/catch → `code:'parse'`.
2. `format === 'offline-rpg-save'`, `typeof version === 'number'` → else `code:'shape'`.
3. `version > SAVE_VERSION` → `code:'version'` ("saved by a newer game version"); never attempts to load.
4. `migrate(raw)` runs `MIGRATIONS[v]` for v..SAVE_VERSION-1 on a deep clone (original untouched).
5. Verify `checksum` (mismatch → **warning** only, load proceeds if shape validates, since hand-edited saves are legitimate: the format is human-readable).
6. `validate()` checks required fields, types and ranges (level 1..30, `weaponSlots.length===3`, `inventory.length===24`, `skills.bar.length===7`, finite numbers, `activeWeapon` 0..2).
7. `persistence.restore(state)` (`persistence/snapshot.js`) rejects unknown modifier kinds, duplicate equipped uids and an xp out of range for the level; then builds the world with `createWorld({seed})` and `areas.enter(area.id)`, sets difficulty, progress, items, skills (removed skills refunded as points, with a warning), recomputed stats, hp (clamped to max, with a warning), position and rng states. Unknown item base ids are dropped with a warning; an unknown area restores in `town`; an unwalkable saved position moves to the area spawn.
8. On any failure: return `{ok:false, error, code}`; UI shows the message, **the existing in-memory game and stored saves remain untouched**.
   A failed import never overwrites a slot. A corrupt slot is moved to `offline-rpg:v1:slot:<n>:corrupt` (kept for recovery/export) rather than deleted.

`progress.time` (world clock, seconds) was added without a version bump: it is optional, validated as a non-negative number when present, and restored to 0 when absent.

## Migration strategy

- `version` is a single integer (`SAVE_VERSION` is 1). `MIGRATIONS[v]` turns a version-`v` save into `v+1` (today only `0: …` exists, a pre-release stub that fills `rngState`); each function transforms exactly one step on a cloned object;
  `migrate()` loops `while (s.version < SAVE_VERSION)`. Missing step → error `code:'version'`.
- Rules: additive changes with safe defaults do NOT need a bump (loader fills defaults via `withDefaults(shapeV1, raw)`); renames, removed fields,
  changed semantics DO bump. Each migration should ship with a test asserting a saved sample at the old version validates after `migrate` (the v0 stub is covered in `tests/persistence/`).
- Forward-compat: unknown top-level keys are returned by `deserialize` under `extra` and written back at top level on re-save by `serialize`.
- Release rule: never edit a published migration; add a new one.

## Export / import

- Export: `Blob(JSON.stringify(save,null,2), 'application/json')` → `<a download="game-save-<level>-<date>.json">` (`persistence.exportFileName`; default name `game-save.json`). There is no textarea or clipboard fallback.
- Import: file picker (`persistence.files.pickSaveText`) → `importJson` runs the same load procedure; only on success is it written to the chosen slot (`1` by default, never `auto`)
  and then loaded. A second Enter confirms overwriting. Max accepted size 2 MB. The Save/Load menu: `X` exports, `G` imports, `D` deletes.
- Delete removes the slot key (and its `:tmp`) only, settings preserved, after a second Enter.

## Sample

`docs/sample-game-save.json` — a level-5 save (3 weapons, 6 inventory items, 3 skills with one upgrade, forest, difficulty normal), with a valid checksum.
`tests/persistence/persistence.test.js` ("docs/sample-game-save.json validates and loads") loads it on every run.

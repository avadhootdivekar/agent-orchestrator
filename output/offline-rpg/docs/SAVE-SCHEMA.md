# Save schema (v1)

Human-readable JSON, one document per slot, also the export/import format (`game-save.json`).
Storage: `localStorage` keys `offline-rpg:v1:slot:<auto|1|2|3>` and `offline-rpg:v1:settings`. Values are `JSON.stringify(save, null, 0)`;
export uses indent 2. A save is ~10–40 KB (24-slot inventory); well under localStorage quotas. IndexedDB is not needed.

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
    "pos": { "x": 1204.0, "y": 880.5 },
    "weaponSlots": [ItemInstance|null, ItemInstance|null, ItemInstance|null], "activeWeapon": 0,
    "equipment": { "head": ItemInstance|null, "chest": ..., "legs": ..., "boots": ..., "ring1": ..., "ring2": ..., "amulet": ..., "offhand": ... },
    "inventory": [ ItemInstance|null x 24 ],
    "skills": { "learned": {"fireball":2,"whirlwind":1}, "upgrades": {"fireball":{"bigger-explosion":1}},
                "bar": ["fireball","whirlwind",null,null,null,"mend",null], "points": 3, "spentPoints": 4 },
    "cooldowns": {}                         // optional; omitted/empty means all ready
  },
  "area": { "id": "forest", "visited": {"town":true,"forest":true}, "cleared": {"forest": ["slot-3","slot-5"]} },
  "progress": { "bossesDefeated": {}, "areasUnlocked": {"town":true,"forest":true,"caves":true}, "kills": 211, "deaths": 2 },
  "settings": { "volume": 0.6, "sfx": 0.8, "muted": false, "quality": "high", "showDamageNumbers": true, "screenShake": 0.7,
                "keys": { "up":["KeyW","ArrowUp"], "skills":["KeyK"] /* only non-default entries need to be present */ } }
}
```

`ItemInstance` = `{ "uid":int, "baseId":string, "ilvl":int, "rarity":"rare", "name":string, "mods":[{"id","stat","kind","value"}], "seedTag":string, "qty"?:int }`
(see DATA-DEFINITIONS §6). Only **IDs and resolved numbers** are stored — never definitions — so balance tuning does not corrupt saves,
and unknown IDs (removed content) are handled by the loader (item dropped to gold value, skill refunded as points, area → `town`).

**Not saved (transient):** monsters, projectiles, effects, drops-on-ground, particles, cooldown animation, camera. On load the current area is
regenerated from `hash(seed, areaId)`; cleared encounter slots (`area.cleared`) are skipped, so “where you left off” = same layout, same area,
same position, cleared fights stay cleared, uncleared fights respawn. This keeps saves tiny and deterministic. Boss in progress is not saved: loading
restarts the boss fight from the arena door (documented; the autosave is written at area entry, never mid-boss).

**Autosave:** slot `auto` written on area change, level up, boss defeat and every 60 s of idle in a safe state (not in combat for 5 s).
`settings` is also stored on its own key so it survives “Delete save”.

## Load procedure (`persistence.deserialize`)

1. `JSON.parse` in try/catch → `code:'parse'`.
2. `format === 'offline-rpg-save'`, `typeof version === 'number'` → else `code:'shape'`.
3. `version > SAVE_VERSION` → `code:'version'` ("saved by a newer game version"); never attempts to load.
4. `migrate(raw)` runs `MIGRATIONS[v]` for v..SAVE_VERSION-1 on a deep clone (original untouched).
5. Verify `checksum` (mismatch → **warning** only, load proceeds if shape validates, since hand-edited saves are legitimate: the format is human-readable).
6. `validate()` checks required fields, types and ranges (level 1..30, `inventory.length===24`, numeric finite, known enum values); sanitises (clamp hp ≤ maxHp, drop unknown items).
7. Build `World` via `createWorld({seed})`, apply, `areas.enter(area.id)`, restore position and rng states, `recomputePlayerStats`.
8. On any failure: return `{ok:false, error, code}`; UI shows the message, **the existing in-memory game and stored saves remain untouched**.
   A failed import never overwrites a slot. A corrupt slot is moved to `offline-rpg:v1:slot:<n>:corrupt` (kept for recovery/export) rather than deleted.

## Migration strategy

- `version` is a single integer. `MIGRATIONS = { 1: (s)=>{ ...; s.version = 2; return s; }, ... }` — each function transforms exactly one step on a cloned object;
  `migrate()` loops `while (s.version < SAVE_VERSION)`. Missing step → error `code:'version'`.
- Rules: additive changes with safe defaults do NOT need a bump (loader fills defaults via `withDefaults(shapeV1, raw)`); renames, removed fields,
  changed semantics DO bump. Each migration ships with a fixture `tests/fixtures/save-vN.json` and a test asserting `migrate(fixtureN)` validates at the current version.
- Forward-compat: unknown top-level keys are preserved on re-save in `extra` to avoid destroying data from tools.
- Release rule: never edit a published migration; add a new one.

## Export / import

- Export: `Blob(JSON.stringify(save,null,2), 'application/json')` → `<a download="game-save-<level>-<date>.json">` (default name `game-save.json`). Fallback for browsers
  without download (some Android webviews): show the JSON in a `<textarea>` with a Copy button.
- Import: file picker (`<input type=file accept=".json,application/json">`) or paste textarea → same load procedure into a staging world; only on success is it written to the chosen
  slot (confirm if overwriting). Max accepted size 2 MB.
- `Delete Save` removes the slot key only (settings preserved), with confirm.

## Sample

`samples/game-save.sample.json` — a level-5 save (3 weapons, 6 inventory items, 3 skills with one upgrade, forest, difficulty normal). Produced by a later unit via
`node build/make-sample-save.js` (uses `persistence.serialize` headlessly so it cannot drift from the schema); the test suite loads it.

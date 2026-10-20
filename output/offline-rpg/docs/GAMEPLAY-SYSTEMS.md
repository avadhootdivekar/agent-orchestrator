# Gameplay systems: weapons, skills, monsters, loot, difficulty

A short guide to the five gameplay systems. The full field lists and design intent are in
[`DATA-DEFINITIONS.md`](DATA-DEFINITIONS.md) (§2 difficulty, §3 weapons, §4 skills, §5 monsters, §6 items).

Each system's **data** is in `src/data/` (tested by `tests/data/`) and its **logic** is in `src/world/`. All five are
wired into the per-tick pipeline in `sim/sim.js` (stage list in [`ARCHITECTURE.md`](ARCHITECTURE.md) §3.1), and the
headless integration tests in `tests/integration/` play a scripted 60 s run, a boss fight and area transitions through
them. Nothing here has been walked through by hand: the only real-browser evidence is scripted headless Chrome (w07-04, w08-06) (see [`TEST-CHECKLIST.md`](TEST-CHECKLIST.md)).

## Status at a glance

| System | Data (`src/data/`) | Logic | Wired in |
|---|---|---|---|
| Weapons | `weapons.js`: `sword`, `hunting-bow`, `arc-staff` | `world/weapons.js`: attack state machine (windup, active, recover), projectiles. Behaviours `swing`, `projectile`, `spread`, `aoeGround`; any other key is refused by `tryAttack` | `sim/player.js` (attack, weapon switch), `sim/combat-step.js` (projectiles, hits) |
| Skills | `skills.js`: nine skills | `world/skills.js` (learn, upgrade, respec, bar, cooldowns, passives) and `world/skill-effects.js` (cast effects) | `sim/player.js` casts bar slots 1–7; `ui/skills-screen.js` spends points |
| Monsters | `monsters.js`: six monsters and the boss `warden-of-ash` | `world/ai.js` (one behaviour per AI key), `world/boss.js` (phases, telegraphs), `world/encounters.js` (seeded composition, elites) | `sim/monsters.js` stages 5–7, `world/areas.js` spawns on area entry |
| Loot | `items.js`: 16 items, 5 rarities, 9 modifiers | `world/loot.js` (seeded generation, drop tables), `world/equipment.js` (equip, stat modifiers) | `sim/rewards.js` (drops, pickups), `ui/inventory.js` |
| Difficulty | `difficulties.js`: `easy`, `normal`, `hard`, `nightmare`, `custom` | `world/difficulty.js`: `resolve`, `clampCustom`, `scaleMonster`, `spawnBudget` | spawn scaling at area entry and per-monster stats; changed in the main menu and Settings |

Also wired: the XP curve and level-up grants (`world/progression.js`, called from `sim/rewards.js`) and the stat model
(`world/stats.js`, `StatBlock`) with base values from DATA-DEFINITIONS §1. Areas are `town`, `forest`, `caves` and
`arena` (`data/areas.js`), unlocked in that order by `world/areas.js`; the caves need level 4.

## Weapons

A weapon is data: a category, a `behavior` key that picks the attack code, damage and timing values, and optional
projectile, area and status fields. Behaviour keys in the design are `swing`, `thrust`, `smash`, `projectile`, `spread`,
`beam`, `arc`, `aoeGround` and `explosive`. Each attack has three animation phases (windup, active, recover). Damage
lands at the start of the active phase.

The shipped starter weapons:

| Id | Category | Behaviour | Notes |
|---|---|---|---|
| `sword` | melee | `swing` | cone attack, no resource cost, no status |
| `hunting-bow` | ranged | `projectile` | projectile with pierce 1, resource cost 2 |
| `arc-staff` | area | `aoeGround` | costs 8 resource; applies `slow` (strength 0.4, 2.5 s) on each hit |

The design's `arc` (chain lightning), `thrust`, `smash`, `beam` and `explosive` behaviours have no code, and the Mace, Crossbow and Bomb thrower are not in the data. Weapon slots are three; `Tab`, `Shift+Tab` and the mouse wheel switch (the intent's `switchDelta`). The player starts with all three weapons.

## Skills

Skills have a type (`active` or `passive`) and a category. An active skill has a cost in energy, a cooldown (reduced by
cooldown reduction), a cast time and a targeting mode. Each rank scales the skill's parameters. Upgrades are chosen per
skill and each has its own max rank. Passive skills add modifiers to the player's stats under their own source name.

Shipped skills:

- Active: `fireball`, `whirlwind`, `ground-slam`, `frost-nova`, `shield`, `mend`, `blink-strike`
- Passive: `vitality`, `focus`

Learning a skill needs a level and sometimes another skill at a given rank. Skill points come from levelling (one per
level, with a bonus every five levels). Respec costs gold and refunds all spent points. The keymap binds the skill
actions to `1`–`5`, `Q` and `E` (bar slots 1–7); `input/intent.js` reports them as edge-triggered flags and the player stage casts the skill in that bar slot.

## Monsters

A monster is data: an archetype, a tier, a shape (so each monster has its own silhouette, not a recolour), base stats
with per-level growth, an AI key and its parameters, attack definitions, XP and gold ranges, and a loot table.

Shipped monsters, with the AI key each one uses:

| Id | Archetype | AI | Tier |
|---|---|---|---|
| `husk-grunt` | melee | melee | 1 |
| `skitter` | fast | flanker | 1 |
| `bone-archer` | ranged | ranged | 2 |
| `stone-brute` | tank | tank | 2 |
| `cinder-bomb` | exploder | exploder | 2 |
| `hexcaller` | summoner | summoner | 3 |
| `warden-of-ash` | boss | boss | 4 |

The design calls for seven archetypes; the shipped data has all seven as one monster each. The boss rule in the
design is that every boss attack has a telegraph of at least 0.8 seconds, so no damage is unavoidable. Elites are
normal monsters with a stat multiplier (HP x2.5, damage x1.5, radius x1.15) and one random affix, each with a real sim effect
(stage `affixes` in `sim/monsters.js`; data in `RPG.data.eliteAffixes`) and its own outline ring and tint:

| Affix | Effect |
|---|---|
| `fast` | moves 25 % faster |
| `shielded` | a shield worth 30 % of max HP absorbs damage before HP |
| `vampiric` | heals by 50 % of the damage it deals to the player |
| `explosive-death` | on death, one blast of its damage within 70 px if the player is in range; a nova ring and a `hit` sound mark it (w09-01) |
| `regenerating` | heals 4 % of max HP per second |

Covered by `tests/sim/elite-affixes.test.js` and `tests/sim/effects-hardening.test.js`; the effects were checked in tests, not by eye in a browser. Encounter groups are meant to be rolled from a seeded random number generator so a game state can be
reproduced. `world/encounters.js` does that roll; elites are decided per member at spawn time, not per group.

## Combat animations and effects

All drawn in code on the canvas (`render/draw-entities.js`, `render/draw-effects.js`); the renderer draws them in the order floor, `below` effects (telegraphs, ember zones), monsters' projectiles, death ghosts, monsters, player, player projectiles, `above` effects, damage numbers, then the HUD and the hurt flash.

- **Melee swing arc** (`drawSwing`): only for `swing` weapons. Windup shows a faint marker at the start edge, the active phase sweeps a wedge with a bright leading edge across the weapon's arc, recover holds the full arc while it fades. Bow and staff attacks get no swing arc (their projectile or ground area is drawn instead).
- **Death animation** (`drawDying`): a dying monster flashes white, then shrinks and fades over 0.4 s. The sim removes the monster on its death tick, so this is a cosmetic copy in `world.fx.deaths` (capped at 40); it cannot collide or be targeted.
- **Telegraphs**: monster and boss attack warnings (circle, cone, line) and ember zones draw under the entities, so boss attacks are readable before they land.
- **Damage numbers, particles, hurt flash**: numbers obey the `showDamageNumbers` setting; particles are capped by the quality setting; the hurt flash peaks at alpha 0.35 and decays over 0.4 s. There is no full-screen white flash.
- **Hit flash**: a struck sprite flashes, then must stay un-flashed for 0.25 s, so rapid area hits cannot strobe it faster than about 2.7 Hz.
- **Effect cap**: `world.effects` is held to 64 entries (oldest die first).

Status: tests (`tests/render/combat-anims.test.js`, `effects-wiring.test.js`, `effects-hardening.test.js`) and one scripted real-Chrome run show these draw. The swing arc and death animation were not pixel-checked in a browser and nobody has judged how they look.

## Loot

Items have a slot, a base, an item level (the area level at the drop) and a rarity from Common to Legendary. Rarity sets
how many modifiers an item rolls. The design gives none for Common and four or five for Legendary. Modifiers come from
the shipped `modifierPool` in `items.js` (nine modifiers, such as damage, HP, armour, crit, attack speed, move speed and
life steal), with ranges that depend on the item level.

The design stores an item's resolved numbers in the save, so retuning the modifier pool does not change existing items.
Drops come from the monster's loot table, scaled by the difficulty's loot quality. The rolls should use a separate
seeded random stream so loot is repeatable for a given seed. Generation is in `world/loot.js`, drops and pickups in `sim/rewards.js`, and the 24-slot inventory plus eight equipment slots in `ui/inventory.js`.

## Difficulty

Difficulty is a preset or a custom configuration, resolved into one config object that is stored in the save. The
shipped presets are `easy`, `normal`, `hard`, `nightmare`, and `custom` (custom values are clamped to the slider ranges by `clampCustom`). Each preset sets multipliers for enemy HP, enemy damage, enemy speed, spawn density, elite chance, boss HP,
boss telegraph length, loot quality, XP and gold. The field list is `RPG.data.difficultyMultiplierFields`.

Saves keep the resolved config, so changing a preset later does not affect existing saves. The design's player death
penalty is `none`, `gold10` or `gold25` (a fraction of carried gold lost on death: 0, 10% or 25%). Death never deletes
the save.

Changing difficulty during play (Settings) calls `game.setDifficulty`, which runs `areas.rescaleMonsters` on the live
monsters: HP (ratio-preserving, shields included), damage, the stored speed value and the XP/loot/gold multipliers are
multiplied by new/old factor, using the player level and area level the monster spawned with. So **HP, damage and speed
rescale live**, but **spawn count, elite rolls and boss modifiers apply only from the next area entry**: they are decided
at entry and not redone. Speed is real movement speed (since w11-02 the AI velocity is multiplied by `m.speed` over the
definition's base speed); attack cooldowns, windups and telegraphs are not scaled by `enemySpeed`. Summoned minions are not
difficulty-scaled. Monsters are not saved, so saves are unchanged.

### Continue

The main menu's Continue loads the **newest readable save by `savedAt`** across all slots (corrupt slots are skipped;
`ui/mainmenu.js` `latestSave`). It is not "the last slot you used": an area-entry autosave (slot `auto`) that is newer
than a manual slot you just loaded wins the next Continue. Loading a save does not itself rewrite `auto` (w10-03). If
the only slot is corrupt, Continue does nothing and shows no message (open).

## Not implemented

Weapon behaviours other than the four above, the extra weapons, a virtual stick for touch (only four on-canvas buttons exist, untested on touch hardware) and multiplayer. The full design,
including fields not yet in the data, is in [`DATA-DEFINITIONS.md`](DATA-DEFINITIONS.md).

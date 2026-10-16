# Gameplay systems: weapons, skills, monsters, loot, difficulty

A short guide to the five gameplay systems. The full field lists and design intent are in
[`DATA-DEFINITIONS.md`](DATA-DEFINITIONS.md) (§2 difficulty, §3 weapons, §4 skills, §5 monsters, §6 items).

Each system's **data** is shipped in `src/data/` and is tested by `tests/data/`. The **simulation does not use it yet**:
`sim/sim.js` `step` only moves the player and records aim. The helpers in `world/combat.js` and `world/progression.js`
are shipped but not called from the loop. Nothing below changes what the player can do in the game today, except
movement and aim.

## Status at a glance

| System | Data shipped (`src/data/`) | Logic shipped | Not yet wired or written |
|---|---|---|---|
| Weapons | `weapons.js`: `sword`, `hunting-bow`, `arc-staff` | none for attacks; `world/combat.js` has the hit, status and cooldown pipeline attacks will use | attack behaviours (`world/weapons.js`), attack input, weapon switching |
| Skills | `skills.js`: nine skills | none; the `skill1`–`skill7` actions are read as input and nothing consumes them | `world/skills.js`: casting, cooldown ticks, learning, upgrades, passives, respec |
| Monsters | `monsters.js`: six monsters and the boss `warden-of-ash` | none for AI or spawning | `world/ai.js`, `world/boss.js`, encounters, elite affixes |
| Loot | `items.js`: 16 items, 5 rarities, 9 modifiers | none for generation or drops | `world/loot.js`, `world/equipment.js`, inventory |
| Difficulty | `difficulties.js`: `easy`, `normal`, `hard`, `nightmare`, `custom` | none for scaling; `difficulty` is `null` in the shipped world | `world/difficulty.js`, applying the multipliers to spawns |

Also shipped, and not yet called by the simulation: the XP curve and level-up grants (`world/progression.js`), and the
stat model (`world/stats.js`, `StatBlock`) with base values from DATA-DEFINITIONS §1. The player's movement reads
`moveSpeed` from the stat model.

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

The design's `arc` (chain lightning) behaviour and the Mace, Crossbow and Bomb thrower are not in the data yet.

## Skills

Skills have a type (`active` or `passive`) and a category. An active skill has a cost in energy, a cooldown (reduced by
cooldown reduction), a cast time and a targeting mode. Each rank scales the skill's parameters. Upgrades are chosen per
skill and each has its own max rank. Passive skills add modifiers to the player's stats under their own source name.

Shipped skills:

- Active: `fireball`, `whirlwind`, `ground-slam`, `frost-nova`, `shield`, `mend`, `blink-strike`
- Passive: `vitality`, `focus`

Learning a skill needs a level and sometimes another skill at a given rank. Skill points come from levelling (one per
level, with a bonus every five levels). Respec costs gold and refunds all spent points. The keymap binds the skill
actions to `1`–`5`, `Q` and `E`, and `input/intent.js` reports them as edge-triggered flags. Nothing reads them yet.

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
normal monsters with a stat multiplier and one random affix from `fast`, `shielded`, `vampiric`, `explosive-death` or
`regenerating`. Encounter groups are meant to be rolled from a seeded random number generator so a game state can be
reproduced. That roll is not written yet.

## Loot

Items have a slot, a base, an item level (the area level at the drop) and a rarity from Common to Legendary. Rarity sets
how many modifiers an item rolls. The design gives none for Common and four or five for Legendary. Modifiers come from
the shipped `modifierPool` in `items.js` (nine modifiers, such as damage, HP, armour, crit, attack speed, move speed and
life steal), with ranges that depend on the item level.

The design stores an item's resolved numbers in the save, so retuning the modifier pool does not change existing items.
Drops come from the monster's loot table, scaled by the difficulty's loot quality. The rolls should use a separate
seeded random stream so loot is repeatable for a given seed. Item generation, drops and the inventory are not written yet.

## Difficulty

Difficulty is a preset or a custom configuration, resolved into one config object that is stored in the save. The
shipped presets are `easy`, `normal`, `hard`, `nightmare`, and `custom` (custom values are clamped to fixed ranges in
the design). Each preset sets multipliers for enemy HP, enemy damage, enemy speed, spawn density, elite chance, boss HP,
boss telegraph length, loot quality, XP and gold. The field list is `RPG.data.difficultyMultiplierFields`.

Saves keep the resolved config, so changing a preset later does not affect existing saves. The design's player death
penalty is `none`, `gold10` or `gold25` (the design does not spell out the unit beyond the name). Death never deletes
the save.

## Next steps

Wiring these systems into `sim/sim.js` `step` is the next step: attack input and weapon behaviours, skill casting, AI and
spawning, drops, then the HUD and the menu screens. The full design, including fields not yet in the data, is in
[`DATA-DEFINITIONS.md`](DATA-DEFINITIONS.md).

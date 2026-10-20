# Data definitions and stat model

All definitions are frozen plain objects in `src/data/*.js`, registered on `RPG.data.<kind>` keyed by `id`.
They contain data and string keys referencing behaviours only — no functions, no rendering code
(rendering looks up `shape`/`color` tokens). `RPG.data.validate()` checks every field below and all cross-references.

## 1. StatMap (player stat model, extensible)

| Stat key | Meaning | Base (level 1) | Notes |
|---|---|---|---|
| `maxHp` | Maximum HP | 100 | +12 per level |
| `maxResource` | Max energy (resource) | 60 | +3 per level |
| `damage` | Base attack damage added to weapon damage | 4 | +1.2 per level |
| `attackSpeed` | Attack-rate multiplier | 1.0 | weapon base interval / this |
| `moveSpeed` | px/s | 170 | cap 320 |
| `armor` | Flat armor | 5 | `reduction = armor/(armor+ 50+10*attackerLevel)`, cap 0.85 |
| `critChance` | 0..1 | 0.05 | cap 0.75 |
| `critDamage` | multiplier | 1.5 | |
| `lifeRegen` | HP/s | 1.0 | |
| `resourceRegen` | energy/s | 6.0 | |
| `cooldownReduction` | 0..1 | 0 | cap 0.5; cooldown = base*(1-CDR) |
| `lifeSteal` | fraction of damage dealt → HP | 0 | cap 0.25 |
| `skillDamage` | % more skill damage | 0 | multiplicative on skills only |
| `resFire` `resCold` `resLightning` `resPoison` `resArcane` | 0..0.75 resist | 0 | `resPhys` not used (armor) |
| `thorns` | reflect flat damage | 0 | |
| `pickupRadius` | px | 40 | |
| `goldFind` `xpGain` | fraction bonus | 0 | |

Plus level-driven attributes (v1 keeps a classless 3-attribute core so equipment/levels have stable hooks):
`might` (+0.5 damage, +2 maxHp per point), `agility` (+0.3% crit, +0.5% attackSpeed), `spirit` (+1.5 maxResource, +0.1 resourceRegen).
Level-up grants 3 attribute points… **v1 simplification:** attributes auto-grow per class growth table; the character screen
shows them read-only (manual allocation = later content, hook: `player.attrPoints`).

### Computation (world/stats.js)
For each stat `s`: `derived[s] = (base[s] + Σ flat) * (1 + Σ inc) * Π (1 + more_i)`, then clamped to the cap.
Modifier = `{ source, stat, kind:'flat'|'inc'|'more', value }`. Sources: `base`, `level`, `item:<slot>`, `skill:<id>`, `buff:<id>`,
`difficulty` (monsters only). `removeModifiersBySource` is how unequip, buff expiry and respec work. Monsters use the same `StatBlock`
so a future class/ally system needs no new machinery.

### ClassDefinition (hook; one entry in v1)
```
id:'wayfarer', name, baseStats:Partial<StatMap>, growth:Partial<StatMap> /*per level*/, resourceKind:'energy',
startWeapons:[weaponId x3 with ilvl 1], startSkills:[skillId], skillBar:[...]
```

## 2. DifficultyDefinition

```
id: string                      'easy'|'normal'|'hard'|'nightmare'|'custom'
name: string
enemyHp: number                 multiplier   (easy .7, normal 1, hard 1.3, nightmare 2.0)
enemyDamage: number             multiplier   (.55, 1, 1.2, 1.7)
enemySpeed: number              multiplier   (.9, 1, 1.1, 1.2; clamp .5..1.5)
spawnDensity: number            multiplier on encounter budget (.7, 1, 1.15, 1.45)
eliteChance: number             0..1 per group (.05, .10, .16, .25)
bossHp: number                  multiplier on the Warden's HP, applied on top of enemyHp (.7, 1, 1.25, 1.5)
bossTelegraph: number           multiplier on telegraph duration (1.3, 1, .85, .7) — never < 0.5 s absolute
lootQuality: number             rarity-roll boost and drop count (0.8, 1, 1.25, 1.4)
xpMultiplier: number            (.9, 1, 1.25, 1.6)
goldMultiplier: number          (.9, 1, 1.25, 1.5)
playerDeathPenalty: 'none'|'gold10'|'gold25'     // respawn in town; never deletes the save
```
`custom` = same fields, each slider-clamped (`enemyHp .25..4`, `enemyDamage .25..4`, `enemySpeed .5..1.5`, `spawnDensity .25..3`,
`eliteChance 0..0.6`, `lootQuality .5..3`, `xpMultiplier .25..4`). `bossHp`, `bossTelegraph` and `goldMultiplier` are not bounded
in the original spec; `src/data/difficulties.js` uses `bossHp .25..4`, `bossTelegraph .5..1.5`, `goldMultiplier .25..4`
(closest analogous bounds). `DifficultyConfig` is the resolved object stored in the save
(so edits to presets later do not retro-change a save). `RPG.data.difficultyMultiplierFields` lists the ten numeric fields above.

## 3. WeaponDefinition

```
id, name, category: 'melee'|'ranged'|'area'
behavior: 'swing'|'thrust'|'smash'|'projectile'|'spread'|'beam'|'arc'|'aoeGround'|'explosive'   // implemented in world/weapons.js: swing, projectile, spread, aoeGround only (thrust, smash, beam, arc, explosive are (planned); tryAttack refuses them)
baseDamage: [min, max]
damageType: 'phys'|'fire'|'cold'|'lightning'|'poison'|'arcane'
attackInterval: number          seconds between attacks at attackSpeed 1
windup, active, recover: number seconds (anim phases; damage lands at start of `active`)
range: number                   px reach (melee) or max travel
arc: number                     radians (melee cone) | spread cone for 'spread'
aoeRadius: number               px (area/explosive impact); 0 if none
projectile: { speed, radius, lifetime, pierce:int, bounce:int, homing:number, gravity?:0, shape, trail:bool } | null
projectileCount: int            (spread / multishot)
knockback: number               impulse px/s
critChanceBonus: number         added to player's critChance for this weapon
critMultBonus: number
resourceCost: number            per attack
moveSlowWhileAttacking: number  0..1
status: StatusSpec[]            e.g. {id:'burn', chance:.25, duration:3, dps:5}
tags: string[]                  for skill/affix interaction ('blade','bow','staff','heavy')
sfx: { attack, hit }            audio names
color: token                    palette key for swing/projectile
```
Starter set as shipped in `src/data/weapons.js` (distinct feel, not renamed stats):
- **Sword** (`swing`, melee, 8–12 phys, 0.5 s interval, 56 px reach, 1.9 rad cone, knockback 240, +5% crit chance, no energy cost).
- **Hunting Bow** (`projectile`, ranged, 5–8 phys, 0.8 s interval, 420 px range, one arrow at 620 px/s with `pierce: 1`, +10% crit chance,
  +25% crit multiplier, **costs 2 energy per shot**, knockback 90).
- **Arc Staff** (`aoeGround`, area, 10–15 lightning, 1.1 s interval, 300 px range, targets a ground point; a 90 px circle is the telegraph
  and hit area; every monster inside takes the hit; costs 8 energy; applies `slow` 0.4 for 2.5 s with `stacks: 'refresh'`; moves the player at 0.6 slow while attacking).
  The `arc` chain behaviour (hits up to 3 targets with falloff) is **(planned)**: the shipped Arc Staff uses `aoeGround`, not `arc`.

Stretch (planned, no data yet): Mace (`smash`, big knockback, stun chance), Crossbow, Bomb thrower (`explosive`).

## 4. SkillDefinition

```
id, name, category: 'offensive'|'defensive'|'regenerative'|'mobility'|'control'
type: 'active'|'passive'
icon: { glyph:string, color:token }              // canvas-drawn glyph, no images
maxRank: int                                      // base-skill ranks (scales params)
cost: number (energy)       cooldown: number (s, before CDR)      castTime: number
targeting: 'self'|'aim'|'ground'|'nearest'|'cone'   // shipped: self, aim, ground; nearest and cone are (planned)
effect: 'projectile'|'aoe'|'nova'|'buff'|'heal'|'dash'|'teleport'|'chain'|'summonZone'|'whirl'   // shipped in src/data: projectile, aoe, nova, buff, heal, dash, whirl; teleport, chain, summonZone (planned)
params: { damageMult, radius, duration, speed, count, pierce, heal, shield, slow, stunDur, ... }   // meaning depends on effect
rankScaling: { paramName: perRankDelta }   // active: added to params per rank above 1. passive: keyed by modifier stat (see conventions below)
requires: { level:int, skills?: {skillId:rank} }                              // unlock requirements; `points?` is (planned), no shipped skill uses it
tier: int                                          // column in skill screen
upgrades: UpgradeDefinition[]                      // 2-3 mutually-inclusive options each with maxRank
passiveMods?: Modifier[]                           // for type 'passive', pushed into StatBlock (source 'skill:<id>')
tags: string[]
sfx: string
```
`UpgradeDefinition = { id, name, desc, maxRank, cost /*skill points per rank*/, requiresRank /*of parent*/, paramDeltas:{param: delta|{mult}}, addStatus?:StatusSpec, passiveMods?:Modifier[] }`.

Starter set as shipped in `src/data/skills.js`: nine skills (≥ 5 required). Upgrades are listed per skill.
- **Fireball** (`fireball`, offensive, active, `projectile` + aoe; upgrades Bigger Explosion, Burning Damage, Multiple Projectiles).
- **Whirlwind** (`whirlwind`, offensive, active, `whirl`, self; Larger Radius, Increased Damage, Life Steal).
- **Ground Slam** (`ground-slam`, control, active, `aoe` on the ground, stun; Wider Impact, Lingering Stun).
- **Frost Nova** (`frost-nova`, control, active, `nova`, slow + freeze; Deeper Chill, Deep Freeze).
- **Shield** (`shield`, defensive, active, `buff`; Longer Duration, Reflect Damage, Heal on Expiration).
- **Mend** (`mend`, regenerative, active, `heal` over time; Swift Recovery, Emergency Recovery).
- **Blink Strike** (`blink-strike`, mobility, active, `dash`; Extended Blink, Strike on Arrival).
- **Vitality** (`vitality`, defensive, passive, +maxHp, +lifeRegen; Hardy, Second Wind).
- **Focus** (`focus`, regenerative, passive, +resourceRegen, +cooldownReduction; Clear Mind, Composure).

Shipped passive conventions (from the header of `src/data/skills.js`):
- Passive skills set `cost: 0`, `cooldown: 0`, `castTime: 0`, `targeting: null`, `effect: null`, `params: {}`.
- `passiveMods[].value` is the value at rank 1. `rankScaling[stat]` is added to that value for each rank above 1, keyed by the modifier's stat.
- Upgrade `passiveMods[].value` is added per rank of the upgrade.
- `passiveMods` carry no `source`; the engine stamps `skill:<id>` when it pushes them into a StatBlock.
- Upgrade `paramDeltas[param]` is a per-rank delta, or `{ mult }` for a per-rank multiplier. `slow` is the fraction of movespeed removed (0..1);
  `stunDur`, `duration` and `tickInterval` are seconds.

Skill points: 1 per level, +1 bonus every 5 levels; respec costs gold (`50 * level`) and refunds all spent points, clears bar entries for removed skills.
(Point and respec rules are engine behaviour in `src/world/skills.js`, not `src/data`; not re-verified in this file.)

## 5. MonsterDefinition

```
id, name, archetype: 'melee'|'fast'|'ranged'|'tank'|'exploder'|'summoner'|'boss'
tier: 1|2|3|4(boss)               areas: string[]  (allowed area ids)
shape: 'blob'|'dart'|'archer'|'brute'|'bomb'|'caster'|'boss-colossus'     // distinct silhouette (not recolours)
radius: number   color: token   outline: token   eyes: bool
baseStats: { hp, damage, speed, armor, resists?:{fire,...} }
perLevel: { hp, damage }          // growth multipliers per area level
ai: 'melee'|'flanker'|'ranged'|'tank'|'exploder'|'summoner'|'boss'
aiParams: { aggroRange, leashRange, attackRange, attackCooldown, windup, preferredDistance?, flankAngle?, fuse?, blastRadius?,
            summonDefId?, summonInterval?, summonMax?, retreatBelowHp? }
attacks: AttackDef[]              // see below; ranged/boss use projectiles & telegraphs
xp: number   gold: [min,max]
lootTable: { id, dropChance, rolls, rarityBoost, guaranteed?: itemId[] }   // id names the loot table; boss ships guaranteed: []
sfx: { hit, death, attack }
deathAnim: 'pop'|'shatter'|'dissolve'|'explode'
spawnWeight: number   groupSize: [min,max]   eliteAllowed: bool
```
`AttackDef = { id, kind:'melee'|'projectile'|'aoe'|'nova'|'charge'|'summon', damageMult, range, radius, windup, telegraph?:'circle'|'cone'|'line'|'ring', projectile?, status?:StatusSpec[], weight, minPhase }`

`BossDefinition extends MonsterDefinition`: `phases: [{ hpBelow:0..1, attacks:[attackId], speedMult, special?:'adds'|'shield'|'enrage'|'zones' }]`
(shipped specials: `adds`, `enrage`; `shield` and `zones` are (planned)),
`mechanic: { id, desc, params }`, `arenaAreaId`, `modifierPool: string[]` (seed-rolled boss modifiers), `rewards: { guaranteedRarity:'epic', gold, xp }`.
Rule: every boss `AttackDef` has `telegraph` with `windup >= 0.8 s`, enforcing no unavoidable damage. This is checked by `tests/data/monsters.test.js`
(`BOSS_MIN_WINDUP`), not by `validate()`.

Starter monsters as shipped in `src/data/monsters.js` (≥ 5 plus boss; all 7 archetypes are present): Husk Grunt (melee), Skitter (fast/flanker),
Bone Archer (ranged), Stone Brute (tank), Cinder Bomb (exploder), Hexcaller (summoner), Warden of Ash (boss).
- **Base XP per kill** (before difficulty/level scaling): Husk Grunt 40, Skitter 30, Bone Archer 70, Stone Brute 120, Cinder Bomb 60, Hexcaller 150, Warden of Ash 400.
  Monsters do not respawn within a visit, so these are tuned to the XP curve (`100 * L^1.5` per level; 903 XP total for level 4, 2821 for level 6):
  a seeded normal-difficulty forest holds ~1500-1620 XP and the caves ~2700-3100 XP, so clearing about 60% of the forest opens the caves gate and
  about 45% of the caves then opens the arena gate (`node scripts/balance.mjs`, "Gate route").
- **Hexcaller** summons **Skitters**, not Imps. There is no Imp monster in `src/data`. `call-skitters` is a `summon` attack with `summonDefId: 'skitter'`;
  `aiParams` sets `summonInterval: 6`, `summonMax: 4`, `retreatBelowHp: 0.3`. Its bolt `hex-bolt` is a homing projectile.
- **Warden of Ash** (`warden-of-ash`, 1800 HP base, arena `arena`) has four attacks: `ember-circles` (aoe, from phase 1), `ash-charge` (charge, from phase 2),
  `ash-add-wave` (summons Husk Grunts, from phase 2), `ring-of-ash` (nova, from phase 3). Phases: 100% HP (`ember-circles`, speed ×1);
  below 66% (adds, speed ×1.15); below 33% (enrage, speed ×1.3). Mechanic `cinder-brand`: every third ember circle is brighter, lasts 4 s,
  and opens a 2 s vulnerable window. "Rotating zones" are **(planned)**; no shipped phase uses the `zones` special.

Elite affixes live in `RPG.data.eliteAffixes` (`src/data/monsters.js`, declaration order = roll order); `ELITE_AFFIXES` in `src/world/encounters.js` is its key list. Each entry is `{ id, name, color, <effect> }`; `color` is a render token (`affix-*`) for the affix outline ring and tint. Effects, read by `src/sim/monsters.js` (stage `affixes`, between `statuses` and `resolve`) and `hurtPlayer`:

| id | effect field | behaviour |
|---|---|---|
| `fast` | `speedMult: 1.25` | moves 25 % faster |
| `shielded` | `shieldFraction: 0.3` | a shield of 30 % max HP soaks damage before HP (`m.shield`) |
| `vampiric` | `leechFraction: 0.5` | heals by half of the damage it deals to the player (melee, area and shots) |
| `explosive-death` | `deathRadius: 70`, `deathDamageMult: 1` | on death, one fire blast of `damage × mult` if the player is within the radius |
| `regenerating` | `regenPerSecond: 0.04` | heals 4 % of max HP per second |

Elite scaling is `ELITE` in `src/world/world.js`: `hp ×2.5`, `damage ×1.5`, `radius ×1.15`. Each elite rolls one random affix from `ELITE_AFFIXES` (seeded: one seed gives the same elites and affixes).

## 6. ItemDefinition and ItemInstance

```
ItemDefinition: id, name, slot: 'weapon'|'offhand'|'head'|'chest'|'legs'|'boots'|'ring'|'amulet'|'consumable'|'currency',
  // shipped `RPG.data.itemSlots` lists the 8 equipment slots; `consumable` is used as an item slot; `currency` is (planned, no shipped item)
  weaponId?: string,                 // for slot 'weapon' links to WeaponDefinition
  base: { stats: Partial<StatMap> }, // implicit base stats (e.g. leather vest: armor 8)
  ilvlMin, ilvlMax, weight /*drop weight*/, tier, icon:{glyph,color}, stackable?, consumable?:{effect:'heal'|'resource', amount}
  // `item()` defaults: stackable false, consumable null, weaponId null.
Shipped items (16, `src/data/items.js`): weapons Sword, Hunting Bow, Arc Staff (ilvl 5–30); Wooden Buckler; Leather Cap, Iron Helm;
  Leather Vest, Chain Mail; Leather Leggings, Chain Leggings; Leather Boots; Copper Ring, Jade Ring; Bone Amulet;
  consumables Health Potion (heal 40) and Energy Potion (resource 30), both stackable. ilvl ranges: most 1–30; Arc Staff 5–30;
  leather head/chest/legs pieces 1–15; iron, chain and Jade Ring 10–30.
RarityDefinition: id, name, color, modCount:[min,max], modTierBoost, dropWeight, nameStyle, rank, uniqueProperty
   common 0-0 | uncommon 1-2 | rare 2-3 | epic 3-4 | legendary 4-5 (+1 unique property; `uniqueProperty: true` only on legendary)
   shipped `modTierBoost` 0/0/1/2/3 and `dropWeight` 600/260/100/30/10 for common..legendary.
ModifierPool: id ('dmg','hp','armor','crit','attackSpeed','moveSpeed','skillDmg','resRegen','lifeSteal' shipped; 'cdr','fireRes' (planned)),
  stat, kind ('flat'|'inc'), weight, allowedSlots[], tags,
  ranges: [{ ilvlMin, min, max }] one roll range per ilvl bracket starting at ilvl 1, 11, 21 (`ILVL_BRACKET_STARTS`)
ItemInstance (saved): { uid:int, baseId, ilvl, rarity, mods:[{id, stat, kind, value}], name, seedTag /*rng stream tag for repro*/, qty?:int, locked?:bool }
```
Instance name = `<rarity-affix> <base name>`; `ilvl` = area level at drop. `mods` store resolved numbers, so a save never depends on
modifier-pool tuning. Comparison (`equipment.compare`) diffs `recomputed derived stats` with the candidate swapped in.

## 7. AreaDefinition

```
id, name, order:int, level:[min,max], theme:'town'|'forest'|'caves'|'ruins'|'dungeon'|'arena'   // ruins, dungeon: (planned)
kind: 'authored'|'cave'|'rooms'|'open'      size:[w,h] tiles      layout?: string[]  /*ASCII for authored*/   // rooms: (planned)
safe: bool  (town: no spawns, vendor/portal)  
spawnTable: [{ monsterId, weight, min, max }]   budget: number (encounter points per 100 tiles)   eliteChance: number
boss?: { bossId, slot:'arena-center' }          exits: [{ to: areaId, requires?: { bossesDefeated?:[id], level?:int } }]
palette: { floor, floorAlt, wall, accent, ambient }    decor: [{ kind, density }]
```
Shipped areas (`src/data/areas.js`, four): Hollow Hearth (`town`, authored, safe, 36×18, level 1–2) → Whispering Forest (`forest`, open, 40×30,
level 2–4, budget 40, eliteChance 0.08) → Hollow Caves (`caves`, cave, 48×36, level 4–6, budget 60, eliteChance 0.12) → Ashen Arena (`arena`,
authored, 24×18, level 6–8, holds `warden-of-ash` at `arena-center`, budget 0). Exits: town→forest; forest→caves requires level 4;
caves→arena requires level 6; arena→caves.
Planned, not shipped: Ashen Ruins / Dungeon (`rooms` kind, `ruins` / `dungeon` themes).
`src/data/maps/test-map.js` is a 40×30 ASCII test fixture (`RPG.data.maps`), not a playable area.
Milestone minimum (Town + Forest + Caves + Arena) is met by the shipped set.

## 8. StatusSpec
`{ id:'slow'|'stun'|'freeze'|'root'|'burn'|'poison'|'shield'|'haste'|'armorUp'|'reflect'|'invuln', duration, strength?, dps?, chance?:0..1, stacks?:'refresh'|'stack'|'max' }`
Ids referenced by shipped `src/data` as statuses: `slow` (Arc Staff), `burn` (Fireball upgrade), `freeze` (Frost Nova upgrade), `reflect` (Shield upgrade).
Frost Nova's slow is the `slow` param (a fraction), not a status.
The other ids are (planned) until some data uses them.

## 9. ProjectileSpec / EffectSpec (runtime, not saved)
`ProjectileSpec = { x,y,angle,speed,radius,lifetime,damage:DamageSpec,team,pierce,bounce,homing,onHit?:'explode'|'chain'|'none',aoeRadius,shape,color,trail }`
`EffectSpec = { type:'aoe'|'telegraph'|'text'|'particle'|'zone'|'ring'|'arc', x,y,r,duration,color, damage?:DamageSpec, delayDamageAt?:t, tickEvery? }`

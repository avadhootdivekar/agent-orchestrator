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
enemyHp: number                 multiplier   (easy .7, normal 1, hard 1.5, nightmare 2.3)
enemyDamage: number             multiplier   (.7, 1, 1.4, 2.0)
enemySpeed: number              multiplier   (.9, 1, 1.1, 1.2; clamp .5..1.5)
spawnDensity: number            multiplier on encounter budget (.7, 1, 1.3, 1.7)
eliteChance: number             0..1 per group (.05, .10, .20, .30)
bossHp: number                  multiplier (stacks over enemyHp)
bossTelegraph: number           multiplier on telegraph duration (1.3, 1, .85, .7) — never < 0.5 s absolute
lootQuality: number             rarity-roll boost and drop count (0.8, 1, 1.25, 1.6)
xpMultiplier: number            (.9, 1, 1.25, 1.6)
goldMultiplier: number
playerDeathPenalty: 'none'|'gold10'|'gold25'     // respawn in town; never deletes the save
```
`custom` = same fields, each slider-clamped (`enemyHp .25..4`, `enemyDamage .25..4`, `enemySpeed .5..1.5`, `spawnDensity .25..3`,
`eliteChance 0..0.6`, `lootQuality .5..3`, `xpMultiplier .25..4`). `DifficultyConfig` is the resolved object stored in the save
(so edits to presets later do not retro-change a save).

## 3. WeaponDefinition

```
id, name, category: 'melee'|'ranged'|'area'
behavior: 'swing'|'thrust'|'smash'|'projectile'|'spread'|'beam'|'arc'|'aoeGround'|'explosive'   // implemented in world/weapons.js
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
Starter set (distinct feel, not renamed stats): **Sword** (fast cone `swing`, small knockback), **Hunting Bow** (`projectile`,
pierce 1, no cost but longer interval, energy-free, crit bonus), **Arc Staff** (`arc` chain lightning: hits up to 3 targets
with falloff, costs energy, applies `slow`). Stretch: Mace (`smash`, big knockback, stun chance), Crossbow, Bomb thrower (`explosive`).

## 4. SkillDefinition

```
id, name, category: 'offensive'|'defensive'|'regenerative'|'mobility'|'control'
type: 'active'|'passive'
icon: { glyph:string, color:token }              // canvas-drawn glyph, no images
maxRank: int                                      // base-skill ranks (scales params)
cost: number (energy)       cooldown: number (s, before CDR)      castTime: number
targeting: 'self'|'aim'|'ground'|'nearest'|'cone'
effect: 'projectile'|'aoe'|'nova'|'buff'|'heal'|'dash'|'teleport'|'chain'|'summonZone'|'whirl'   // implemented in world/skills.js
params: { damageMult, radius, duration, speed, count, pierce, heal, shield, slow, stunDur, ... }   // meaning depends on effect
rankScaling: { paramName: perRankDelta }
requires: { level:int, skills?: {skillId:rank}, points?:int }                // unlock requirements
tier: int                                          // column in skill screen
upgrades: UpgradeDefinition[]                      // 2-3 mutually-inclusive options each with maxRank
passiveMods?: Modifier[]                           // for type 'passive', pushed into StatBlock (source 'skill:<id>')
tags: string[]
sfx: string
```
`UpgradeDefinition = { id, name, desc, maxRank, cost /*skill points per rank*/, requiresRank /*of parent*/, paramDeltas:{param: delta|{mult}}, addStatus?:StatusSpec, passiveMods?:Modifier[] }`.

Starter set (≥ 5 required, 7 planned): **Fireball** (offensive/projectile + aoe; upgrades Bigger Explosion, Burning Damage, Multiple Projectiles),
**Whirlwind** (offensive/whirl; Larger Radius, Increased Damage, Life Steal), **Ground Slam** (control/aoe + stun), **Frost Nova** (control/nova + slow/freeze),
**Shield** (defensive/buff; Longer Duration, Reflect Damage, Heal on Expiration), **Mend** (regenerative/heal; heal over time + Emergency Recovery upgrade),
**Blink/Dash Strike** (mobility/dash), passive **Vitality** (+maxHp, +lifeRegen), passive **Focus** (+resourceRegen, +CDR).

Skill points: 1 per level, +1 bonus every 5 levels; respec costs gold (`50 * level`) and refunds all spent points, clears bar entries for removed skills.

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
lootTable: { dropChance, rolls, rarityBoost, guaranteed?: itemId[] }
sfx: { hit, death, attack }
deathAnim: 'pop'|'shatter'|'dissolve'|'explode'
spawnWeight: number   groupSize: [min,max]   eliteAllowed: bool
```
`AttackDef = { id, kind:'melee'|'projectile'|'aoe'|'nova'|'charge'|'summon', damageMult, range, radius, windup, telegraph?:'circle'|'cone'|'line'|'ring', projectile?, status?:StatusSpec[], weight, minPhase }`

`BossDefinition extends MonsterDefinition`: `phases: [{ hpBelow:0..1, attacks:[attackId], speedMult, special?:'adds'|'shield'|'enrage'|'zones' }]`,
`mechanic: { id, desc, params }`, `arenaAreaId`, `modifierPool: string[]` (seed-rolled boss modifiers), `rewards: { guaranteedRarity:'epic', gold, xp }`.
Rule: every boss `AttackDef` has `telegraph` with `windup >= 0.8 s` (checked by `validate()`), enforcing no unavoidable damage.

Starter monsters (≥ 5 plus boss, all 7 archetypes): Husk Grunt (melee), Skitter (fast/flanker), Bone Archer (ranged), Stone Brute (tank), Cinder Bomb (exploder),
Hexcaller (summoner → summons Imps), Warden of Ash (boss: 3 phases, ground circles, charge, add-wave, rotating zones).
Elite affixes (from `ELITE_AFFIXES`): modifiers on top: `x2.5 hp, x1.4 dmg`, one random affix, rarity boost +1, name prefix.

## 6. ItemDefinition and ItemInstance

```
ItemDefinition: id, name, slot: 'weapon'|'offhand'|'head'|'chest'|'legs'|'boots'|'ring'|'amulet'|'consumable'|'currency',
  weaponId?: string,                 // for slot 'weapon' links to WeaponDefinition
  base: { stats: Partial<StatMap> }, // implicit base stats (e.g. leather chest: armor 8)
  ilvlMin, ilvlMax, weight /*drop weight*/, tier, icon:{glyph,color}, stackable?, consumable?:{effect:'heal'|'resource', amount}
RarityDefinition: id, name, color, modCount:[min,max], modTierBoost, dropWeight, nameStyle
   common 0-0 | uncommon 1-2 | rare 2-3 | epic 3-4 | legendary 4-5 (+1 unique property)
ModifierPool: id ('dmg','hp','armor','crit','attackSpeed','moveSpeed','skillDmg','resRegen','lifeSteal','cdr','fireRes',...),
  stat, kind, range:[min,max] per ilvl bracket, weight, allowedSlots[], tags
ItemInstance (saved): { uid:int, baseId, ilvl, rarity, mods:[{id, stat, kind, value}], name, seedTag /*rng stream tag for repro*/, qty?:int, locked?:bool }
```
Instance name = `<rarity-affix> <base name>`; `ilvl` = area level at drop. `mods` store resolved numbers, so a save never depends on
modifier-pool tuning. Comparison (`equipment.compare`) diffs `recomputed derived stats` with the candidate swapped in.

## 7. AreaDefinition

```
id, name, order:int, level:[min,max], theme:'town'|'forest'|'caves'|'ruins'|'dungeon'|'arena'
kind: 'authored'|'cave'|'rooms'|'open'      size:[w,h] tiles      layout?: string[]  /*ASCII for authored*/
safe: bool  (town: no spawns, vendor/portal)  
spawnTable: [{ monsterId, weight, min, max }]   budget: number (encounter points per 100 tiles)   eliteChance: number
boss?: { bossId, slot:'arena-center' }          exits: [{ to: areaId, requires?: { bossesDefeated?:[id], level?:int } }]
palette: { floor, floorAlt, wall, accent, ambient }    decor: [{ kind, density }]
```
Areas v1: Town (authored, safe) → Whispering Forest (open) → Hollow Caves (cave) → Ashen Ruins/Dungeon (rooms) → Boss Arena (authored).
Minimum for the milestone: Town + Forest + Caves + Arena.

## 8. StatusSpec
`{ id:'slow'|'stun'|'freeze'|'root'|'burn'|'poison'|'shield'|'haste'|'armorUp'|'reflect'|'invuln', duration, strength?, dps?, chance?:0..1, stacks?:'refresh'|'stack'|'max' }`

## 9. ProjectileSpec / EffectSpec (runtime, not saved)
`ProjectileSpec = { x,y,angle,speed,radius,lifetime,damage:DamageSpec,team,pierce,bounce,homing,onHit?:'explode'|'chain'|'none',aoeRadius,shape,color,trail }`
`EffectSpec = { type:'aoe'|'telegraph'|'text'|'particle'|'zone'|'ring'|'arc', x,y,r,duration,color, damage?:DamageSpec, delayDamageAt?:t, tickEvery? }`

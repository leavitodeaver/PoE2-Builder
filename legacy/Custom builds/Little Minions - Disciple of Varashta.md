# Little Minions — Disciple of Varashta

> **Research snapshot:** Path of Exile 2 patch 0.5.4f, audited 2026-08-31  
> **Reset target:** 0.5.5 on 2026-09-04; the 0.5.5 patch notes were not published at the time of this audit  
> **Status:** manually calculated chase-gear specification; mandatory live retest after 0.5.5  
> **Ascendancy:** Disciple of Varashta  
> **Combat buttons:** exactly two — Ember Fusillade and Kelari's Deception  
> **Banned:** every Skeleton, Companion, Wolf, Spectre, and Wardbound Minions

## Verdict

This is the first version of the little-minion concept for which the requested
1.5-million boss-DPS target can be supported by a transparent calculation.
The conditional Deception-only figures are:

- **1,715,528 sustained DPS off Archon** against the stated reference Unique
  after Despair and full Armour Break.
- **2,769,190 probability-weighted DPS** in **Deception-priority mode**, using
  Archon's cooldown bonus so aggressively that Ember/cloud upkeep is largely
  starved during Archon windows.
- **1,851,897 off-Archon / 2,989,316 weighted** in that same mode if
  Upwelling II is active.

For actual cloud-preserving play, hold Deception at its off-Archon 0.644/s
cadence even while Archon is active. That produces approximately **1,820,894
weighted Deception DPS**, or **1,965,639 with Upwelling**, before adding the
modeled 53k–119k cloud scenario band below. The 2.989M number is not
“more debuffs”; it is a Deception-spam operating mode.

The 1.716M proof line counts **only Kelari's Deception**. A separate manual
estimate below puts the modeled small-minion cloud at approximately
**53k–108k DPS** without Upwelling, or **58k–119k** with it, under the stated
resistance and population assumptions. The low edge credits Azmerian at zero;
the high edge requires the unresolved full Azmerian level/support leak.
Kelari's persistent attacks, clone attacks, Critical Weakness, Shock, Wither,
and all on-kill effects remain valued at zero.

The number is conditional, not a universal tooltip claim. It requires the
level-36 Kelari package, all five Deception supports functioning, a perfect
**Very Large** Time-Lost jewel affecting all 15 intended notables, full 300% consecutive-use
ramp, Kelari and the boss inside Presence, Despair on a target assumed to have
50% base Chaos resistance, and the blast actually landing. Full Armour Break
is established after the opening hits, not on the first hit.

### Concept compromise

The surrounding cloud is made from small flying/transient creatures, but its
boss-killing anchor is **Kelari, a full-size Djinn**. This is the route selected
because Disciple of Varashta was specifically raised as a candidate and is the
only presently audited route that clears 1.5M. If the rule is instead that
*every* minion, including the damage anchor, must be tiny and flying, this
version does not qualify; no tiny-only route has been honestly demonstrated at
1.5M in current data.

## Constraint check

| Requirement | Result |
|---|---|
| Little-minion cloud | 12 Raging Spirits, 3 Ravenous Swarms, up to 8 Azmerian Swarms, and ephemeral Living Lightning |
| Maximum two buttons | **Ember Fusillade + Kelari's Deception** |
| Disabled weapon-set exploit | **Required:** Periphery is deliberately invalidated by a Limited-1 augment collision |
| Defensive resource above 4,500 | **CI; 5,746 ES conditional purchase-floor model before the stronger body roll, 6,000–8,000 target** |
| Resistances | Fire/Cold/Lightning must each display 75%; CI gives Chaos immunity |
| Tankiness | Pure ES, CI, Sacred Rituals Armour conversion, Fourth Teaching recharge, ailment/stun charms |
| Boss DPS | 1.716M conditional Deception-only off-Archon proof; approximately 1.874M–1.929M modeled combined band in cloud-preserving play before Upwelling, with the upper edge requiring the unresolved Azmerian leak |
| Banned actors | No Skeleton, Companion, Wolf, Spectre, or Wardbound Minion |

Persistent skills are enabled during setup and do not add combat key presses.

## Ascendancy — exact eight allocations

Allocate these eight nodes:

1. Barya of Kelari
2. Kelari's Deception
3. the small Area-of-Effect passive toward Baryanic Leylines
4. Baryanic Leylines
5. the first 20% increased Energy Shield small passive
6. Fourth Teaching
7. the second 20% increased Energy Shield small passive
8. Sacred Rituals

The important effects are:

- **Barya of Kelari:** grants the persistent Kelari minion.
- **Kelari's Deception:** grants the boss-damage Command skill.
- **Baryanic Leylines:** increases the radius of non-Unique Time-Lost Jewels by
  40%. Together with the Very Large crafted prefix below, this lets the jewel
  cover 15 allocated notables.
- **Fourth Teaching:** shortens Energy Shield recharge delay by one second and
  gives 40% more recharge rate while on Low Energy Shield.
- **Sacred Rituals:** adds 60% of current Energy Shield to Armour for Physical
  Damage Reduction calculations.

Source: https://poe2db.tw/us/Disciple_of_Varashta

## The two-button loop

### Button 1 — Ember Fusillade

Use a level-20, q20 Ember Fusillade with:

1. Living Lightning II
2. Lightning Attunement
3. Rapid Casting II
4. Compressed Duration II
5. Wildshards II

Lightning Attunement adds 25% of Ember's damage as Lightning without removing
its Fire identity. The same self-cast spell can therefore create Raging
Spirits and trigger Living Lightning II; no Lightning Infusion or Verisium
Infusion is required.

At q20 on a Disciple, Ember does **not** receive Advanced Thaumaturgy's
cast-speed/duration penalty. Rapid Casting II and the two Evergrasping
implicits supply 20% increased Cast Speed each in total buckets, giving
`0.40 / (1 + 0.20 + 0.20) = 0.2857 seconds` cast time.
Compressed Duration II reduces hover duration to
`1.30 × 0.65 = 0.845 seconds`. Wildshards II has a 25% chance to add eight
projectiles; that proc is never treated as deterministic.

Ember must be used in a **cast-then-release cycle**. Recasting resets the hover
duration of every accumulated Ember, so three casts per second does not mean
three hits per second while the button is held. The ordinary q20
Raging-Spirit limit is 12 and their duration is five seconds, but maintaining
all 12 with Ember's actual release cadence is a mandatory live test, not a
proved population. “Maximum two Spirits per cast” is only a ceiling.

Sources:

- https://poe2db.tw/us/Ember_Fusillade
- https://poe2db.tw/us/Lightning_Attunement
- https://poe2db.tw/us/Rapid_Casting_II
- https://poe2db.tw/us/Compressed_Duration_II
- https://poe2db.tw/us/Wildshards_II
- https://poe2db.tw/us/Living_Lightning_II

### Button 2 — Kelari's Deception

Use Kelari's Deception with:

1. Bidding II — 30% more damage and 30% increased Command cooldown recovery
2. Commandment — 100% increased Command cooldown recovery
3. Deliberation — 20% more damage
4. Concentrated Area — 30% more Area damage and 50% less Area of Effect
5. Uul-Netol's Embrace — 40% of Physical gained as Chaos and Armour Break
   equal to 20% of Chaos damage dealt

PoE2DB currently lists these supports against Kelari or its Deception command.
Their actual propagation to the explosion remains a mandatory live panel and
combat test after the reset. Losing any one multiplicative support invalidates
the 1.5M proof.

Sources:

- https://poe2db.tw/us/Kelaris_Deception
- https://poe2db.tw/us/Bidding_II
- https://poe2db.tw/us/Commandment
- https://poe2db.tw/us/Deliberation
- https://poe2db.tw/us/Concentrated_Area
- https://poe2db.tw/us/Uul-Netols_Embrace

### Persistent/automatic groups

| Group | Supports | Correct current behavior |
|---|---|---|
| **Raging Spirits, q20** | Feeding Frenzy II; Fire Attunement; Elemental Armament III; Rapid Attacks II; Immolate | Ordinary quality gives +2 limit, for **12 total**. The 50% more-damage quality line is Advanced-only and is not available here. |
| **Ravenous Swarm, q20** | Feeding Frenzy II; Deadly Poison II; Chaos Mastery; Cooldown Recovery II; Prolonged Duration II | Hidden cap is **3**. Chaos Mastery makes it level 37, so `12.5 × 1.35 = 16.875s` duration exceeds three `6/1.30 = 4.615s` spawn intervals, sustaining cap after ramp. Ordinary quality adds attack radius; the faster-spawn line is Advanced-only. |
| **Blasphemy, q20** | Despair q20; Heightened Curse; Upwelling II; Clarity II | Blasphemy's ordinary quality is reservation efficiency. Its +20% curse-magnitude line is Advanced-only and is excluded. |
| **Azmerian Swarms** | Feeding Frenzy II; Elemental Armament III; Elemental Focus; Rapid Attacks II; Prolonged Duration II | Granted by disabled Periphery. Count the base limit of 8 unless the live item-granted panel proves otherwise. Supports are provisional until the exploit is tested. |

Living Lightning II has a cap of eight, a 0.2-second trigger cooldown with
eight cooldown uses, and each minion disperses after three attacks at a
0.14-second cadence. Eight is a cap, not a claimed sustained population; at
ordinary Ember cast rates only a few are likely to coexist.

Ordinary non-Spirit supports may be reused between skill groups; this changed
in the 0.3.0 era. Spirit-reserving supports still have a global one-copy
restriction. The duplicated Feeding Frenzy II, Elemental Armament III, Rapid
Attacks II, and Prolonged Duration II assignments above are non-Spirit and
remain legal. No generated Living Lightning attack is assumed to inherit
Ember's other supports, and Living Lightning copies the host skill's level
one-for-one. It does **not** double the host level or inherit Kelari's +16
level package.

Quality correction sources:

- https://poe2db.tw/us/Advanced_Thaumaturgy
- https://poe2db.tw/us/Raging_Spirits
- https://poe2db.tw/us/Ravenous_Swarm
- https://poe2db.tw/us/Blasphemy
- https://poe2db.tw/us/Despair

## Required disabled weapon-set exploit

### Enabled set I

Use an **Exceptional rare Omen Sceptre** with two augment sockets:

1. Idol of the Pharisee
2. Primate Idol

Idol of the Pharisee supplies 60% increased Command damage but imposes 30%
reduced Mana Cost Efficiency on Command skills. Primate supplies 40% increased
damage for allies in Presence.

### Deliberately disabled set II

Use **Periphery** as an Exceptional bow and socket a second Idol of the
Pharisee. Equip the active sceptre set first. The duplicate Limited-1 Idol must
make the Periphery set red/invalid while leaving its granted Azmerian Swarms
skill accessible under the reported exploit.

Count only the leaked skill. Every Periphery modifier, its augment effect,
weapon damage, attack speed, ailment contribution, attributes, and defenses is
zero. Supports attached to the disabled granted skill are also zero until live
testing proves they remain functional.

Disciple has no documented way to activate Pharisee's **Bonded** lines; those
normally require the relevant Maji ascendancy mechanic. A duplicate Limited-1
collision is not assumed to grant Bonded. If the undocumented exploit
unexpectedly makes the active Idol display Bonded anyway, its lines are 25%
reduced Reservation Efficiency for Minion skills and +2 Temporary-Minion
limit. The alternate reservation budget below survives that result, but
neither line is credited.

Periphery's raw static entry grants level-16 Azmerian Swarms, while examples on
the current page may show level 19. No Azmerian level or damage enters the DPS
proof.

This is undocumented behavior. It must be tested in a combat area after every
patch, zone transition, death, and relog. If 0.5.5 fixes it, the build can keep
its Kelari DPS and defenses but fails the user's mandatory exploit criterion.

Sources:

- https://poe2db.tw/us/Idol_of_the_Pharisee
- https://poe2db.tw/us/Periphery
- https://poe2db.tw/us/Azmerian_Swarms
- https://poe2db.tw/us/Exceptional_Item

## Legal level-36 Kelari package

| Source | Levels | Exact requirement |
|---|---:|---|
| Base skill | 20 | Current Kelari's Deception endpoint |
| Rare sceptre | +4 | Slavedriver suffix |
| Helmet | +4 | Natural +2 suffix plus upgraded +2 Kopec corruption enchant |
| Amulet | +4 | Natural +3 suffix plus `+1 to Level of all Skills` or `+1 to Level of all Minion Skills` corruption enchant |
| Amanamu Focus | +2 | Desecrated Amanamu suffix |
| Genesis belt | +2 | Genesis `of the Despot` suffix |
| **Total** | **36** | Kelari entity level 72 |

The helmet is a severe chase craft: first hit the +1 Minion corruption enchant,
then upgrade it with Kopec while retaining the natural +2 suffix through the
random explicit removal. Instil **Dominion** on the amulet before corrupting it.
Quality is not used to invent an additional amulet level. There are no “+2
Genesis minion-skill rings”; the genuine +2 outcome is on a Genesis belt.

Sources:

- https://poe2db.tw/us/Minions
- https://poe2db.tw/Kopecs_Orb_of_Sacrifice
- https://poe2db.tw/us/The_Genesis_Tree
- https://www.poe2wiki.net/wiki/List_of_item_corruption_enchantment_modifiers
- https://www.poe2wiki.net/wiki/Desecrated_modifier

## Exact gear endpoint

| Slot | Audited target |
|---|---|
| **Sceptre** | Exceptional Omen, prefixes: 119% Presence ally damage, 65% increased Spirit, 38% increased Spirit/+45 mana; suffixes: +4 Minion levels, 38% Presence ally crit chance, 39% Presence ally Critical Damage Bonus. Pharisee + Primate augments. This is a legal 3P/3S item and supplies `100 × 2.03 = 203 Spirit`. |
| **Focus** | Amanamu rare: flat ES, local %ES, +164 mana prefixes; +2 Minion levels, 69% mana regeneration, 20% Mana Cost Efficiency suffixes. No resistance suffix remains. |
| **Helmet** | High pure ES, natural +2 Minion suffix retained, upgraded +2 Kopec enchant. The final corruption normally leaves five explicits. Fill surviving suffix space with resistance. |
| **Body** | High pure ES with three local ES/hybrid-ES prefixes. Do **not** spend the third prefix on Spirit. Three resistance suffixes. |
| **Gloves** | High pure ES; resistance and attributes. |
| **Boots** | High pure ES; movement speed, resistance and attributes. Do not count an impossible three-ES-prefix package together with movement speed. |
| **Amulet** | Azure base; 45–50% increased maximum Energy Shield, +189 mana, 8% maximum mana prefixes; +3 Minion levels, 69% mana regeneration, one resistance suffix. Instil Dominion, then obtain `+1 to Level of all Skills` or `+1 to Level of all Minion Skills` corruption. |
| **Belt** | Genesis rare; 31% Minion damage, +124 mana and a defensive prefix; +2 Minion levels and two elemental-resistance suffixes. |
| **Rings** | Two perfect Evergrasping Rings: each grants allies in Presence 25% of damage as extra Chaos and up to 100 mana. Each also lets enemies in Presence gain up to 12% as extra Chaos; CI neutralizes that downside for the player. Kelari is invulnerable, but observe any other damageable cloud minions. |
| **Augments** | Three Perfect Inspiration armour augments for 63% increased mana regeneration; use remaining sockets for elemental resistance. Resistance caps take priority if the actual rares need them. |
| **Charms** | Heavy-stun and Freeze protection first; cover Ignite and Shock according to the finished gear. |

Every damage/crit/extra-Chaos modifier requiring Presence is lost if Kelari or
the boss leaves Presence. Stay close enough to maintain it.

Source for Evergrasping: https://poe2db.tw/us/Evergrasping_Ring

## Passive tree

The 120-point route was connected against Grinding Gear Games' current public
tree export. Important damage/minion nodes are:

- Growing Swarm
- Restless Dead
- Relentless Fallen
- Vile Mending
- Unstable Bond
- Entropic Incarnation
- Necrotic Touch
- Grip of Evil
- At Your Command
- both Puppet Master chance branches
- Archon of Undeath
- Mana Blessing

Defense and recharge:

- Pure Energy
- Enhanced Barrier
- Dampening Shield
- Illuminated Crown
- Rapid Recharge
- Soul Bloom
- Loose Flesh
- Taut Flesh
- Chaos Inoculation

Radius/pathing notables covered by the Time-Lost setup include Frozen Limit,
Infernal Limit, Shocking Limit, Ice Storm and Lightning Storm. Use socket
`61419` for a Rare Time-Lost jewel whose radius has been upgraded to **Very
Large** with Ancient Potent Liquid Melancholy, plus ordinary Sapphire sockets
`7960` and `21984`.

The exact 120 normal-tree hashes are:

```text
95, 517, 1433, 1447, 1823, 2254, 3251, 3443, 4345, 4748, 5314, 6006,
6077, 7104, 7424, 7960, 8357, 8616, 8737, 8983, 9642, 10131, 10320,
10742, 11376, 11672, 11679, 13537, 13542, 14258, 14548, 14598, 14666,
14686, 14945, 15358, 15408, 15809, 15885, 17044, 17501, 17973, 18485,
19318, 21540, 21560, 21984, 22393, 22691, 22783, 23907, 25893, 25927,
26926, 27658, 28458, 29148, 29391, 29800, 30346, 30720, 31175, 31238,
32847, 33612, 34006, 34199, 34367, 34840, 35560, 35645, 36507, 36746,
37594, 38105, 38972, 39037, 40200, 40691, 40783, 41511, 43557, 43979,
44299, 44405, 44733, 44871, 45885, 46554, 46819, 47168, 48552, 48774,
49455, 49512, 50104, 50715, 50720, 50837, 51169, 53795, 54036, 54521,
54632, 54733, 55180, 56349, 56956, 57710, 58090, 59362, 60878, 61027,
61354, 61403, 61419, 61842, 62210, 62677, 64804
```

`Archon of Undeath` is the connected notable at hash `26926`. `Dominion` is an
amulet-only instil, not another pathable node.

Tree export: https://github.com/grindinggear/poe2-skilltree-export/blob/main/data.json

### Jewels and audited offensive totals

At socket `61419`, use a Rare Time-Lost jewel with:

- a crafted **Very Large** radius prefix from Ancient Potent Liquid Melancholy;

- `10% increased Minion Critical Hit Chance per allocated Notable in radius`
- `12% increased Minion Critical Damage Bonus per allocated Notable in radius`

The crafted prefix adds 500 radius to Time-Lost's native 1,000; Baryanic
Leylines therefore produces `(1,000 + 500) × 1.40 = 2,100` in official-tree
coordinate units. That reaches all
15 allocated notables—the farthest, Grip of Evil, is approximately 2,091.65
units away—giving 150% crit chance and 180% Critical Damage Bonus. A normal
Grand/Large prefix reaches only eight of them and would reduce the headline
off-Archon DPS to roughly **1.318M**.

Use two Basic Sapphire Jewels, each with:

- 15% Minion damage
- one perfect Potent Ferocity modifier granting 60% increased effect of
  suffixes, making a 20% Minion-crit suffix worth 32% and a 25% Minion-CDB
  suffix worth 40%

The audited tree/jewel totals used below are:

- 184% generic Minion damage from the tree
- 55% Command damage from the tree
- 10% safe At Your Command contribution, counting only Kelari as the
  guaranteed persistent type in Presence
- 92% Command cooldown recovery
- 60% Minion crit chance and 70% Minion Critical Damage Bonus from the tree
- 30% Minion damage, 64% crit and 80% CDB from the two Sapphire Jewels
- 150% crit and 180% CDB from Time-Lost
- 228% maximum Energy Shield and +20 flat Energy Shield
- 20% mana regeneration

Sources:

- https://poe2db.tw/us/Time-Lost_Jewels
- https://poe2db.tw/us/Ancient_Potent_Liquid_Melancholy
- https://poe2db.tw/us/Basic_Jewel

## Manual DPS calculation

No Path of Building, planner, or other build-calculation utility was used. The
calculation starts from PoE2DB's raw entity-level endpoint for Kelari.

### 1. Base hit

The **16–23** shown at the top of PoE2DB is the rounded
`15.57–23.36` value of Kelari's `ExplosiveTeleportSandDjinn` at **entity
level 1**. It is not the level-20 or level-36 value. The player-facing Command
wrapper itself is tagged `base deal no damage [1]`; it tells Kelari to execute
that entity skill.

The current level mapping and raw entity endpoint are:

| Player skill state | Kelari entity level | Raw Deception hit |
|---|---:|---:|
| PoE2DB's initial page preview | 1 | 15.57–23.36, displayed as 16–23 |
| Natural skill level 20 | 40 | 817.5–1,226 |
| This chase package, skill level 36 | 72 | 5,238–7,857 |

Kelari's own skill page maps player skill level `L` to Minion/entity level
`2L`, including skill level 36 to entity level 72. The calculation uses the
entity-level-72 skill base directly. It does **not** multiply it by PoE2DB's separate monster
header values for Spell Damage, Minion Damage, rarity, or map scaling.

Thus the base used here is:

```text
B = (5,238 + 7,857) / 2 = 6,547.5 average base Physical damage
```

Sources:

- Command wrapper and its `base deal no damage` flag:
  https://poe2db.tw/us/Kelaris_Deception
- Kelari player-skill to Minion-level table:
  https://poe2db.tw/us/Kelari%2C_the_Tainted_Sands
- Raw entity endpoints:
  [level 1](https://poe2db.tw/us/api/monsterLevelSkill?monsterHash=31136&arealv=1),
  [level 40](https://poe2db.tw/us/api/monsterLevelSkill?monsterHash=31136&arealv=40),
  and [level 72](https://poe2db.tw/us/api/monsterLevelSkill?monsterHash=31136&arealv=72)

### 2. Increased-damage ledger

```text
184  tree generic Minion damage
 30  two Basic Sapphire Jewels
 55  tree Command damage
 10  At Your Command, one guaranteed persistent type
119  sceptre Presence ally damage
 60  Idol of the Pharisee Command damage
 40  Primate Idol Presence ally damage
 31  Genesis-belt Minion damage
---
529% increased damage = 6.29 multiplier
```

Upwelling II's optional 50% increased Minion damage is deliberately omitted
from the conservative line.

### 3. More multipliers and full consecutive-use ramp

Kelari's Deception has 50% more damage for each previous consecutive use, up
to 300% more. At full ramp, that is a 4.00 multiplier.

```text
H = 6,547.5 × 6.29 × 1.30 Bidding × 1.20 Deliberation
    × 1.30 Concentrated Area × 4.00 ramp
  = 334,082.7828 average pre-crit Physical component
```

The seventh consecutive Deception use reaches full ramp. At the audited
1.553-second off-Archon interval, the next use occurs within the clone's
four-second duration, but boss phases reset the ramp. It must also be tested
whether interleaving Ember Fusillade affects what the game considers
“consecutive”; if it does, this calculation is invalid.

### 4. Critical strikes

```text
Increased crit chance = 60 tree + 64 Sapphire + 150 Time-Lost + 38 sceptre
                      = 312%
Crit probability      = 15% × (1 + 3.12) = 61.8%

Increased CDB = 70 tree + 80 Sapphire + 180 Time-Lost
                + 50 Kelari + 39 sceptre = 419%
Final crit hit multiplier = 1 + [100% base bonus × (1 + 4.19)] = 6.19

Expected crit multiplier = 1 + 0.618 × (6.19 - 1) = 4.20742
K = 334,082.7828 × 4.20742 = 1,405,626.5820
```

`K` is the expected pre-mitigation Physical component per use.

The 100% base Minion Critical Damage Bonus is not inferred from PoE2DB's
generic `+30%` raw-monster header, which does not encode the player-minion
override. GGG explicitly changed player-minion base CDB to +100% to match
players and warned that the character panel would display it incorrectly. If
the generic 30% monster-header value were used instead, the off-Archon result
would fall to about 800k DPS and the Archon-weighted result to about 1.291M.

Official source:
https://www.pathofexile.com/forum/view-thread/3646886

### 5. Extra Chaos, Despair, and full Armour Break

Chaos gained from the Physical source is:

```text
40% Uul-Netol's Embrace
25% first Evergrasping Ring
25% second Evergrasping Ring
10% Entropic Incarnation
---
100% Physical gained as Chaos
```

The level-20 q20 Despair calculation against a Unique is:

```text
49% × (1 + 0.10 Despair quality + 0.25 Heightened Curse)
    × 0.59 level-20 Blasphemy
    × 0.50 Unique-enemy curse penalty
= 19.51425% Chaos resistance reduction
```

Against the stated reference target's assumed 50% Chaos resistance, Chaos
therefore deals `1 - (0.50 - 0.1951425) = 0.6951425` of pre-resistance damage.
Full Armour Break makes a non-player take 20% increased Physical Hit damage.

```text
Damage/use = K × (1.20 Physical + 0.6951425 Chaos)
           = 2,663,862.6747
```

Sources:

- https://poe2db.tw/us/Despair
- https://poe2db.tw/us/Heightened_Curse
- https://poe2db.tw/us/Curses
- https://poe2db.tw/us/Armour_Break

### 6. Sustained command rate

```text
Command cooldown recovery = 92 tree + 30 Bidding + 100 Commandment = 222%
Rate = (1 + 2.22) / 5 seconds = 0.644 uses/second

Off-Archon DPS = 2,663,862.6747 × 0.644
               = 1,715,527.5625 DPS
```

With Upwelling II active, replace the additive `6.29` with `6.79`:

```text
1,715,527.5625 × 6.79 / 6.29 = 1,851,897 DPS
```

### Audited condition table

The no-break rows use 8,593 Armour as a representative level-84 value and
apply Armour separately to non-critical and critical hits. That value is not a
universal boss stat.

| Reference boss state | Off-Archon DPS | Archon-on DPS | Archon-weighted DPS |
|---|---:|---:|---:|
| No Despair, fully broken | **1,538,880** | 3,462,480 | 2,484,047 |
| No Despair, no break | **1,357,283** | 3,053,886 | 2,190,914 |
| Corrected Despair, fully broken | **1,715,528** | 3,859,937 | 2,769,190 |
| Corrected Despair, no break | **1,533,930** | 3,451,343 | 2,476,057 |

The target is therefore not unconditional. Losing both Despair and full Break
drops below 1.5M. The one-condition cases clear by only about 2–3%.

### Archon of Undeath weighting

Archon has a 15% chance per Command use, lasts ten seconds, grants 25% more
Minion damage and 200% more Command cooldown recovery. Dominion halves the
buff magnitudes and removes the recovery period, leaving 12.5% more damage and
100% more cooldown recovery.

The two perfect Evergrasping implicits supply 20% increased Cast Speed. The
on-state rate is therefore capped by cooldown recovery, not animation time:

```text
Adjusted cast time = 0.8 / 1.20 = 0.6667 seconds
Animation cap      = 1 / 0.6667 = 1.50 uses/second
Recovery cap       = (3.22 × 2) / 5 = 1.288 uses/second
r1                 = 1.288 uses/second
```

That rate consumes `1.288 × 0.6667 = 85.9%` of player action time. Only 14.1%
remains for Ember during a full-rate Archon window, so the weighted model still
treats that window as **Deception-only**. A small Ember burst may fit in live
play, but full cloud upkeep does not. Raging Spirits and Living Lightning can
decay without altering the headline because their damage is assigned zero in
that Deception-priority headline.

Using no stored-charge burst and no refresh while active:

```text
Expected off time = 1 / (0.644 × 0.15) = 10.35197 seconds
Uptime            = 10 / (10 + 10.35197) = 49.1353%
Weighted DPS      = (1-u) × 1,715,528 + u × 3,859,937
                  = 2,769,190
```

This is an expected-state model, not a guaranteed floor.

Sources:

- https://poe2db.tw/us/Archon_of_Undeath
- https://poe2db.tw/us/Dominion

### Small-minion cloud estimate

This section is a separate estimate, not padding in the 1.716M
Deception-only proof. It uses raw entity hits and manual arithmetic. Reference
conditions are full Armour Break, the same 50% base Chaos resistance reduced
by Despair, 40% elemental resistance, every modeled attack landing, zero
Strength/Dexterity scaling, and no Shock or Upwelling unless stated.

Common cloud inputs are:

```text
Generic increased Minion damage = 184 tree + 30 jewels + 119 sceptre
                                  + 40 Primate + 31 belt
                                = 404%, or 5.04×

Ordinary 5%-base-crit minion expectation:
chance = 5% × (1 + 3.12) = 20.6%
CDB hit = 1 + [100% × (1 + 3.69)] = 5.69×
expectation = 1 + 0.206 × (5.69 - 1) = 1.96614×

Living Lightning 14%-base-crit expectation:
chance = 14% × 4.12 = 57.68%
expectation = 1 + 0.5768 × (5.69 - 1) = 3.705192×
```

#### Modeled Ember population

Ember has a 0.2857-second cast, 0.845-second hover, ten-projectile release,
and Wildshards' non-deterministic 25% eight-projectile proc. Solving that
cycle gives 4.0247 expected casts to fill the release and 4.9497 expected
Raging Spirits per cycle. Scheduling one 0.6667-second Deception action every
1.553 seconds stretches the modeled release cycle to 4.0373 seconds:

```text
Average Raging-Spirit population = 4.9497 × 5 / 4.0373 = 6.128
Ember hits per second            = 10 / 4.0373 = 2.4769
Living-Lightning attacks/second  = 2.4769 × 3 = 7.4306
Azmerian mean population         = 2.4769 × 10% × 13.5 = 3.3438
```

That is a scheduling model, not a promise that every projectile hits or every
minion immediately acquires the boss. The Azmerian line applies only while a
Rare/Unique is already affected by an Elemental Ailment and only if both the
disabled granted skill and Prolonged Duration support function. Otherwise its
population is zero. A literal guaranteed floor is zero.

#### Damage by cloud engine

| Engine and state | Main damage types | At 40% elemental resistance | At 0% elemental resistance |
|---|---|---:|---:|
| 6.128 average Raging Spirits; no Ignite/no ailment bonus | 30% Physical, 70% converted Fire + 25% gained Fire, plus 53% gained Chaos | 36.70k | 47.44k |
| Same Spirits; target Ignited and one elemental ailment | Above, plus 30% gained Fire from Immolate | 46.81k | 62.64k |
| Same Spirits; three elemental ailments **including Ignite** | Above, plus 30% gained Fire from Immolate | 56.84k | 76.06k |
| Living Lightning II from level-20 Ember; 7.4306 attacks/s | 100% Lightning conversion plus 50% gained Chaos | 4.14k | 5.89k |
| Three level-37 Ravenous Swarms, including the strongest supported Poison | Physical, 60% gained Chaos, Poison | 12.17k–14.14k | 12.17k–14.14k |
| Provisionally supported level-19 Azmerian, one ailment; 3.3438 mean population | Random Element plus 50% gained Chaos | 5.02k | 7.31k |
| Same provisional level-19 Azmerian, three ailments | Random Element plus 50% gained Chaos | 8.92k | 12.98k |
| **Unproved** fully level-leaked level-35 Azmerian, three ailments | Random Element plus 50% gained Chaos | 33.26k | 48.42k |

Raging Spirits start from a level-72 raw average hit of 184; Living Lightning
starts from a level-40 raw average hit of 31.5; Ravenous with Chaos Mastery is
level 37/entity 74 with a 234.5 raw average hit. Azmerian's exact level is
unresolved because Periphery's static record says level 16, a current item
example shows level 19, and disabled-set propagation of the active set's +16
Minion levels and socketed supports is undocumented.

`RavenousSwarmAttack` carries a Chaos skill tag but exposes no explicit
conversion stat. The table uses the normal default reading that its raw attack
base is Physical. If live testing shows that hit is actually all Chaos, the
direct Ravenous contribution falls from about 11.70k to about 7.54k.

The resulting whole-cloud scenarios are:

| Scenario | 40% elemental resistance | 0% elemental resistance |
|---|---:|---:|
| No credited ailment bonus; Azmerian zero; Raven average-poison model | **53.02k** | **65.51k** |
| One ailment + Ignite; provisional supported level-19 Azmerian; strongest-poison model | **70.11k** | **89.97k** |
| Three ailments including Ignite; fully leaked level-35 supported Azmerian; strongest-poison model | **108.38k** | **144.50k** |

Upwelling changes the generic additive bucket from 5.04 to 5.54, multiplying
these cloud estimates by `5.54 / 5.04 = 1.0992`. An aggressive overlap model
with 12 Spirits, 15 Living-Lightning attacks/s, 6.75 Azmerians, three ailments,
and the unproved level-35 leak reaches about 201k at 40% resistance or 273k at
0%; it is an explicit ceiling hypothesis, not a build claim.

In cloud-preserving play, adding the modeled scenario band to weighted
Deception gives approximately **1.874M–1.929M** without Upwelling or
**2.024M–2.085M** with it. Do not add this cloud band to the 2.769M/2.989M
Deception-priority mode, because that mode largely starves Ember during
Archon windows.

Raw entity sources:

- https://poe2db.tw/us/Raging_Spirits
- https://poe2db.tw/us/Ravenous_Swarm
- https://poe2db.tw/us/Living_Lightning_II
- https://poe2db.tw/us/Azmerian_Swarms
- https://poe2db.tw/us/Periphery

### What is excluded, and what breaks the proof

Excluded from the **1.716M proof line**: every cloud minion, Kelari's ordinary
attacks, the clone's ordinary attacks, Critical Weakness, Wither, Shock,
Frozen, Upwelling II in the base line, on-kill effects, Culling Strike, and the
disabled bow's stats. The cloud is estimated separately above.

The following invalidate or materially lower the 1.716M result:

- anything short of the Very Large crafted radius and all 15 notables inside a
  perfect 10/12 Time-Lost jewel;
- losing any part of the legal +16 level package;
- failure of Bidding, Deliberation, Concentrated Area, Commandment, or Uul to
  propagate to the Deception explosion;
- failure to maintain the 300% consecutive-use ramp;
- missing the boss because Concentrated Area halves explosion radius;
- Kelari or the boss leaving Presence;
- insufficient Spirit for Despair, or a Hexproof/curse-reduced target;
- a boss with different Chaos resistance, Armour, Physical Damage Reduction,
  or map modifiers;
- a phase reset before Armour Break and ramp are re-established.

A 9/11 rather than 10/12 Time-Lost jewel lowers the corrected-Despair/no-break
case to roughly 1.459M. Fourteen covered notables lower it to about 1.479M.
Losing one skill level lowers the same no-break case to about 1.376M; even the
curse-plus-break case only barely remains around 1.539M.

## Spirit budget

Recommended pre-test supply:

```text
100 campaign base
203 Exceptional Omen Sceptre
---
303 Spirit
```

Known reservation, before the undocumented Bonded contingency:

```text
36  Raging Spirits: 30 × 1.20 Elemental Armament III
47  Ravenous: ceil(30 × 1.30 CDR II × 1.20 Prolonged II)
122 Blasphemy group: ceil[(60 × 1.40 Heightened + 30 Upwelling
    + 20 Clarity) / 1.10 q20 efficiency]
---
205 Spirit known; 98 remains
```

If the undocumented collision unexpectedly grants Bonded and its 25% reduced
Minion Reservation Efficiency applies, SRS becomes `ceil(36/0.75) = 48` and
Ravenous becomes `ceil(46.8/0.75) = 63`. The known total becomes **233**,
leaving **70**.

Current sources disagree on Azmerian Swarms: its data has a reservation flag
but no exposed numeric value, while the current minion table reports zero.
Treat it as “apparently zero, live-confirm.” Kelari's Deception and Living
Lightning II reserve no Spirit. Do not invent an Azmerian cost; record it from
the live panel. The build fails the 303-Spirit configuration if the live total
exceeds 303.

The prior 414-Spirit configuration was a maximum possible supply, not a useful
target. It spent a 61-Spirit body prefix and 50-Spirit amulet prefix to leave
209 unused in the documented state. Those prefixes are now a third local
ES/hybrid-ES body roll and 45–50% increased maximum ES on the amulet.

After live testing proves both that Azmerian reserves zero and that Bonded is
inactive, an optional mana-oriented endpoint can remove the sceptre's 38%
Spirit/+45 mana hybrid prefix as well:

```text
100 campaign + 165 from the remaining 65% sceptre prefix = 265 Spirit
```

That leaves 60 above the documented 205. Replacing the hybrid with up to +149
mana gains as much as 104 net mana, but 265 is too tight to recommend before
the Azmerian/Bonded tests.

## Mana sustain

Level-36 Deception costs 250 mana. Uul-Netol raises that to 300. Focus, Puppet
Master, and Pharisee make the effective cost:

```text
300 / (1 + 0.20 focus + 0.15 Puppet Master - 0.30 Pharisee)
= 285.714 mana/use
```

That is:

- 184 mana/second off Archon;
- 368 mana/second during the full Archon command window;
- 274.4 mana/second under the weighted Archon rate, before Ember.

Legal regeneration sources are 20% tree, 69% focus, 69% amulet, 30% Azure
implicit, 63% from three Perfect Inspiration augments, and 50% from Clarity II:
**301% increased mana regeneration**.

After the focus's 20% Cost Efficiency, Ember costs approximately
`37 / 1.20 = 30.833` mana per cast. A true three-casts/second average would add
92.5 mana/second, putting the weighted command-plus-Ember load near 366.9/s.

At 2,400 mana, regeneration is `2,400 × 0.04 × 4.01 = 384.96/s`, enough for
that weighted average. During Archon, the 1.288/s Deception cadence costs
368/s and consumes 85.9% of action time, leaving too little time for full Ember
upkeep. A purely hypothetical simultaneous three-casts/second Ember load would
raise the total to 460.5/s; require at least **461 mana/second**, approximately
2,871 mana at this exact 301% regeneration ledger. Use **2,400 mana as the
minimum** and **2,900 as the continuous-overlap target**. If resistance
augments displace Inspiration, the live regeneration gate still applies.
Avoid no-regeneration/recovery maps.

## Defense and resistance gates

### Energy Shield

Chaos Inoculation makes ES the entire counted defensive resource and grants
Chaos immunity. With +228% maximum ES and +20 flat ES from the exact tree plus
the amulet's 50% increased maximum ES:

```text
(1,500 displayed ES across helmet/body/focus/gloves/boots + 20 flat)
× 3.78 = 5,745.6 ES
```

Therefore the same 1,500 displayed-item-ES purchase floor now clears the gate
more comfortably, even before counting the stronger third body prefix. A
finished target of **1,820+ displayed item ES** yields approximately 6,955.2
ES before any additional source. Target **6,000–8,000 ES** and never accept a
finished sheet at or below 4,500 ES.

Sacred Rituals adds 60% of current ES to Armour for PDR calculations: about
3,000 at 5,000 current ES, 3,600 at 6,000, and 4,200 at 7,000. Fourth Teaching
then improves recharge delay and low-ES recharge. Neither temporary Armour nor
conditional Barrier is counted in the 4,500-resource gate.

### Resistances

Fire, Cold and Lightning must each display **75% in the actual endgame area**.
CI supplies Chaos immunity; do not report a fictional need for displayed 75%
Chaos resistance.

The resistance burden is feasible but tight because the sceptre, focus and
both Evergrasping Rings provide none. Use body, gloves, boots, belt, the
surviving helmet suffix, the amulet's one free suffix, and remaining armour
augment sockets. Do not declare the build complete from a gear template: add
the actual equipped totals after campaign penalties and reject any set that
does not show 75/75/75. Aim for 20% overcap where the ES and mana gates permit.

Heavy-stun and Freeze immunity/protection are first-priority charms. Cover
Ignite and Shock with the remaining charm/gear capacity.

## Gameplay sequence

1. Enter with all persistent skills enabled and the sceptre/focus set active.
2. Confirm Periphery is red/invalid but Azmerian Swarms remains in the skill
   panel; never weapon-swap to obtain its stats.
3. Keep the boss and Kelari inside Presence and use Ember in deliberate
   cast-then-release bursts; continuous recasting prevents release.
4. Use Kelari's Deception every time its cooldown is available. It takes six
   previous consecutive uses before the seventh reaches full ramp.
5. Despair is automatic through Blasphemy. The first Deception hits establish
   Armour Break through Uul; the opening hit is not credited as fully broken.
6. Rebuild ramp and Break after every boss phase.

## Removed candidates

- **Doedre's Undoing:** Witchtoads are not Minions and add no dependable
  addless-boss engine here.
- **Bursting Fen Toad:** not a Minion and requires kills.
- **Spiraling Conspiracy:** visually appropriate, but The Raven's Flock is a
  two-handed staff and conflicts with both the rare sceptre/focus damage package
  and the required disabled Periphery set.
- **Skyfall:** genuine Runic minions, but its Glory cast is a third combat
  button and violates the cap.
- **Verisium Manifestations:** require player Attack Hits; Ember Fusillade is a
  Spell. Adding a reliable Attack trigger would disrupt the two-button design.
- **Kelari's Malediction:** corpse/Command architecture conflicts with the
  banned roster and two-button limit.
- **Wardbound Minions:** explicitly banned.
- **All Skeleton, Companion, Wolf and Spectre sources:** explicitly banned.
- **Doedre/Fen/Grim Pillars/Bitter Dead and other creature-shaped effects:**
  not treated as Minions merely because their visuals fit.

## Mandatory 0.5.5 acceptance tests

Do not buy the Kopec/amulet chase package until these inexpensive tests pass:

1. Read the 0.5.5 notes for every named skill, support, ascendancy node, unique,
   modifier and augment.
2. Reproduce the duplicate-Pharisee Periphery invalidation in a combat area.
3. Confirm Azmerian Swarms remains available, its supports remain active, its
   reservation is affordable, and no Periphery/disabled-augment stat applies.
4. Confirm whether the active Pharisee becomes Bonded and record the resulting
   temporary-minion limits and reservations.
5. Confirm all five Deception supports affect the explosion.
6. Confirm interleaving Ember does not reset Deception's consecutive-use ramp,
   and measure the real Ember release/SRS population cycle.
7. Confirm the level-36 setup produces entity-level-72 Deception damage.
8. Confirm the Time-Lost jewel has the Ancient Potent Liquid Melancholy Very
   Large prefix and its 10/12 suffixes cover all 15 intended notables.
9. Confirm 303 Spirit is sufficient for the exact live reservation total;
   record Azmerian's real value rather than assuming zero.
10. Confirm at least 4,501 ES, 75/75/75 elemental resistance, and enough mana
    regeneration to maintain the intended command rate.
11. Run an add-free boss test from zero ramp, then separately record the fully
    ramped sustained window. Do not present the latter as opening DPS.

If a test fails, remove the failed contribution from the equations before
claiming 1.5M. Do not replace it with estimated cloud damage.

## Current-version sources

- 0.5.4f patch: https://www.pathofexile.com/forum/view-thread/3996513
- 0.5.5 reset announcement: https://www.pathofexile.com/forum/view-thread/3999858
- Current official passive export: https://github.com/grindinggear/poe2-skilltree-export/blob/main/data.json
- Disciple: https://poe2db.tw/us/Disciple_of_Varashta
- Kelari's Deception: https://poe2db.tw/us/Kelaris_Deception
- Archon: https://poe2db.tw/us/Archon_of_Undeath
- Ember: https://poe2db.tw/us/Ember_Fusillade
- Raging Spirits: https://poe2db.tw/us/Raging_Spirits
- Ravenous Swarm: https://poe2db.tw/us/Ravenous_Swarm
- Living Lightning II: https://poe2db.tw/us/Living_Lightning_II
- Periphery/Azmerian: https://poe2db.tw/us/Periphery and https://poe2db.tw/us/Azmerian_Swarms
- Minion modifiers: https://poe2db.tw/us/Minions
- Genesis outcomes: https://poe2db.tw/us/The_Genesis_Tree
- Very Large Time-Lost craft: https://poe2db.tw/us/Ancient_Potent_Liquid_Melancholy
- Advanced-quality distinction: https://poe2db.tw/us/Advanced_Thaumaturgy

# Explosive Transmutation: Shaman versus Acolyte of Chayula

Current-data audit: Path of Exile 2 0.5.4/0.5.4f, immediately before 0.5.5. Recheck the short launch-test list at the end before buying rare gear because the disabled weapon-set interaction and several remnant interactions are runtime-dependent and could change in 0.5.5.

## Verdict

**Build the defensive Acolyte of Chayula.** It has the best combination of remnant rate, short-fight damage, and survivability. The Volatility Acolyte has the highest long-ramp paper ceiling. Stormweaver is the audited wildcard and produces the densest literal remnant field, but loses the Purple-Flame damage engine. Shaman is thematically excellent and can be made functional, but its Rage and Life engines are considerably more fragile and its ordinary damage is lower.

| Route | Modelled ET DPS assuming one Cascade contact per trigger | Rough defence | Verdict |
|---|---:|---|---|
| Defensive Acolyte, 9-10 Purple stacks | 0.409-0.621M, from moderate to optimistic critical investment | 8.20-9.52k provisional fresh non-Chaos hit capacity; 6.20-7.02k effective Chaos hit capacity; Evasion, Ghost Dance, 20% Block, 15% additional PDR | Best overall |
| Volatility Acolyte | Optimistic: 0.645M at 0 Volatility; 1.03M at 100; 1.46M at 200 | 6.8-8.2k provisional Life + ES against non-Chaos; 4.8-5.7k effective against Chaos; no Darkness | Highest staged ceiling, weakest short-fight choice |
| Stormweaver wildcard | Roughly 0.13-0.27M realistic; up to about 0.40M with an extreme triple-gain wand | 7.5-9.0k fresh non-Chaos and 5.5-6.5k Chaos capacity with Force of Will | Most literal remnant density, not best ET damage |
| Rage Shaman, normal wand | Optimistic: 0.300M at 75 Rage | 3.8-4.3k Life; provisional 8-15k Armour and 8-15k Evasion; 65-75% of Armour applies to elemental; 20% Block; heavy self-drain | Inferior baseline |
| Rage Shaman, extreme gain-as wand | Optimistic: 0.500-0.590M at 75 Rage | Same as above, but a near-perfect six-affix wand and harder Rage sustain | Can reach the damage neighbourhood only at extreme cost |

Multiply the one-contact-per-trigger DPS by measured average contacts, not automatically by three. At the optimistic critical target, two contacts make the defensive Acolyte approximately 1.15-1.24M and three make it 1.73-1.86M. At the moderate critical target, nine to ten Purple stacks instead produce about 0.817-0.882M at two contacts or 1.226-1.324M at three. Random remnant placement means three contacts are a favourable case, not a guaranteed boss result.

These figures are manual scenarios, not PoB output and not claims about an already itemised passive tree. Driver damage is excluded.

## Confidence standard

- **Verified:** present in the current skill, support, item, passive, or engine data.
- **Modelled:** arithmetic from verified base values and stated target modifiers.
- **Test-gated:** the data does not expose the complete runtime interaction; test it in game after 0.5.5.

The important current references are [Explosive Transmutation](https://poe2db.tw/us/Explosive_Transmutation), [Runic Ward](https://poe2db.tw/us/Runic_Ward), [Into the Breach](https://poe2db.tw/us/Into_the_Breach), [Wildwood's Gifts](https://poe2db.tw/us/Wildwoods_Gifts), [Sanguine Revelry](https://poe2db.tw/us/Sanguine_Revelry), [Mana Remnants](https://poe2db.tw/us/Mana_Remnants), [Animus Exchange](https://poe2db.tw/us/Animus_Exchange), [Berserk](https://poe2db.tw/us/Berserk), [Acolyte of Chayula](https://poe2db.tw/us/Acolyte_of_Chayula), [Shaman](https://poe2db.tw/us/Shaman), [The Hollow Mask](https://poe2db.tw/us/The_Hollow_Mask), and [Sunsplinter](https://poe2db.tw/us/Sunsplinter).

## Mechanics that determine the build

### Explosive Transmutation selects one damage mode

A pickup does **not** deal all five damage packages. The remnant type selects one explosion mode. At skill level 40:

| Mode | Base damage | Average | Base critical chance |
|---|---:|---:|---:|
| Physical | 2,597-3,896 | 3,246.5 | 14% |
| Cold | 1,189-1,784 | 1,486.5 | 12% |
| Fire | 1,338-2,007 | 1,672.5 | 8% |
| Lightning | 237-4,512 | 2,374.5 | 9% |
| Chaos | 2,435-4,522 | 3,478.5 | 7% |

It reserves 30 Spirit, costs 74 Runic Ward at level 40 before support multipliers, has a hidden 0.50-second explosion cooldown, stores five cooldown uses, and has a 1.8-metre base radius. Normal 20% quality adds 0.2 metres of radius. There is no 30% Ward-efficiency quality option for these ascendancies; that additional quality effect belongs to Gemling's Advanced Thaumaturgy.

[Khatal's Rejuvenation](https://poe2wiki.net/wiki/Khatal%27s_Rejuvenation) gives 5% cooldown recovery per recently collected remnant, up to eight stacks for ten seconds. At eight stacks:

`1 / (0.50 / 1.40) = 2.80 explosions per second`

The five stored uses absorb pickup bursts; they do not raise sustained output above the cooldown ceiling.

Ordinary global or Spell cooldown recovery can also affect the hidden explosion. The clean breakpoints are:

| ET cooldown recovery | Sustainable cooldown ceiling |
|---:|---:|
| Khatal, 40% | 2.80/s |
| Khatal + one perfect 25% Genesis caster-ring prefix | 3.30/s |
| Khatal + two perfect ring prefixes | 3.80/s |
| Khatal + Cooldown Recovery II | 3.40/s, but the support also raises Ward cost by 30% |

Second Wind and Atziri's Impatience are not legal shortcuts: both exclude Triggered skills. More cooldown recovery is useful only when pickups can feed it. Defensive Acolyte and Shaman are already remnant-supply-limited at their proposed rates; the only credible sustained CDR upgrade in this report is the test-gated 3.30/s Acolyte variant described below.

### Cascade is coverage, not an automatic triple

[Spell Cascade](https://poe2db.tw/us/Spell_Cascade) produces the original area and two additional areas on a random axis. Each deals 30% less damage. With [Vorana's Siege](https://poe2db.tw/Voranas_Siege) and [Zenith II](https://poe2db.tw/us/Zenith_II), each contact has this non-critical multiplier:

`0.70 x 1.20 x 1.30 = 1.092`

Cascade beats one non-Cascade hit only when it averages more than `1 / 0.70 = 1.428` contacts. Use it for mapping and scattered remnants, but directly measure one-, two-, and three-contact boss behaviour. Zenith II also requires more than 90% Mana, so a low Mana snapshot loses another factor of 1.30.

### Runic Ward is a skill fuel, not ordinary Energy Shield

Ordinary non-fatal hits do not consume Runic Ward. On a fatal hit, Life is left at one and Ward pays the excess; the character dies if Ward is insufficient. Non-damage Life loss is not prevented. Ward normally regenerates at 5% of maximum per second. Its overflow cap is 150%, but only recovery explicitly allowed to overflow, such as Animus Exchange, can fill that overflow.

For these builds its primary job is paying frequent ET triggers. Do not add Ward to ordinary hit points in the defence totals.

### The remnant-to-damage mapping is not fully published

Purple Flames of Chayula unambiguously select the Chaos package. Current public data does not explicitly identify which ET package Hateful, Ancient, Life/Rage, and Mana remnants select. Community observation points toward Physical for the non-Purple remnants, but that remains a launch test, not a verified premise.

That uncertainty matters more than small passive optimisations. The damage tables therefore show both the favourable Physical working model and sensitivity to the other modes.

### Complete current remnant-source audit

The current `GeneratesRemnants` skill list contains fourteen player skills. For an isolated living boss, their useful rates and restrictions are:

| Source | Isolated-boss production | Decision for this build |
|---|---:|---|
| Into the Breach | 0.82645/s at level 20 | Core Acolyte source |
| Wildwood's Gifts | 0.82645/s at level 20 | Core source from a level-20/ilvl-90 Hollow Mask |
| Sanguine Revelry | 0.50/s while repeatedly hitting a Bleeding target | Core disabled-set source if the exploit survives |
| Mana Remnants | 0.50/s while repeatedly critting an elementally Ailmented target | Core Volatility source; optional Lament roll for defensive Acolyte |
| Life Remnants | 0.3333/s on hit | Requires Blood Mage or The Covenant body; not worth losing the ascendancy/body |
| Remnants of Kalguur | At most one per 3 seconds; also requires repeated Stuns and a chance roll | Too conditional for a boss-rate headline |
| Siphon Elements | One Cold remnant per boss Freeze is effectively guaranteed at quality 20; fresh Ignite/Shock attempts are only 7% each | Useful only with a measured Freeze/consume loop |
| Lingering Illusion | Depends on dodge rolls and the boss destroying the illusion | Unreliable, and a Lament roll loses Ghost Dance/Mana Remnants |
| Grim Feast | Depends on deaths of reviving minions | Reject for this non-minion build |
| Frost Bomb | 0.1667/s before CDR | Extra active button for a weak rate |
| Snap | 0.25/s before CDR and consumes an ailment | Can fill a small CDR gap, but costs a button and ailment uptime |
| Orb of Storms | Roughly 0.083-0.125/s in this driver cadence | Too weak |
| Living Bomb | One after its seed's threshold is dealt | Potentially useful only after timing the threshold; no honest fixed rate yet |
| Lightning Warp | Zero against a living boss above Cull without another setup | Reject |

Runic Extraction and Rip create remnants only on killing blows, so they add zero to the isolated-boss model. Two separate 5% additional-remnant tree smalls also exist. The displayed baseline uses Harmonic II's 20% plus Remnant Attraction's 10%; taking one accessible 5% small raises defensive Acolyte's expected object rate from 2.79876 to 2.90640/s and gives enough oversupply for the 2.8/s cooldown cap. Ancient Rune of Discovery adds 30% on a staff, but losing the wand plus Sunsplinter costs too many levels, Block, and maximum resistances.

One correction to a tempting interaction: the 17-23% glove modifier that makes a pickup count again is the **Way of the Stonefist transformed version** of Expansive Alloy. Shaman's Wisdom of the Maji does not transform glove explicits, so Shaman cannot use that value. The ordinary two Genesis-ring suffixes remain available, but whether their additional counted pickups retrigger ET is still a launch test.

## Recommended build: defensive Acolyte

### Ascendancy

Take both four-point branches:

- Waking Dream into Choice of Power.
- Embrace the Darkness into Deepening Shadows.

At level 90, base Darkness is `100 + 10 x 90 = 1,000`. Two 10% path nodes plus 75% from capped Chaos Resistance make approximately `1,000 x 1.95 = 1,950 Darkness`.

Darkness is a rolling five-second damage reservation buffer, not a permanently recoverable 1,950-point resource. The defence section treats it as fresh-hit capacity and labels it accordingly.

### Core equipment

- **Weapon:** Runemastered Runic Fork, choosing the +300 maximum Ward implicit. Target `+5 to level of all Chaos Spell Skills`, `+4 to level of all Spell Skills`, and spell critical chance or Mana regeneration. Its three sockets are Equinox, Obsession, and Perfect Inspiration for the Ward engine. If sustain proves excessive, Ancient Rune of Decay is the damage swap.
- **Offhand:** a uniquely runemastered Sunsplinter. Its +1 Fire, +2 Cold, and +3 Lightning all apply to ET, for +6 levels, plus 20% Block and one 76/77/78 maximum-resistance permutation. Unique runeforging upgrades it to a Runemastered Array Buckler with 33 Runic Ward.
- **Helmet:** a uniquely runemastered Hollow Mask, granting level-20 Wildwood's Gifts on a sufficiently high-item-level copy. It also supplies 15% additional physical damage reduction. Unique runeforging upgrades it to a Runemastered Hewn Mask with 54 Runic Ward. Put **Rune of Reach** in one augment socket for 15% increased remnant effect.
- **Amulet:** Lament with `+3 to all Spell Skills`; select Ghost Dance as its reservation-free persistent skill.
- **Rings:** two rare caster-Genesis-tree rings with 15% Amanamu remnant-effect prefixes. The 15-16% suffix for a pickup to count as an additional pickup is valuable for Purple stacks, but is deliberately not counted as another ET explosion until tested.
- **Body:** uniquely runemastered Silks of Veneration on the Runemastered Enlightened Robe base: 124 base ES before the unique's local ES modifier, 86 Ward, and 40-50% increased Mana regeneration implicit. Socket a Warding Rune of Heart for maximum Ward equal to 5% of maximum Life.
- **Remaining rares:** high Evasion/ES, Life, ES, movement speed, attributes, and enough resistances to reach the actual caps: the selected Sunsplinter permutation for elemental resistances and 75% Chaos Resistance.
- **Weapon set 2:** invalid/disabled Spiteful Floret granting level-20 Sanguine Revelry. Its own modifiers and augments are not counted. The granted skill remaining usable from an invalid set is the requested exploit and is test-gated for 0.5.5.

The level-40 ET target is arithmetically legal:

`21 gem + 5 Chaos Spell + 4 all Spell + 3 Lament + 6 Sunsplinter + 1 Dark Entries = 40`

### Skills and supports

**Explosive Transmutation**

- Spell Cascade
- Vorana's Siege
- Zenith II
- Blindside
- Atziri's Communion

[Atziri's Communion](https://poe2db.tw/us/Atziris_Communion) converts the 30-Spirit reservation into 19.8% maximum-Life reservation. Acolyte has no Spirit, so this is what makes ET usable.

**Into the Breach**

- Harmonic Remnants II
- Remnant Potency I
- Khatal's Rejuvenation

Potency I is intentional: +20% effect without the three- or four-second delay on the stronger tiers.

**Wildwood's Gifts from Hollow Mask**

- Harmonic Remnants II, if the current repeated-support rules allow a separate copy

**Sanguine Revelry from disabled Spiteful Floret**

- Harmonic Remnants II if separately legal

**Driver: Bonestorm**

- Bleed III
- Blind II
- Armour Break III
- Withering Touch
- Chaos Attunement or another explicit source of Chaos damage before relying on Withering Touch

Tap the driver rather than continuously channeling it: the job is to Blind, Bleed, fully break Armour, and establish Wither while keeping Mana above 90% for Zenith II. Allocate Wither Away; ten ordinary Wither stacks then produce approximately 60% increased Chaos damage taken. Ancient Rune of Decay can raise the modelled magnitude further, but only after Ward sustain is proven.

Add a manually cast **Despair** for bosses if another button is acceptable. The main tables stop at zero Chaos Resistance and do not count it. Against a target that Despair actually moves from 0% to -25% Chaos Resistance, its effect on the large gained-as-Chaos portion raises the nine-to-ten-stack moderate-critical rows to about 0.497-0.538M DPS at one contact per trigger, 0.994-1.075M at two, or 1.490-1.613M at three. The corresponding optimistic rows become approximately 0.699-0.756M, 1.397-1.513M, and 2.096-2.269M. Scale those numbers down if the target's effective curse result is weaker.

### Remnant production

At level 20, Into the Breach and Wildwood's Gifts each create one remnant event every 1.21 seconds:

`1 / 1.21 = 0.82645 events per second`

Sanguine Revelry is capped at one event every two seconds: 0.50/s, provided the driver hits a Bleeding target at least that often. Harmonic Remnants II contributes 20% chance to create an additional object and Remnant Attraction contributes 10%, for a modelled 1.30 object multiplier. The fast row assumes the current duplicate-support rules permit a separate Harmonic copy on every generator; if 0.5.5 changes that, recalculate per generator.

| Generator | Base events/s | Expected objects/s with +30% |
|---|---:|---:|
| Into the Breach | 0.82645 | 1.07438 |
| Wildwood's Gifts | 0.82645 | 1.07438 |
| Sanguine Revelry | 0.50000 | 0.65000 |
| **Total** | **2.15290** | **2.79876** |

The 2.79876 figure is generated world objects, not guaranteed collected explosions. Into and Wildwood place remnants within an 8.5-metre area, so movement and collection radius matter. Whether an additional object or a pickup that “counts as an additional Remnant” creates another ET trigger is test-gated. The conservative fallback trigger rate is 2.1529/s, or 76.9% of the fast model.

There are two further 5% additional-remnant small passives on the tree. If one is reachable without waste, the same total becomes `2.15290 x 1.35 = 2.90640 objects/s`, giving a small collection/travel margin over Khatal's 2.8/s ceiling. Do not path badly merely to take it; the cap means the second small adds no single-target DPS here.

Khatal's 20% reservation multiplier can support zero-reservation Into/Wildwood without creating flat Spirit reservation: zero times a multiplier is still zero. ET remains the only skill using Atziri's Communion in this setup.

### Purple-flame scaling

A Purple Flame grants 7% of damage as extra Chaos per stack, up to ten stacks. The proposed effect package is:

- 50% from Choice of Power.
- 50% from level-20 Into the Breach.
- 15% + 15% from two Amanamu ring prefixes.
- 15% from the helmet rune.
- 20% from Remnant Potency I.

Total increased effect is 165%. The engine floors each stack:

`floor(7 x 2.65) = 18% gained as extra Chaos per stack`

Nine stacks are 162%; ten are 180%. At the baseline 1.07438 Purple objects/s, two perfect 16% ring suffixes produce an unconstrained expected `1.07438 x 1.32 x 8 = 11.35` stack-arrivals within the eight-second duration, if additional objects and counted pickups both grant the buff. That is enough to make ten stacks reachable in steady combat, but it is **not** an exact ten-stack uptime claim: the cap, individual expiry timing, travel time, missed pickups, and the two test-gated additional-remnant interactions all matter. The tables therefore expose nine and ten separately instead of hiding them inside a dubious uptime average.

### Defensive Acolyte damage calculation

The optimistic scenario uses these **targets**, not hidden PoB values:

- level-40 ET;
- +300% generic Spell/AoE damage;
- +150% increased Chaos damage;
- +349% increased Spell critical chance;
- +180% increased critical damage bonus;
- Blind, isolated target, Mana above 90%, ten Wither stacks at 60%;
- 2.80 collected triggers/s;
- full Armour break for Physical mode;
- no remaining resistance or mitigation unless the sensitivity table says otherwise.

The generic and Chaos totals are plausible from item affixes: a perfect rare wand can carry 168% combined Spell damage and 119% Chaos damage across its prefixes, and two caster-Genesis rings can contribute another 78% Spell damage. The critical budget is the strained part. After reserving ring affixes for Amanamu effect and additional pickups, simultaneously reaching +349% critical chance, +180% critical bonus, 315 Mana/s, 4-5k ES, and capped resistances requires a crit-heavy tree and several excellent jewels. There is no finished level-90 allocation proving all of those targets together, so the main table is an **optimistic ceiling**, not a promised character-sheet result. If the wand's last suffix is Mana regeneration, critical chance has to come from tree/jewels; the table does not silently grant both suffixes.

For a Physical-mode pickup with gain-as-Chaos ratio `G`, one Cascade contact is:

`3,246.5 x [4.00 + G x 5.50 x 1.60] x crit-average x 1.092 x 2.80`

The weighted Chaos-remnant component also receives the global gain-as-Chaos buff. That is legal in PoE 2: gain conversion can add a damage type from base damage already of that same type. The gained portion then scales only with its destination-type modifiers.

Physical base critical chance becomes:

`14% x 4.49 x 1.15 = 72.29%`

Critical damage bonus becomes:

`100% x 2.80 x 1.15 = 322%`

Therefore critical average is:

`1 + 0.7229 x 3.22 = 3.3277`

The resulting weighted model assigns Purple objects to Chaos and, provisionally, all other objects to Physical:

| Purple stacks | DPS at one contact/trigger | DPS at two contacts/trigger | DPS at three contacts/trigger |
|---:|---:|---:|---:|
| 0 | 0.159M | 0.318M | 0.478M |
| 5 | 0.390M | 0.781M | 1.171M |
| 8 | 0.529M | 1.058M | 1.587M |
| 9 | 0.575M | 1.150M | 1.726M |
| 10 | 0.621M | 1.243M | 1.864M |

If Harmonic/additional remnants do not trigger ET, multiply those figures by `2.1529 / 2.80 = 0.769` before applying actual contact count.

A more defensively comfortable critical budget of +200% increased critical chance and +130% increased critical bonus, while retaining the same damage prefixes and debuffs, gives this sensitivity check:

| Purple stacks | DPS at one contact/trigger | DPS at two contacts/trigger | DPS at three contacts/trigger |
|---:|---:|---:|---:|
| 9 | 0.409M | 0.817M | 1.226M |
| 10 | 0.441M | 0.882M | 1.324M |

This is the more honest planning range until an exact passive tree and actual items exist.

Mapping sensitivity at ten Purple stacks, assuming one Cascade contact per trigger:

| Assumed mode for all non-Purple remnants | DPS at one contact/trigger | Extra condition in this row |
|---|---:|---|
| Physical | 0.621M | Fully broken Armour |
| Chaos | 0.567M | 60% Wither |
| Lightning | 0.417M | 50% base boss resistance, no further reduction |
| Cold | 0.367M | 50% base boss resistance, no further reduction |
| Fire | 0.349M | 50% base boss resistance, no further reduction |

That table is why the launch mapping test is mandatory. Elemental Weakness or other resistance reduction improves the elemental rows; it does nothing for the Physical working model.

#### Higher-explosion defensive-Acolyte switch

If the priority is maximum remnant/explosion density rather than the safest version, obtain a **Lament rolled with Mana Remnants** instead of Ghost Dance and put one perfect `Spells have 25% increased Cooldown Recovery Rate` Genesis prefix on a ring. Lament's granted persistent skill has no reservation, so this works despite Embrace the Darkness.

With Mana Remnants maintained, the four generators make 2.6529 base events/s or 3.4488 expected objects/s under the +30% model. If additional objects independently trigger ET, one ring raises the cooldown ceiling to 3.30/s and the build can feed it. This multiplies the 2.8/s damage rows by `3.30 / 2.80 = 1.1786`:

| Critical target, 9-10 Purple stacks | One contact | Two contacts | Three contacts |
|---|---:|---:|---:|
| Moderate | 0.482-0.520M | 0.964-1.040M | 1.446-1.559M |
| Optimistic | 0.678-0.732M | 1.355-1.464M | 2.033-2.196M |

This is conditional, not the headline build. It loses the modelled 160-300 ES/s Ghost Dance recovery, the driver must demonstrably keep an elemental Ailment on the boss and crit at least once every two seconds, and the 3.30/s gain disappears if Harmonic/additional objects do not each trigger ET. Two CDR rings advertise a 3.80/s ceiling but the expected 3.4488 objects/s cannot sustain it without roughly one boss Freeze every 2.85 seconds through Siphon Elements or an active Snap loop.

At 3.30/s, Blindside-supported ET burns `74 x 1.20 x 3.30 = 293.04 Ward/s`. With about 623 Ward and Obsession, the conservative Equinox formula requires approximately `(293.04 - 62.3) / 0.60 = 384.6` pre-Equinox Mana regeneration. Target 390+/s for this switch. A Cooldown Recovery support is a poor substitute: its 130% cost multiplier makes the Ward engine substantially worse.

### Defensive Acolyte Ward engine

Approximate maximum Ward:

- Runic Fork: 300
- uniquely runemastered Hollow Mask: 54
- uniquely runemastered Sunsplinter: 33
- Runemastered Enlightened Robe: 86
- Warding Rune of Heart at 2.8-3.2k maximum Life: 140-160
- **Total: approximately 613-633**

The ordinary 613-633 maximum already holds the five stored 88.8-Ward ET uses; do not assume ordinary regeneration can create Animus-style overflow.

At eight Khatal stacks, ET consumes approximately:

`74 x 1.20 Blindside x 2.80 = 248.64 Ward per second`

Equinox gives 40% less Mana regeneration and applies Mana regeneration recovery to Ward. Obsession makes incoming damage bypass Ward and doubles Ward regeneration. At about 623 maximum Ward, doubled native regeneration is about 62.3/s. Let `M0` be Mana regeneration before Equinox:

- conservative recovery: `62.3 + 0.60 x M0`;
- optimistic interpretation if Obsession doubles the converted recovery too: `62.3 + 1.20 x M0`.

Target at least **315 Mana/s before Equinox**; the conservative line is then about 251 Ward/s. Since innate Mana regeneration is 4% of maximum Mana per second, one transparent way to exceed that is roughly 2,000 maximum Mana with 300% increased Mana regeneration (`2,000 x 0.04 x 4.00 = 320`). The robe implicit and Perfect Inspiration contribute to that increase; the remainder must actually exist on tree/gear. If the finished character does not show at least 315/s, the Ward engine is not complete.

A Rune of Nourishment and a Runemastered Heavy Belt can apply a portion of Life-flask recovery to Ward as an emergency backup. With Obsession, Ward is not a defensive layer; it is a protected ET fuel tank.

### Defensive Acolyte survivability

Target ranges before perfect gear:

- maximum Life before Atziri reservation: 2.8-3.2k;
- usable Life after 19.8% reservation: 2.246-2.566k;
- Energy Shield: 4.0-5.0k;
- Darkness: 1.95k fresh-hit buffer;
- Evasion: approximately 8-15k is a provisional target with the unique helmet/offhand and ES/Ward body; do not assume a high-Evasion showcase number without an itemised tree;
- Block: 20% from Sunsplinter before any other source;
- 15% additional physical damage reduction from Hollow Mask;
- elemental caps: 76/77/78 in the Sunsplinter-selected order; Chaos: 75%.

Fresh non-Chaos hit capacity is:

`usable Life + ES + Darkness = 8.20-9.52k`

Chaos removes twice as much ES, so effective fresh Chaos capacity is:

`usable Life + ES/2 + Darkness = 6.20-7.02k`

These are resource-side capacities for damage that reaches the pools, not unmitigated incoming-hit values. Capped resistances, Evasion, Block, and Hollow's physical reduction apply outside those sums.

Ghost Dance is conditional recovery, not permanent regeneration. After a Ghost Shroud is lost, its 120%-of-Evasion-per-minute rate is approximately 2% of Evasion per second: about 160-300 ES/s at the provisional 8-15k Evasion range while the recent-loss condition is active. It can hold three shrouds, and level 20 generates one about every 10.1 seconds.

#### Mórrigan defensive swap

[Mórrigan's Insight](https://poe2db.tw/M%C3%B3rrigans_Insight) can support ET, consume Freeze, and trigger level-matched [Nature's Exchange](https://poe2db.tw/us/Natures_Exchange). At level 40 that grants 9,275 Guard for eight seconds on an eight-second cooldown, but [Guard](https://poe2db.tw/us/Guard) is capped at 200% of maximum Life. With 2.8-3.2k Life, the usable Guard is approximately 5.6-6.4k.

Use Mórrigan in place of Blindside for dangerous bosses and change the driver to Cold Attunement plus Freeze Buildup II. ET cannot itself build the Freeze required by Mórrigan. The supported hit that consumes Freeze gets its damage bonus, but sustained damage between activations loses Blindside; do not count full Guard uptime until the Freeze/trigger loop is tested.

## Damage variant: Volatility Acolyte

Take Waking Dream/Choice of Power plus Inner Turmoil/Unravelling instead of Darkness. This preserves the Purple engine and adds the Volatility ramp, but removes the 1,950 fresh-hit buffer.

### Changes from the defensive version

- ET links: Cascade, Vorana, Zenith II, Chaos Attunement, Blindside.
- Add Mana Remnants. With Hollow's reservation efficiency it costs roughly 15 Spirit.
- Available base Spirit is 100; ET uses 30 and Mana Remnants roughly 15. Lament still makes Ghost Dance free. There is no need to acquire 400 Spirit.
- Add Volatility to the Bonestorm driver and allocate Precise Volatility.
- Raise pre-Equinox Mana regeneration target to 400+/s because Chaos Attunement plus Blindside raises the ET Ward burn to roughly 297-300/s. For scale, 2,500 maximum Mana with 300% increased Mana regeneration is 400/s before Equinox.

The four generators produce 2.6529 base events/s or 3.4488 expected objects/s. Mana's 0.50/s component requires a critical hit against an elementally Ailmented target at least every two seconds. With no other CDR, ET is capped at 2.8/s. If extra objects do not trigger but the driver maintains both conditional generators, the fallback is still `2.6529 / 2.80 = 94.75%` of that capped model.

After confirming that extra objects independently trigger ET, one perfect 25% Genesis caster-ring CDR prefix is credible here: Khatal plus the ring gives a 3.30/s ceiling that the 3.4488 expected-object model can supply. Multiply the following table by 1.1786 for that variant, giving one-contact figures of approximately 0.760M, 0.844M, 1.214M, and 1.717M at 0, 20, 100, and 200 Volatility. It requires materially more Ward recovery. Two rings reach 3.80/s but are short 0.3512 remnants/s even in the favourable object model; quality-20 Siphon Elements can fill that only if the build Freezes the boss roughly once every 2.85 seconds. Its Ignite/Shock chances are 7%, not a general high-frequency proc.

At the same full Purple/Wither/zero-post-debuff-mitigation assumptions, the provisional Physical-remnant model is:

| Detonated Volatility | DPS at one contact/trigger | DPS at two contacts/trigger | DPS at three contacts/trigger |
|---:|---:|---:|---:|
| 0 | 0.645M | 1.290M | 1.935M |
| 20 | 0.716M | 1.432M | 2.148M |
| 100 | 1.030M | 2.060M | 3.090M |
| 200 | 1.457M | 2.914M | 4.371M |

The 200 line is not normal boss-start damage. Volatility caps at 200, can gain at most ten per second, lasts four seconds unless refreshed, and requires at least twenty ideal seconds to reach 200 absent a special Catharsis outcome. For the user's expected sub-ten-second bosses, the 0-100 rows are the relevant ones.

The 3.30/s CDR variant with Chaos Attunement and Blindside costs about `74 x 1.20 x 1.20 x 3.30 = 351.65 Ward/s`, requiring roughly 485+/s pre-Equinox Mana regeneration under the conservative recovery interpretation. If that is too expensive, Efficiency II in place of Chaos Attunement reduces the same rate to `74 x 1.20 x 0.60 x 3.30 = 175.82 Ward/s`, at the cost of the Attunement damage. Do not use Cooldown Recovery II merely to reach the breakpoint: its own 130% cost multiplier is substantially less efficient than the ring.

Rough defence is 2.8-3.2k Life plus 4-5k ES: 6.8-8.2k against non-Chaos and approximately 4.8-5.7k effective against Chaos, before optional Guard. That is why this is the damage variant rather than the recommendation.

Blackflame Covenant is not a baseline item. It applies to Fire damage from Fire spells; it does not convert ET's Physical, Cold, Lightning, or Chaos modes, so it is too narrow for an unidentified mixed remnant mapping.

## Audited wildcard: Stormweaver remnant loop

[Storm's Recollection](https://poe2db.tw/us/Stormweaver) makes every remnant you create reappear once, three seconds after collection. This makes Stormweaver the literal remnant-density winner, but not the highest-damage ET ascendancy.

A clean single-target package is level-20 Wildwood's Gifts, disabled-set Sanguine Revelry, and reservation-free Mana Remnants from Lament:

`0.82645 + 0.50 + 0.50 = 1.82645 original remnants/s`

After the three-second delay, Recollection makes 3.6529 pickup opportunities/s in steady state. Across a ten-second boss, only remnants collected in the first seven seconds have time to reappear, so the no-travel-loss average is `1.82645 x 17 / 10 = 3.1050/s`. Do **not** add Harmonic and Remnant Attraction if the objective is literally for every pickup to explode: their 2.3744 original objects/s would become 4.7488/s after the delay, well beyond ET's normal 2.8/s ceiling.

The clean version instead uses two perfect 25% Genesis Spell-CDR prefixes plus Khatal for a nominal 3.8/s ET ceiling, enough for the 3.6529 steady stream. This relies on two launch tests: the hidden ET explosion must inherit Spell CDR, and a Recollection reappearance must count as a fresh pickup for ET.

The legal level stack remains `21 + 5 elemental Spell + 4 all Spell + 3 Lament + 6 Sunsplinter + 1 Dark Entries = 40`. Use the same +300-Ward Runic Fork shell, but with one or two high-end elemental gain-as prefixes; use Cascade, Vorana, Zenith II, Pinpoint Critical, and Blindside on ET. Put Khatal on Wildwood and drive Sanguine/Mana with a tapped Bonestorm carrying Bleed, Blind, Armour Break, and enough Lightning gain/buildup to maintain the two modelled Shocks.

A defensive eight-point layout is Storm's Recollection plus Constant Gale/Force of Will plus Strike Twice. With one or two 30% elemental gain-as wand prefixes, +250% generic damage, the report's moderate-to-optimistic critical brackets, and two 15% Shocks, the Physical-remnant working model gives roughly **0.13-0.27M one-contact DPS** at the clean steady rate; three favourable Cascade contacts give about **0.40-0.82M**. An extreme triple-gain wand can approach **0.40M one-contact or 1.20M at three contacts**, before the same unknown-remnant-mapping caveat as the other routes. First-ten-second averages are about 15% lower than the steady figures.

Defensively target 2.8-3.2k Life, 4-5k ES, 2.5-3.5k Mana, and 8-15k Evasion, with Sunsplinter's 20% Block, Hollow's 15% additional PDR, 76/77/78 elemental caps, and 75% Chaos Resistance. Force of Will sends 20% of damage to Mana before Life. With at least 0.8k usable Mana behind ES, approximate fresh capacity is 7.5-9.0k against non-Chaos and 5.5-6.5k against Chaos. It can use Lament Mana Remnants and reserve Ghost Dance normally, and Constant Gale improves Mana/Ward recovery.

That is substantially more stable than Shaman and is the best choice if the visual remnant storm matters more than boss DPS. It still loses Acolyte's 162-180% gain-as-Chaos stack engine and Darkness, so it does not replace the defensive Acolyte recommendation.

## Alternative build: Rage Shaman

### Ascendancy and Rage ceiling

Take the complete Druidic Champion/Furious Wellspring Rage branch, then Wisdom of the Maji and Reactive Growth/Adaptation. Turning Seasons is a damage alternative, but costs one of those defensive/utility branches.

Do not take Primal Hunger: “No inherent bonus from Rage” disables the Rage effect that powers Druidic Champion, Mystical Rage, Ichlotl's Inferno, Grace of the Ancestors, and Vengeful Fury.

With mandatory Hollow Mask, a practical unconditional maximum is 75 Rage:

| Source | Added maximum Rage | Running total |
|---|---:|---:|
| Base | 30 | 30 |
| Battle Trance wheel | 14 | 44 |
| Two Commanding Rage smalls | 4 | 48 |
| Shaman path smalls | 6 | 54 |
| Furious Wellspring | 7 | 61 |
| Unforgiving | 4 | 65 |
| Tzamoto in Hollow Mask | 4 | 69 |
| Bonded Legacy of Horns of Bynden in Hollow | 3 | 72 |
| Bonded Legacy of the Bristle in body | 3 | 75 |

Omitting the expensive Commanding pair gives 71. A rare-helmet Vorana modifier is incompatible with Hollow Mask. Hateforge empties Rage at the cap and is unsuitable. Conditional Glory-based maximum Rage and rare Ruby jewels are not included.

Fox Idol in the bonded body raises global skill quality to 25%. At level 21 and 25 quality, Berserk gives 72.5% increased Rage effect. At 75 Rage:

- Druidic Champion: `floor(75 / 2) x 1.725 = 63.825% more Spell damage`.
- Mystical Rage: 258.75% increased Spell damage.
- Ichlotl's Inferno: 129.375% increased Fire damage.
- Grace of the Ancestors: 129.375% increased Evasion.
- Vengeful Fury: 129.375% increased Armour.
- Legacy of Horns of Bynden: another 129.375% increased Armour, for 258.75% total from the two Rage-scaled Armour effects.
- Berserk Life loss: 7.5% of maximum Life per second; increased Rage effect does not increase this loss.

### Core equipment

- **Weapon:** +300-Ward Runemastered Runic Fork with +5 Fire Spell levels, +4 all Spell levels, and either Mana regeneration or spell critical chance. Sockets: Hedgewitch Assandra's Rune of Wisdom, bonded Legacy of Lifesprig, and Perfect Inspiration. On a wand, Perfect Inspiration grants Mana regeneration and bonded Mana-cost efficiency.
- **Offhand:** uniquely runemastered Sunsplinter, adding 33 Runic Ward.
- **Helmet:** uniquely runemastered Hollow Mask granting level-20 Wildwood's Gifts, adding 54 Runic Ward. Socket Tzamoto and bonded Legacy of Horns of Bynden.
- **Amulet:** Strugglescream with Mystical Rage, Ichlotl's Inferno, Grace of the Ancestors, and Vengeful Fury.
- **Body:** Runeforged Warlord Cuirass: 466 base Armour, 86 Ward, and 15-25% of Armour applying to elemental damage. Socket Warding Rune of Heart, bonded Legacy of Blackbraid, bonded Legacy of Bristleboar, and bonded Fox Idol. Blackbraid adds another 50% Armour application to elemental damage and 15% all-elemental resistance while bonded; Fox Idol supplies +5 global skill quality.
- **Weapon set 2:** disabled Spiteful Floret for level-20 Sanguine Revelry, exactly as in the Acolyte version.
- **Remaining rares:** Life, Armour/Evasion, attributes, movement, and actual cap-filling resistances.

Level 40 is legal:

`21 gem + 5 Fire Spell + 4 all Spell + 6 Sunsplinter + 1 Dark Entries + 1 Assandra's Wisdom + 2 Legacy of Lifesprig = 40`

### Skills, remnants, and sustain

**Explosive Transmutation:** Spell Cascade, Vorana's Siege, Zenith II, Pinpoint Critical, Blindside.

Rageforged I/II are not legal ET supports because ET itself has the Persistent tag, which Rageforged explicitly excludes.

**Generators:**

- Wildwood's Gifts: Harmonic Remnants II and Khatal's Rejuvenation.
- Sanguine Revelry: Harmonic Remnants II and Remnant Potency III.
- Mana Remnants: Harmonic Remnants II.

This is 1.8264 base events/s or 2.3744 expected objects/s with the +30% object model, provided Bonestorm hits a Bleeding target and critically hits an elementally Ailmented target at least once every two seconds. It is lower than defensive Acolyte's 2.1529/2.7988.

Taking both 5% additional-remnant tree smalls raises the expected-object line to `1.826446 x 1.40 = 2.55702/s`, still below Khatal's 2.8/s ceiling. The two 16% Genesis-ring pickup suffixes could exceed the ceiling only if their “counts as picking up an additional Remnant” event retriggers ET; that is deliberately not counted before the launch test. Expansive Alloy's transformed 17-23% pickup mod is Martial Artist-only, not a Wisdom of the Maji trick.

**Driver:** Bonestorm with Bleed III, Blind II, Armour Break III, Fire Attunement, and Ignite III. Use Cold Attunement plus Freeze Buildup for the Mórrigan defensive swap.

**Animus Exchange:** level 28, raised to 25 quality by Fox Idol, with Cooldown Recovery II. This is the Ward-recovery skill; Animus Splinters is a different meta skill. One activation every 2.581 seconds sacrifices 976 Life, costs 218 Mana, and grants 683.2 Ward:

- Ward: `683.2 / 2.581 = 264.7/s`.
- Life: `976 / 2.581 = 378.2/s`.
- Mana: `218 / 2.581 = 84.5/s`.

Approximate maximum Ward is 300 Fork + 86 Cuirass + 54 Hollow + 33 Sunsplinter + 190-215 Heart = **663-688**. At 75 Rage, supported ET costs roughly 88 Ward per trigger, or 209/s at 2.3744 triggers and 246.4/s at the 2.8 cap. Animus plus native Ward regeneration covers the ordinary fuel demand. Since non-fatal hits do not consume Ward, Obsession is unnecessary and harmful here.

The 150% overflow cap is approximately 995-1,032 Ward. At the 4,000-Life reference it is about 1,010, giving ample fuel for successive ET triggers after the five stored cooldown uses are released.

#### Life sustain

At 4,000 maximum Life:

- Berserk loses 300 Life/s.
- Animus loses 378.2 Life/s.
- Total planned loss is 678.2 Life/s, or 16.955% maximum Life/s.

At 75 Rage, level-20 Sanguine Revelry with Remnant Potency III recovers:

`[1.5% x floor(75 / 5) x 1.40] x 0.65 = 20.475% maximum Life/s`

That starts only after Potency III's three-second remnant delay. If Harmonic does not produce ET-relevant extra objects, the 0.50/s base rate yields 15.75%/s, below planned self-loss. Add at least 4% independent Life regeneration and keep a flask/opener plan; do not treat average Sanguine recovery as guaranteed.

#### Rage sustain

There is an important test-gated problem: if the hidden ET trigger pays Furious Wellspring's Rage cost, a supported activation costs approximately six Rage.

At 0.5 Bonestorm casts/s:

- ET: `6 x 2.3744 = 14.246 Rage/s`.
- Animus: `6 / 2.581 = 2.325 Rage/s`.
- Bonestorm: `6 x 0.5 = 3 Rage/s`.
- Total: **19.571 Rage/s**, or 22.125/s at the ET cap.

Fox Idol raises Sanguine to 5 quality, so Sanguine with Potency III supplies about `9.5 x 1.40 x 0.65 = 8.645 Rage/s` after its delay. Furious Wellspring supplies 4.5 Rage/s before increased Mana-regeneration scaling. Required increased Mana regeneration is therefore approximately:

`(19.571 - 8.645) / 4.5 - 1 = 142.8%`

At the 2.8 trigger cap it rises to about 200%. Clarity I + II supply 80%; a top Mana-regeneration wand suffix can supply roughly another 69%, but that suffix competes with spell critical chance after +5 Fire and +4 all Spell levels. At one Bonestorm cast/s, add another 66.7 percentage points to the requirement.

Fox Idol raises Mana Remnants to 25 quality, restoring about `331 x 0.65 = 215.2 Mana/s` on average, enough for Animus plus a tapped driver, but pickup timing must leave Mana above 90% for Zenith II. Total Spirit for ET, Berserk, efficient Mana Remnants, and Clarity I/II is approximately 90-93; base 100 Spirit is enough.

### Shaman damage calculation

The ordinary model assumes level-40 ET, +250% generic Spell/AoE before Rage, +300% critical chance, +220% critical bonus, Blind, isolation, Mana above 90%, fully broken Armour, zero post-debuff mitigation, and the Physical non-Purple working model. As with the Acolyte ceiling, those simultaneous offensive totals are optimistic until backed by a finished tree; “normal wand” below means no gain-as-extra prefixes, not inexpensive or relaxed supporting gear.

At 75 Rage and 2.3744 triggers/s:

`3,246.5 x 6.0875 increased-factor x 1.63825 Druidic x 1.092 support-product x 3.576 crit-average x 2.3744 = approximately 300,200 DPS assuming one Cascade contact per trigger`

| Case | DPS at one contact/trigger | DPS at two contacts/trigger | DPS at three contacts/trigger |
|---|---:|---:|---:|
| Normal wand, reliable 2.3744/s | 0.300M | 0.600M | 0.901M |
| Normal wand, capped 2.8/s | 0.354M | 0.708M | 1.062M |
| Perfect two 30% gain-as prefixes, reliable | 0.500M | 0.999M | 1.499M |
| Perfect three 30% gain-as prefixes, reliable | 0.590M | 1.179M | 1.769M |

The gain-as versions are legal in principle because Fire, Cold, and Lightning gain-as prefixes occupy the three prefixes while +5 Fire, +4 all Spell, and Mana regeneration or critical chance occupy the suffixes. In practice the last suffix should be Mana regeneration to sustain Furious Wellspring, so the modelled +300% critical chance must come from tree/jewels. These are nevertheless extreme six-affix crafts. Elemental gained portions also need resistance reduction, and the two-prefix table still requires essentially perfect three-area overlap merely to touch 1.5M. This is not dependable 1.5M damage.

### Shaman survivability

- Life: 3.8-4.3k.
- Armour and Evasion: provisionally 8-15k each with the proposed pure-Armour body and hybrid remaining pieces. A 22-30k-each claim is not supportable without a finished passive tree and exceptional gear.
- 65-75% of Armour applies to elemental damage: 15-25% body implicit plus 50% Legacy of Blackbraid.
- Reactive Growth: 10% less elemental damage plus Adaptation.
- Block: 20% from Sunsplinter before other sources.
- Hollow Mask: 15% additional physical damage reduction.
- Elemental caps: 76/77/78 according to Sunsplinter's permutation; Chaos: 75%.
- No Armour-to-Chaos claim: Chaos is handled by its resistance cap and Life recovery.
- Ward is mostly ET fuel/fatal-hit insurance, not ordinary EHP.

Mórrigan can produce a Life-capped 7.6-8.6k Guard on a reliable Freeze/consume cycle. That is a major boss-defence option, but it occupies a damage-support slot and requires a different driver package. Without it, Shaman has substantially less raw hit capacity than either Acolyte and simultaneously loses 16.955% Life/s to its own engine at the 4k-Life reference point.

## Final choice

1. **Start defensive Acolyte.** It reaches level 40 cleanly, has the strongest short-fight damage at a rate sufficient to feed ET, does not spend its Life maintaining Rage, and has Darkness on top of Life/ES.
2. Use the Physical-remnant damage rows only after confirming the actual ET mapping. If mapping is elemental, change the curse/debuff package before changing the whole ascendancy.
3. Run Cascade for clearing. For bosses, keep it only if a controlled test averages at least 1.43 contacts; otherwise use a direct critical support.
4. Add Mórrigan as the dangerous-boss swap once the Freeze loop is reliable.
5. If testing shows extra remnant objects trigger ET and short fights regularly reach high Volatility, respec the second ascendancy branch into Inner Turmoil/Unravelling. Do not start there merely for the 200-Volatility screenshot number.
6. If the actual priority is the densest possible reliable remnant field, use Stormweaver's Recollection loop. It is safer and more mechanically coherent than Rage Shaman, but its measured damage target is below Purple Acolyte.

Shaman is the style choice, not the optimisation choice. Its normal configuration is materially behind, and the configuration that catches Acolyte requires a nearly perfect gain-as wand, favourable three-area overlap, delayed Sanguine recovery, and a difficult Rage-regeneration threshold.

## Mandatory 0.5.5 launch tests

Do these with an unmodified low-level zone or repeatable boss before serious crafting:

1. Confirm a skill granted by disabled Spiteful Floret still remains active while all item modifiers/augments are disabled.
2. Pick up each remnant family separately and record which ET damage mode it selects.
3. Confirm whether Harmonic Remnants' additional object independently triggers ET.
4. Confirm whether a ring pickup that counts as an additional remnant independently triggers ET or only repeats the remnant buff.
5. Confirm whether each hidden ET explosion pays Furious Wellspring's Rage cost.
6. Measure actual Cascade contacts on a stationary boss at 1.8-2.0m base radius.
7. Confirm Equinox/Obsession recovery with a fixed Mana-regeneration value; use the conservative line until observed.
8. Confirm Mórrigan consumes Freeze, triggers level-matched Nature's Exchange, and can maintain the intended Guard cycle.
9. Verify that Zenith II remains active through Animus/driver Mana spikes.
10. Put exactly 25% Spell cooldown recovery on a ring and time ET's hidden explosion; verify the expected 3.30/s Khatal breakpoint rather than assuming the hidden trigger inherits it.
11. If trying Siphon Elements, time actual boss Freeze frequency. Its quality-20 Cold roll is effectively certain per boss Freeze, but it does not create Freezes for you.
12. If considering Stormweaver, confirm that a remnant reappearing through Storm's Recollection counts as a new pickup and triggers a second ET explosion.

Any failure in tests 1-4 changes the remnant engine. Failure in 5 affects only Shaman. Failure in 8 removes optional Guard but does not invalidate defensive Acolyte. Tests 10-12 gate only the explicitly labelled faster variants.

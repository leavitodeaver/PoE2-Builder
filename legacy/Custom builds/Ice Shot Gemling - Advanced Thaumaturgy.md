# Ice Shot Gemling — q44 Pure Armour / Literal Mind over Matter

Status: audited against live 0.5.4f data on 2026-09-01. Recheck every changed value and interaction when 0.5.5 launches. This is a manual mechanics audit of the raw `27ddb` export; Path of Building and the other GUI utility were not opened.

## Verdict

The corrected build is a pure-Armour Gemling with literal Mind over Matter, no Energy Shield, no Eldritch Battery, no Renegade wheel, and no Loreweave dependency. It uses Atalui's Bloodletting to remove Ice Shot's attack cost from the defensive Mana pool, then refills Mana with direct or instant effects that are not ordinary regeneration.

The realistic, legal target before 0.5.5 is:

| Metric | Audited value or gate |
|---|---:|
| Life | approximately 3,186 |
| Mana | approximately 1,891 |
| Life + Mana | approximately 5,076 |
| Energy Shield | 0 |
| Fire / Cold / Lightning / Chaos resistance | 75 / 75 / 75 / 75 |
| Armour, no Arctic stages | 3,931–3,991 |
| Armour, seven Arctic stages | 14,021–14,235 |
| Armour, seven Arctic stages + ten Scavenged stacks | 22,714–23,061 |
| Armour applied to Elemental hits | 199% with conservative rolls; 220% with current maximum suffixes |
| Armour applied to Chaos hits | 41%, plus 15% of Chaos hit damage shifted to a random Element |
| Core-gem quality | 44% |
| Spirit used / available | 228 / 241 core; 258 / 271 with Alchemist's Boon and helmet corruption |
| Active skill buttons | 3: Ice Shot, Barrage, Pinnacle of Power |
| Certified sustained Ice Shot DPS | **not yet certified** |
| Required legal no-Pinnacle DPS for a 1.25M Pinnacle window | approximately 951k–984k, depending on Elemental share |

The old q54 claim, artificial 100% Mana-diversion package, Loreweave odds, and 1.03–1.66M DPS range were wrong. They are not retained here.

## Hard mechanical corrections

- Allocate the actual **Mind over Matter** keystone. It makes all damage hit Mana before Life and imposes 50% less Mana Recovery Rate.
- A rare Solar Amulet cannot corrupt to +10% quality of all Skills. The legal setup reaches q44, not q54.
- Fox Idol only enables Bonded modifiers of **Idols socketed in the same item**. It does not switch on bow Runes, Rune of Reach, Idol of Eeshta, or an Ancient Augment in another item.
- Current live reports say Fox Idol enables its own Bonded quality line and Panther Idol's Bonded Chaos resistance when both are in the same body armour. If that self-enabling behaviour changes in 0.5.5, the build falls to q39; use Dialla's Desire on Ice Shot as the emergency q44 fallback, at the cost of a damage support.
- Rune of Reach's direct 15% increased Remnant effect works. Its Bonded 3% maximum-Mana recovery does **not** work on this Gemling.
- Emergent Possibility's direct 15% Chaos-hit shift works. Its Bonded Chaos resistance does not.
- The linked CoEA group spends Mana for every triggered Ball Lightning and Enfeeble. Lifetap must replace Electrocute in that group.
- Mirage Electrocuting Arrow already supplies the rod and Electrocution setup; the removed Electrocute support is redundant.
- The active bow's Bonded +1 Attack level and Bonded 8% extra Physical damage are inactive. Fox in the body cannot enable them.
- Elemental Exposure is counterproductive while using Rakiata's Flow against a positive-resistance boss. Remove the Ruby/Sapphire/Emerald conditional jewel trio; Mirage already supplies Blind.

## Ascendancy and quality

Keep the Gemling Legionnaire architecture with Advanced Thaumaturgy and Gem Studded.

Legal quality for ordinary core gems:

```text
23  corrupted gem quality
+6 Gemling small passives
+5 perfect Kurgal quality suffix on Solar Amulet
+5 Paragon anoint on Solar Amulet
+5 Fox Idol Bonded line in the body armour
=44 quality
```

Cast on Elemental Ailment reaches q49 because its group also uses Dialla's Desire. Ice Shot quality currently improves Chill magnitude and movement while firing, not hit damage; the reason to retain q44 is the requested Advanced Thaumaturgy build identity and the unusually strong quality scaling on Arctic Armour, Mana Remnants, Alchemist's Boon, and the meta reservations.

## Passive-tree conversion from `27ddb`

Remove these seven ES-only passives:

- `44871`
- Pure Energy `2254`
- Patient Barrier `62230` and small `19355`
- Melding `3215` and smalls `48030`, `62436`

Allocate Mind over Matter `45918` directly from the already selected `31238`.

### Recommended boss version: Well of Power

Spend five of the six remaining points on the full Well of Power branch. Its three 2% smalls plus the 5% notable recover 11% of maximum Mana per Power Charge consumed. Pinnacle of Power consumes all three preloaded Power Charges:

```text
1,890.56 maximum Mana × 11% × 3 = 623.88 Mana immediately
```

Use the seventh replacement point as the +5 Strength attribute choice. This produces the 180 Str / 160 Dex / 159 Int and resource arithmetic used below. Gem Studded leaves all requirements satisfied.

This is the best ten-second boss package because the refill is tied to a button already in the build. If allocating these five points prevents the final legal gear from clearing the DPS acceptance gate, use the mapping/sustain version below and return the five points to damage.

### Mapping/smoother-recovery version

Instead of Well of Power, allocate:

- Empowering Remnants: `13909 → 62679 → 40985`
- Arcane Alchemy: `18882 → 24045 → 46224`

Empowering Remnants gives a 15% chance for each Remnant to apply twice. Arcane Alchemy gives +10 Int and 0.1 Mana-Flask charges per second; its path also improves flask recovery. This version has better continuous recovery but a smaller immediate boss refill.

No version allocates the Renegade wheel. Spending ten passives for its Chaos-armour node is not defensible here.

## Skills

### 1. Ice Shot — main button

- Atalui's Bloodletting
- Garukhan's Resolve
- Rakiata's Flow
- Longshot II
- Uul-Netol's Embrace

Atalui converts 100% of Mana cost to Life cost with no cost multiplier and grants 2% of damage as extra Physical per 20 Life cost, up to 40%. It replaces Elemental Armament II. This removes the attack's continuous Mana drain from Mind over Matter.

Do not assign an invented Atalui multiplier. The linked export reports 120 Mana per shot and 187.92 Mana per second with its old level/support setup. The legal setup changes both level and support, so read the final Life cost in-game and apply Atalui's displayed formula. Sustain that Life cost with the Life-leech ring suffix and the Life flask.

Keep at least seven metres from bosses for Longshot II. Rakiata's Flow is evaluated against the boss's positive resistance; do not add Exposure or Elemental Weakness unless an actual before/after test proves it helps.

### 2. Barrage — burst button

- Cooldown Recovery II
- Prolonged Duration II
- Rapid Casting II
- Efficiency II
- Second Wind III

Do not include Barrage repeats in sustained-DPS certification. It is burst and should be reported separately.

### 3. Pinnacle of Power — disabled-set button

Use ordinary Lifetap so the cast cannot remove 93 Mana from the defensive pool. At three Power Charges it lasts 15 seconds and gives 45% more Elemental damage. With the recommended Well of Power tree it also grants the approximately 624-Mana direct refill on use.

### Passive debuff delivery: Mirage Archer

- Electrocuting Arrow
- Maim
- Bleed III
- Blind II
- Culling Strike II

Refresh Mirage through the normal dodge-roll behaviour. This applies the rod/Electrocution setup, Maim, Bleed, Blind, and Culling without Tornado Shot or another attack button.

### Passive debuff delivery: Cast on Elemental Ailment

- Ball Lightning
- Enfeeble
- Breachlord's Rift
- Dialla's Desire
- Lifetap

At the linked levels, Ball Lightning and Enfeeble cost 106 + 188 Mana. Dialla's 10% less cost and Gem Studded's 30% less cost imply approximately 185 Life per full trigger after Lifetap:

```text
(106 + 188) × 0.90 × 0.70 = 185.22
```

Recheck the final level costs in 0.5.5. Enfeeble is retained for survivability; Zone of Control turns cursed enemies Hindered. Breachlord's Rift and Uul-Netol provide Armour Break. The engine as a whole supplies Chill, Freeze, Shock/Ignite when thresholds are met, Electrocution, Blind, Maim, Bleed, Enfeeble, Hinder, and Armour Break.

### Persistent skills

- Arctic Armour
- Scavenged Plating
- Combat Frenzy with Resonance
- Overwhelming Presence
- Mana Remnants
- Alchemist's Boon only with the +30 Spirit helmet corruption

Do not socket Clarity II in the core version. Ordinary regeneration is halved by Mind over Matter and is not worth another 20 Spirit here.

## Spirit budget

At q44, five ordinary 30-Spirit persistents still cost 150 total. Mirage Archer's quality supplies 88% reservation efficiency, and CoEA has q49 plus Dialla's 10% less reservation:

```text
Five ordinary persistents          150
Mirage Archer: ceil(60 / 1.88)      32
CoEA: ceil(100 × 0.90 / 1.98)       46
Core total                          228

Alchemist's Boon                    30
Optimised total                    258
```

Supply:

```text
Campaign                            100
Solar Amulet implicit               15
Solar Amulet prefix                 50
Body Armour prefix                  61
Boots suffix                        15
Core supply                        241

Helmet corruption                  +30
Optimised supply                   271
```

A 12% Amanamu reservation suffix on the body can reduce the total further, but it displaces a resistance suffix and makes the resistance plan significantly harder. It is optional, not assumed.

## Gear specification

| Slot | Required core specification |
|---|---|
| Bow | 30-quality Warmonger Bow; retain legal local Physical, flat Physical, Projectile levels, crit, accuracy and extra-arrow rolls. Fenumus direct extra Chaos, Farrul's direct 50% increased Attack Damage against Rare/Unique enemies, and Serle's extra suffix are legal. Do not count either Bonded bow line. |
| Quiver | Rare damage quiver, not Cadiro's Gambit. Prioritise flat damage, Attack Speed, crit/critical bonus, Projectile damage and one T1 elemental resistance. The rare quiver is the principal legal DPS-upgrade lever. |
| Body | 30-quality Warlord Cuirass: about 110% local Armour, 214 Life, 61 Spirit; three T1 elemental-resistance suffixes. Three sockets: Fox Idol, Panther Idol, Emergent Possibility. |
| Helmet | 30-quality pure-Armour base: about 100% local Armour, 149 Life, 149 Mana; 43% Armour applies to Elemental, 31% applies to Chaos, one T1 elemental resistance. Two sockets: Raven-Touched Shard and Rune of Reach. Instill Thicket Warding before corrupting for +30 Spirit. |
| Gloves | 30-quality Massive Mitts: about 100% local Armour, 149 Life, 124 Mana; 43% Armour applies to Elemental, +30 Dex, 8–8.9% Physical Attack Damage leeched as Mana. Two sockets: Perfect Body Rune and Perfect Vision Rune. |
| Boots | 30-quality pure-Armour Tasalian base: 149 Life, 124 Mana, 35% movement; 43% Armour applies to Elemental, +15 Spirit, one T1 elemental resistance. Two enhanced Perfect elemental-resistance Runes. |
| Amulet | Solar: +15 Spirit implicit; 50 Spirit, 119 Life, 189 Mana; +5 quality, 24% damage recouped as Mana, +24 all Attributes. Paragon anoint. Optional corruption: Mana Flasks gain 0.08–0.17 charges/second; instill first. |
| Ring 1 | Mnemonic base; 119 Life, 179 Mana, flat attack damage; Life leech, one T1 elemental resistance, 14–16% all elemental resistances. |
| Ring 2 | Mnemonic base; 119 Life, 179 Mana, flat attack damage and useful suffixes. Three maximum Void Flux augments provide 72–81% Chaos resistance. |
| Belt | Three-charm-slot Birthright Buckle with maximum charge-gain, reduced-charge-use and Armour rolls. |
| Jewels | Keep a 20% increased-Armour Ruby with offensive rolls, the Harness the Elements / Stormbreaker / Prism Guard Megalomaniac, Heart of the Well if its rolled modifiers justify it, and high-value rare damage jewels. Remove the three-colour conditional Exposure package. |

### Socket realism

The core asks for one three-socket armour item—the body—and two sockets on helmet, gloves and boots. It does not assume corrupted three-socket helmet/gloves/boots. Raven-Touched Shard must be installed and the helmet instilled before the final corruption.

Fox enables only itself and Panther because those are Idols in the same body. Emergent Possibility is an Ancient Augment, so its Bonded line stays off.

## Resource calculation

Using the boss-tree attributes of 180 Str / 160 Dex / 159 Int:

Flat Life from gear:

```text
214 body
149 helmet
149 gloves
149 boots
119 amulet
238 rings
 75 Perfect Body Rune
=1,093
```

```text
Life = (1,204 base + 20 campaign + 360 from Strength + 1,093 gear) × 1.19
     = 3,185.63
```

Flat Mana from gear:

```text
149 helmet
124 gloves
124 boots
189 amulet
358 rings
=944
```

```text
Mana = (426 base + 318 from Intelligence + 944 gear) × 1.12
     = 1,890.56

Combined = 3,185.63 + 1,890.56
         = 5,076.19
```

This clears the required 5,000 without Energy Shield. A 200-Life rather than 214-Life body still clears 5,000 narrowly; do not accept simultaneous shortfalls on several resource rolls.

## Armour calculation

Armour quality is a separate local multiplier, not an additive local increased-Armour roll:

```text
Body:   floor(496 × 2.10 × 1.30) = 1,354
Helmet: floor(374 × 2.00 × 1.30) =   972
Gloves: floor(187 × 2.00 × 1.30) =   486
Boots:  floor(280 × 1.30)        =   364
Birthright                           100–150
Equipped base total                3,276–3,326
```

With 20% global increased Armour from the retained Ruby:

```text
No Arctic stages: 3,276–3,326 × 1.20 = 3,931–3,991
```

At q44 Arctic Armour has five base maximum stages plus two quality stages. Its second quality effect supplies 44% increased Armour per stage, so seven stages add 308% increased Armour. Added to the 20% global modifier:

```text
Full Arctic: 3,276–3,326 × 4.28 = 14,021–14,235
```

At q44 Scavenged Plating gives 6.2% more Armour per stack. Ten stacks after fully breaking a Unique enemy's Armour give:

```text
14,021–14,235 × 1.62 = 22,714–23,061
```

The last line is conditional boss ramp, not standing Armour. Armour mitigation is hit-size dependent and Armour does not mitigate damage over time.

### Elemental and Chaos application

Conservative Elemental application:

```text
25 Warlord Cuirass implicit
43 helmet
43 gloves
43 boots
30 Prism Guard
15 permanent quest reward
=199% of Armour applies to Elemental hit damage
```

Current maximum suffixes can reach 50 instead of 43, producing 220%.

Chaos application:

```text
31 helmet suffix
10 Panther Idol direct line
=41% of Armour applies to Chaos hit damage
```

Emergent Possibility shifts 15% of Chaos damage from hits to a random Element, where capped Elemental resistance and the 199% application package handle it. Its Bonded +13% Chaos resistance remains inactive. Chaos damage over time receives resistance and Mind over Matter protection but no Armour mitigation.

## Resistance plan

Chaos has no endgame -60% penalty. Three 24–27% Void Flux lines plus Panther's enabled 8% Bonded line give 80–89%, capping Chaos.

Elemental resistances do have the -60% endgame penalty. Use seven T1 single-element lines:

- three on the body
- one on the helmet
- one on the boots
- one on the ordinary ring
- one on the quiver

Split them 2 / 2 / 3 among Fire, Cold and Lightning. Assign the two enhanced +22% boot Runes to the two elements with only two T1 lines. With +14–16% all Elemental resistance on the ring and +10% campaign resistance:

```text
Low elements: 45 + 45 + 16 + 22 + 10 - 60 = 78
High element: 45 + 45 + 45 + 16 + 10 - 60 = 101
```

This is roll-sensitive. For each low element, the two single rolls plus all-resistance roll need at least 103 before the Rune, campaign bonus and penalty. Minimum T1 rolls are not enough.

## How Mind over Matter changes recovery

GGG clarified that recovery-rate modifiers change the amount recovered per second without extending the duration. Mind over Matter therefore halves regeneration, recoup, timed leech, and non-instant flask recovery over their original durations. It does not make an eight-second recoup last sixteen seconds.

Direct gains and truly instant events have no duration over which a per-second rate operates. Current mechanics therefore indicate that Mana Remnants, Well of Power, on-hit/on-kill gains, Birthright's instant low-Mana flask, and instant charm recovery bypass the 50% less-rate penalty. Because 0.5.5 is imminent, explicitly test Birthright and the charm/instant-leech cases on launch before buying chase gear.

Sources: [GGG recovery-rate clarification](https://www.pathofexile.com/forum/view-thread/3907674), [community Recovery reference](https://www.poe2wiki.net/wiki/Recovery).

## Mana recovery package

### A. Atalui: stop spending the shield

This is the first and strongest layer. [Atalui's Bloodletting](https://poe2db.tw/us/Ataluis_Bloodletting) moves Ice Shot's entire cost to Life and adds Physical damage based on that Life cost. CoEA and Pinnacle also use Lifetap. Mana is reserved for incoming damage.

### B. Well of Power + Pinnacle: approximately 624 instantly

With three preloaded Power Charges, Pinnacle consumes three and the full Well branch gives 33% of maximum Mana immediately:

```text
1,890.56 × 0.33 = 623.88
```

This is the recommended boss-tree discovery. Combat Frenzy plus Resonance supplies Power Charges before the boss. [Well of Power](https://poe2db.tw/us/Well_of_Power) also gives Critical Damage after consuming a charge.

### C. Mana Remnants: approximately 669 expected Mana per eligible event

Run Mana Remnants at level 22 with exactly:

- Uhtred's Augury
- Remnant Potency I
- Harmonic Remnants II

At q44, the base grant is 325 + 88 = 413. Rune of Reach gives 15% increased effect and Remnant Potency gives 20%. Harmonic II has a 20% chance to create an additional Remnant:

```text
Expected grant = 413 × 1.35 × 1.20
               = 669.06 Mana per eligible creation event
```

The boss-tree theoretical two-second-cooldown ceiling is 334.53 Mana/second. Using the linked export's 1.566 attacks/second and 40.253% critical chance, eligible crits arrive at approximately 0.630/second. A simple cooldown-plus-wait model is:

```text
Expected cycle = 2 + (1 / 0.630) = 3.586 seconds
Expected average = 669.06 / 3.586 = 186.6 Mana/second
```

That is an expectation, not a guarantee; pickup travel and Barrage alter it. Three events in ten seconds would grant about 2,007 Mana. The mapping Empowering Remnants version multiplies those figures by 1.15: 769.42 per event and about 2,308 for three.

Mana Remnants can Overflow to 150% maximum Mana. With 1,890.56 maximum Mana, the pre-boss ceiling is approximately 2,836 current Mana, a 945-Mana buffer. [Mana Remnants data](https://poe2db.tw/us/Mana_Remnants), [Overflow reference](https://www.poe2wiki.net/wiki/Overflow).

### D. Birthright + Prolonged Ultimate Mana Flask: 1,116 instantly

Use a 20-quality Ultimate Mana Flask with:

- perfect Prolonged: 100% more recovery when used on Low Mana
- perfect `of the Ample`: 68% increased maximum charges

The campaign reward gives 30% increased Mana recovery from Flasks and Perfect Vision Rune gives another 20%:

```text
310 base × 1.20 quality × 2.00 Prolonged × 1.50 global
=1,116 Mana
```

Low Mana is 35% or less, approximately 662 Mana here. From zero, the flask jumps to about 1,116; from the Low-Mana boundary it nearly fills the pool. [Birthright Buckle](https://poe2db.tw/us/Birthright_Buckle), [Low Mana and Prolonged](https://poe2db.tw/us/Low_mana), [flask bases](https://poe2db.tw/Flasks).

The flask has 75 base maximum charges. A 68% maximum-charge suffix gives 126 charges. That is at least twelve ten-charge uses, and roughly fourteen to fifteen after Birthright's 15% reduced charges used, subject to the game's rounding rule.

### E. Raven helmet + Thicket Warding + Raindancer charms

Raven-Touched Shard lets the helmet carry Thicket Warding while the amulet keeps Paragon. Thicket gives 20% chance for a charm use not to consume charges and recovers 5% maximum Mana whenever a charm is used.

Roll each practical rare charm with:

- Raindancer's: recover 230–300 Mana when used
- `of the Ample`: 63–70% increased maximum charges

At maximum rolls:

```text
300 + (1,890.56 × 5%) = 394.53 Mana instantly per charm use
```

A 20-charge charm with 68 maximum charges stores three uses; three such charms store nine triggers. A 30-charge Staunching or Amethyst charm stores two, and a 40-charge Thawing charm stores one. Thicket's no-charge chance gives an expected 1.25 uses per paid trigger over a long sample.

Choose triggers for the encounter. Bleed, Poison and the boss's dominant Element are more valuable than blindly equipping three Elemental charms because damage over time is the Armour package's main gap. [Raven-Touched Shard](https://www.poe2wiki.net/wiki/Raven-Touched_Shard), [Thicket Warding](https://poe2db.tw/us/Thicket_Warding).

### F. Optional Alchemist's Boon

With the +30 Spirit helmet corruption, q44 Alchemist's Boon generates:

```text
0.23 base + 0.066 from q44 = 0.296 flask charges/second
```

It also grants 44% increased Attack Speed during a Life-Flask effect. That speed is conditional and must not be included in the sustained floor. [Alchemist's Boon](https://poe2db.tw/us/Alchemists_Boon).

## Every other viable Mana idea found

These are ordered roughly by usefulness to this exact bow/MoM build.

### Strong alternatives worth testing

1. **Uhtred's Chalice + Birthright.** A perfect 20-quality Chalice produces:
   ```text
   285 × 1.20 × 4.00 × 1.50 = 2,052 Mana
   ```
   Birthright should make it instant at Low Mana, and the Chalice explicitly allows flask Mana to Overflow. With another 10% flask-recovery small it reaches 2,188.8. It has 50–60% reduced maximum charges, 70% reduced recovery rate, and drains 5% Life/second without Runic Ward. Launch-test whether the instantaneous application ends or preserves the harmful flask effect. This is the largest one-press refill found. [Uhtred's Chalice](https://poe2db.tw/us/Uhtreds_Chalice).

2. **Glowswarm.** Using a Mana Flask grants Guard equal to 100% of the flask's recovery amount for four seconds. The normal flask gives about 1,116 Guard; a perfect Chalice gives about 2,052 Guard. It also grants 0.17–0.25 Mana-Flask charges/second. The cost is a rare resource/damage/resistance ring, which drops the current resource total below 5,000 unless compensated elsewhere. This is a defensive swap, not core. [Glowswarm](https://poe2db.tw/Glowswarm).

3. **Heart of the Well charm prefix.** It can roll 5–10% maximum Mana recovered whenever a charm is used. A perfect roll adds another 189 Mana to every Raindancer/Thicket event. It competes with extra-damage prefixes. Its 8–15% instant-leech prefix is also useful because only the remaining timed portion is exposed to MoM's less recovery rate. [Heart of the Well modifier pool](https://poe2db.tw/us/Heart_of_the_Well).

4. **Oisín's Oath.** The glove Mana-leech suffix normally leeches only Physical Attack damage; Ice Shot converts 80% of Physical to Cold. Oisín makes Mana leech recover from Elemental rather than Physical damage, greatly increasing the leech basis. It costs an Ice Shot support socket, so use it only if the final legal DPS still clears the gate. Timed leech is MoM-halved; a Heart of the Well instant-leech prefix makes this more attractive.

5. **Forsaken Bangle.** During its Amethyst-charm effect, 50% of Chaos hit damage prevented is recouped as both Life and Mana. Capped Chaos resistance, 41% Chaos Armour application and the 15% hit shift can make the prevented-damage basis large. It is Chaos-only, delayed over eight seconds, and MoM halves the delivered recoup; test whether the triggering hit itself is included. [Forsaken Bangle](https://poe2db.tw/us/Forsaken_Bangle).

6. **Arakaali's Gift.** When Poisoned, it recovers Mana equal to 15–20% of the Life Flask's recovery amount. A high-recovery Life flask makes this a substantial automatic direct event and simultaneously covers the build's poison/DoT weakness. It replaces one Raindancer charm and only works in poison encounters.

7. **Omen of Resurgence and Omen of Refreshment.** Resurgence fully restores Life, Mana and Energy Shield once when its Low-Life condition is met. Refreshment restores Flask and Charm charges. These are encounter consumables, not sustainable build statistics. [Omen list](https://poe2db.tw/us/Omen).

### Useful secondary layers

- 24% damage recouped as Mana on the amulet, plus an optional 10–20% body corruption. Under MoM they effectively deliver half their listed total over the original eight seconds. Keep the amulet roll; do not build the defence around it.
- 8–8.9% Physical Attack Damage leeched as Mana on gloves. It is modest after 80% conversion and its timed recovery is halved, but it is free background recovery.
- Recover 1–2% maximum Mana on kill on a rare jewel or Heart of the Well, flat 10–15 Mana on kill from a quiver corruption, and Perfect Inspiration Rune's 40 Mana per enemy killed on a martial weapon. These are excellent mapping tools and irrelevant on an addless boss.
- Flat Mana gained on hit: 1–10 on gloves, 1–2 on jewels, or approximately 15 per hit from Thief's Torment. These are direct but small; Thief's Torment costs too much ring value for the core build.
- Arcane Remnants: 3% maximum Mana whenever any Remnant is collected. It adds about 56.7 Mana per pickup here and becomes better if Siphon Elements or Lingering Illusion is used to create additional Remnant types.
- Siphon Elements or Lingering Illusion plus Arcane Remnants. Lingering Illusion has some synergy with the dodge rolls already used for Mirage Archer, but each costs Spirit/setup and neither beats the core package without a dedicated rework.
- Wellspring: 30% increased Mana recovery from Flasks and 8% Attack/Cast Speed during a Mana-Flask effect. Efficient Alchemy gives 40% increased Life/Mana flask recovery while a charm is active plus charge gain; Combat Alchemy gives 20% recovery and 10% no-charge chance. They are strong if a future tree route is cheaper than the current damage nodes.
- Mana-Flask charge generation can be stacked from Alchemist's Boon (0.296/s at q44), Arcane Alchemy (0.1/s), the Solar-Amulet corruption (0.08–0.17/s), Rune of Acrobatics on the active bow (0.2/s), and Glowswarm (0.17–0.25/s). Rune of Acrobatics costs an offensive bow augment. Passive generation is helpful between packs but does not replace stored charges in a ten-second boss.
- Urgent Call recovers 2% maximum Mana per Warcry. It adds a button and is too small for this build.
- Mana Drain, Feast of Flesh/corpse recovery, Mark of Siphoning and Font of Mana work mechanically but violate the button/setup budget or weapon plan.

### Investigated and rejected

- **Midnight Braid:** 50% recoup appears attractive, but MoM reduces the delivered rate/total and it takes eight seconds. It also replaces Birthright.
- **Keelhaul:** 0.25 Mana-Flask charges/second and increased flask recovery are weaker than Birthright's instant low-Mana refill.
- **Oasis / The Traitor:** 30% more flask recovery is good, but losing all charms gives up Raindancer, Thicket, ailment protection and stored automatic refills.
- **Pragmatism:** charge-free charms are powerful, but the Armour/Evasion body loses the Warlord implicit, body Spirit, resource rolls and the body augment package.
- **Hollow Mask / Wildwood Bloom charge loops:** wrong defence base, elemental-resistance penalty and too much setup.
- **Alkem Eira / shield blocked-damage recoup:** the bow cannot use a shield, and the disabled set's item modifiers are nullified.
- **Sacrifice of Mind:** overflowed regeneration is still regeneration and is halved again; insufficient return.
- **Jiquani's Thesis:** the spend threshold is unrealistic here and the item is prohibitively expensive.
- **Convalescence:** banned and not used.
- **Eldritch Battery:** banned and not used.
- **Mana on block:** incompatible with the bow.
- **Legacy Inspiration's Mana-to-Life Bonded line:** global Bonded access is a Shaman mechanic, not this Gemling.

## Disabled weapon-set exploit

Retain Adonia's Ego in weapon set two and place the same Limit-1 Fenumus augment on the active bow and Adonia so the second set is attribute/augment-invalidated. Under the observed exploit, the granted Pinnacle of Power remains usable while every Adonia modifier and augment is nullified.

Count none of the following:

- Adonia's maximum Mana
- its spell levels or cast speed
- its Power-charge modifier
- its resist penalty
- either inactive-set augment line

Only the granted Pinnacle skill is used. Revalidate this exploit immediately in 0.5.5; if GGG fixes it, the 1.25M Pinnacle acceptance gate and Well of Power interaction disappear.

## Conditional “damage against” package

The legal package remains:

| Source | Condition | Increased damage |
|---|---|---:|
| Farrul's Rune of the Hunt | Rare or Unique enemy | 50% Attack |
| Lockdown | Maimed enemy | 40% |
| Wasting | Enemy affected by an Ailment | 30% hit |
| Harness the Elements | three Elemental Ailments | up to 60% |
| Stormbreaker | relevant Elemental conditions | up to 60% |
| **Total when all are genuinely active** | | **240% increased** |

This is additive increased damage, not a 3.4× multiplicative multiplier. Do not check Freeze, Shock, Ignite, Electrocution, full Armour Break or maximum Scavenged stacks unless the boss test actually sustains them.

## DPS audit and acceptance gate

The raw `27ddb` export contains:

```text
Average hit                  579,026.921
Attack rate                        1.566
Critical chance                  40.2533%
Ice Shot hit DPS             924,891.281
Ice Shot + Ignite            927,373.777
Full combined DPS          1,076,602.468
Mana cost / second             120 / 187.92
```

The 1.076M full number includes Electrocuting Arrow, Culling, Ball Lightning, Bleed and Ignite. It is not Ice Shot DPS.

The 924,891 Ice Shot number is also not a legal final number. The active bow incorrectly receives Bonded +1 Attack level and Bonded 8% extra Physical damage. Removing the +1 level takes Ice Shot from level 26's 507% attack damage to level 25's 475%:

```text
924,891.281 × 475 / 507 = 866,515.5
```

Dividing by 1.08 gives 802,329 as a deliberately pessimistic same-scaling illustration for removal of the Bonded extra Physical line, but that is **not exact** because extra Physical and converted/Elemental portions scale differently. The linked Loreweave also contributes flat Physical damage, while the replacement rare body does not. Conversely, a good rare quiver can beat Cadiro's Gambit.

Atalui replaces Elemental Armament II's 25% more Elemental Attack damage and adds an amount of extra Physical determined by the final Life cost. Without the legal final damage-type split, there is no honest single multiplier for that swap.

For a legal no-Pinnacle Ice Shot value `D0` and Elemental fraction `E`, three-charge Pinnacle gives:

```text
Pinnacle DPS = D0 × (1 + 0.45 × E)
```

The legal no-Pinnacle requirement for 1.25M is:

| Elemental share | Required `D0` |
|---:|---:|
| 60% | 984,252 |
| 65% | 967,118 |
| 70% | 950,570 |

Therefore the rare bow/quiver, Atalui swap and final passive version must produce approximately 951k–984k no-Pinnacle Ice Shot DPS before this build may be called a 1.25M build. The corrected starting region is roughly 802k–867k before all gear/support substitutions, implying approximately 10–23% legal uplift is still required. Barrage is excluded from both figures.

This is the acceptance test, not a promise. If the legal setup does not clear it, do not print 1.25M.

## Loreweave investigation

The Safrin, Eshtera and Zaida resistance lines are different modifier families in the exported data and have no ordinary mutual-exclusion rule. A Loreweave containing all three is therefore not ruled out by modifier-family data.

That does **not** prove the recipe can generate the triple. Loreweave produces three random ring-derived modifiers, can generate modifiers not visibly present on the donated rings, and its server-side selection/weighting algorithm is not published. The community wiki says the result *appears* to guarantee at least one modifier based on a randomly selected donated ring; this is an observation, not an official probability table.

With 20 Safrin, 20 Eshtera and 20 Zaida rings:

- if that observed guarantee is correct, at least one of the three desired families should be guaranteed;
- if donor selection is uniform, the guaranteed family would be approximately one-third Safrin, one-third Eshtera and one-third Zaida;
- the chance of obtaining two or all three desired lines is unknown;
- the old “1 in 34–36” claim had no defensible basis and is withdrawn.

For intuition only, a fictitious model that independently sampled all three output slots from the three equally represented donor families would give a 22.22% triple chance. The real recipe demonstrably is not known to follow that model, so **22.22% is not a craft probability**.

Current official trade searches found no Loreweave with any two of the three target lines and none with the exact triple. Zero listings does not prove impossibility, but it is strong evidence that this is not a buildable acquisition plan.

The 2026-09-01 donor snapshot was approximately:

```text
20 Eshtera at 25 div  = 500.000 div
20 Safrin at 9 div    = 180.000 div
20 Zaida at 0.2302    =   4.604 div
Total before slippage = 684.604 div
```

That consumes about 15% of the listed Eshtera supply before slippage. Loreweave is excluded from the build.

References: [Loreweave mechanics](https://www.poe2wiki.net/wiki/Loreweave), [Safrin's Resolve](https://poe2db.tw/us/Safrins_Resolve), [Eshtera's Path](https://poe2db.tw/us/Eshteras_Path), [Zaida's Longevity](https://poe2db.tw/us/Zaidas_Longevity), [trade search for two or more target lines](https://www.pathofexile.com/trade2/search/poe2/Runes%20of%20Aldur/ky0OlbY3S5), [exact triple search](https://www.pathofexile.com/trade2/search/poe2/Runes%20of%20Aldur/q90O3RpPcg).

## 0.5.5 launch checklist

1. Confirm the disabled-set exploit still leaves Pinnacle usable while nullifying Adonia.
2. Confirm Fox Idol still self-enables and activates Panther in the same body.
3. Confirm Rune of Reach's direct 15% effect works while its Bonded 3% recovery remains off.
4. Confirm Birthright's instant low-Mana recovery is not reduced by Mind over Matter.
5. Confirm Raindancer, Thicket Warding, Well of Power and instant-leech events bypass the less recovery-rate penalty.
6. Test Uhtred's Chalice: instantaneous amount, Overflow, effect duration and Life drain.
7. Record Ice Shot's final Life cost, damage-type split, attack rate and legal no-Pinnacle DPS.
8. Verify every resistance after the -60% Elemental penalty; Chaos receives no such penalty.
9. Verify all six persistent reservations and the optional Alchemist version in-game.
10. Only accept the damage version if it clears the 951k–984k no-Pinnacle gate appropriate to its measured Elemental share.

## Banned / excluded

Flameblast, Frost Wall, Glacial Bolt, High Velocity Rounds, Whirling Slash, Entangle, Tornado Shot, Attrition, Convalescence, Renegade wheel, Eldritch Battery, Wardbound minions, Loreweave dependency, and every modifier/augment on the disabled weapon set.


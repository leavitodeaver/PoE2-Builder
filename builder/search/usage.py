"""Usage requirements (PLAN.md §5.4): skills and supports that can't be used at will.

PoB marks them two ways: the HasUsageCondition skill type (a skill's own, or one a support adds to
the skills it supports: Hit and Run, Combo Finisher, Dauntless, ...) and constant stats naming what
a use needs (`active_skill_requires_X_glory`, `..._required_number_of_combo_stacks`, ...). It reads
them from PoB's gem data (bridge `usageRules`), not from a list of names. PoB itself calculates such
a skill as if it were used at will, so the search allows one only where the build has a real,
sustainable source for what a use needs:
- charges a use needs (Charged Staff, Spell Totem): while the resolver has evidence for that charge. With the keystone
  Ancestral Bond, a totem skill's charges (PoB type SummonsTotem: Spell Totem's 3 Power and/or Endurance Charges,
  Ancestral Warrior Totem's 3 Endurance Charges) need no source (COVC8, A13-1, RESEARCH18 #64, MEDIUM-HIGH): the game
  text (passive_skill_stat_descriptions.csd keystone_ancestral_bond) "No [Charges|Charge] requirement for placing
  [Totem|Totems]"; three 0.5 community sources (Mobalytics, aoeah, the 0.5.0 notes) agree. The four gems with a
  charge requirement are those two, Charged Staff and Wind Serpent's Fury (checked: PoB Data/Skills constantStats
  active_skill_requires_X_*_charges; RESEARCH18: every ActiveSkills description holding "Totem" and "Charge" names
  the two totems only). Whether charges held are still consumed (and Spell Totem's per-charge bonuses apply) is
  UNVERIFIED: the totem model's part (MECH-B)
- Glory a support adds together with its own source (Zerphi's Infamy: 5% of Life spent on skills
  becomes Glory, 50 per use): while the main skill's Life spending pays for UPTIME of its uses, so
  never on a build that spends no Life
- flask charges consumed on use (Ambrosia, Concoct): while the flask's charge generation pays for
  UPTIME of the uses (a boss fight refills flasks only by generation)
- the Concoctions' Mana Flask charges per throw (Requirement kind "flask throw"; flasks.THROW_CHARGES, 5 a throw from
  the game files' flask_throw_base_charges_used, "Consumes {0} Charges from your Mana [Flask]"; PoB's data carries the
  stat on three of the five only, so the names come from flasks.py; COVC7, A8-2): throws a second = the Mana Flask's
  charges a second / the throw's cost, flasks.charge_use_rate's formula (the flask's generation, nothing from kills on a
  lone boss; the stock the fight starts with not counted). The cost (throw_cost; COVC8, RESEARCH16 #82): 5 (HIGH: game
  files, and 0.2.0's "consume 5 Mana Flask charges per use (previously 3)"; the old 3 was 0.1.0e's, reversed), x (1 +
  the build's increased / reduced Flask Charges used, mana-flask ones included) x its "less / more" x (1 - its chance
  for Flasks to not consume Charges) (MEDIUM: a guide's "with 20% reduced charges used, PConc will use 2.4 charges
  instead of 3", poe-vault, 28 Mar 2025; no recorded test; "increased" raises it the same way). Carriers (checked: the
  stats flask_charges_used_+%, flasks_%_chance_to_not_consume_charges, husk_of_dreams_flask_charges_used_-%_final in
  every game table, scratch/covc5/where.py: belt mods (reduced, and two uniques' increased), Abyss belt prefixes, the
  passives flasks5 / flasks18 / flasks21, Husk of Dreams; explosive_ / poisonous_concoction_flask_charges_consumed_+%
  are on no Mod, passive or stat set: unused; PoB's ModCache parses the lines to FlaskChargesUsed INC / MORE and
  FlaskChanceNotConsumeCharges, read by bridge flaskThrowCost). The Mana Flask's own local "charges per use" isn't
  applied (UNVERIFIED whether it changes a throw). Barred where the throw rate is 0 (no Mana
  Flask, or no charge generation); otherwise allowed: as the main skill its throws are capped at that rate by MECH-C's
  Assumptions.flask_main; beside the main skill (a pressed group) MECH-B's press hook (Assumptions.press_caps,
  MECHB5) caps it at the Mana Flask's throws a second (its own Assumptions.throw_rate, at the base 5 charges a throw)
- distance travelled before each use (Requirement kind "travel"; `skill_unusable_until_moved_X_distance` 100: Echoing
  Cry and Hit and Run, the two stat sets that carry it; checked: the stat's columns in every game table (no Mod, passive
  or other stat set holds it) and PoB src (Data/Skills sup_str/sup_dex only, no ModParser line)). The game: "Supported
  Skills become usable after you have moved {0} metres" (gem_stat_descriptions.csd, divide_by_ten: 100 = 10 m; HIGH).
  A play strategy (CLAUDE.md: the player who takes it walks on purpose): walk 10 m between uses. travel_rotation gives
  the rate: walking 10 m takes 10 / (BASE_RUN x the build's movement speed) s, BASE_RUN 3.7 m/s* (MEDIUM: the game's
  Character.ot Pathfinding `base_speed = 37`, HIGH for the number, read directly by RESEARCH21 and through PoB's
  exporter, Data/Misc.lua 244, by RESEARCH20; the unit, tenths of a metre a second, is inferred and agrees with PoE1's
  2016 measured 38 units/s; research character-ot-base-speed.md, base-movement-speed-character-ot.md), so about 2.7 s
  at base; a use then comes every max(its cooldown, use time + walk) s, and the
  walk is time no skill is used (headline: walking only). With dodge rolls (UNVERIFIED that a roll counts as "moved"; a
  with-it figure only): whole rolls of ROLL_METRES (DodgeRollPlayer dodge_roll_base_travel_distance 46, scaled by
  movement speed) of models.DODGE_ROLL_SECONDS each. Blink and other teleports: not counted (UNVERIFIED).
  Usage.travel_rate gives (uses/s, share of PoB's at-will rate, share of the fight spent walking, uses/s with rolls)
  for the group holding it. Allowed (COVC10, on MECH-B's press hook, MECHB5): Assumptions.press_caps holds a pressed
  group to travel_rate's uses and takes the walk out of the main skill's time; Assumptions.main_cap scales a walking
  main skill to its share. Still barred (travel_blocker) only where a group holding it counts in Full DPS without that
  cap (uncapped_groups, as for Combo). Usage.play_note gives the play line (walk 10 m between uses; without / with)
- Combo (Requirement kind "combo"; RULE_AUDIT row 2b): Combo Finisher I needs 3 and II needs 5
  (support_combo_finisher_required_number_of_combo_stacks), Tempest Bell and Mantra of Destruction their own
  (active_skill_required_number_of_combo_stacks). The game (KeywordPopups 'Combo'): "Skills gain Combo when you Strike
  Enemies ... Combo can only be built while using the same weapon as the Combo Skill is bound to"; 'Strike': "a Melee
  attack that directly hits with weapons or body parts. Slam attacks do not count as Strikes". So the build's other
  skills build it: Usage.combo_builders finds them (PoB types Attack and Melee, not Slam, in an enabled group sharing a
  weapon set with the Combo skill). Barred with "no skill of the build Strikes" when there's none. Where there is one,
  combo_rotation gives the real use rate (the player alternates: Combo-per-use Strikes of the fastest builder, then
  one use; uses/s = 1 / (amount / builder speed + 1 / its own speed)). Allowed then (COVC10, on MECH-B's press hook,
  MECHB5): Assumptions.press_caps holds a pressed group to combo_rate (0 with no builder) and Assumptions.main_cap the
  main skill (a DPS "more" at combo_rate's share). Still barred (combo_blocker) where a group holding it counts in
  Full DPS but the hook doesn't cap it (uncapped_groups: neither the main group nor a pressed one, e.g. triggered or
  a totem's), or where combo_rate finds no builder's use rate. Usage.play_note gives the play line ("Strike with X
  until you hold N Combo, then use Y", its rate, and without / with the builder).
  The Invoker's Martial Master (AscendancyMonk1Notable2: can_gain_combo_from_any_attack_hit, "Gain [Combo] from all
  [Attack] [Hits]", and share_combo_across_weapon_sets_and_weapon_types, "Skills can build and retain [Combo] regardless
  of [Weapon Set]": stat_descriptions.csd) makes any Attack of the build a builder, on any weapon set; Martial Adept
  (AscendancyMonk1Notable1_: additional_combo_gain_on_hit, "When you gain [Combo], gain an additional [Combo]") makes
  each gain 2 (COVC7, A11-4; checked: both passives' stats' columns in every game table, scratch/covc5/where.py: those
  two passives only; PoB's ModCache parses none of their lines). The chance-based extra Combo (COVC8, A13-2;
  additional_combo_gain_chance_%, "{0}% chance to build an additional [Combo] on [Hit]", 100 and up "Build an
  additional [Combo] on [Hit]": stat_descriptions.csd; HIGH for the text) adds its chance to each gain: the passives'
  and items' lines (bridge sourceLines; carriers, checked: the stat's columns in every game table, where.py: the
  passives area_attacks45__ / 46 / 49 (5%), unarmed1 (5%), unarmed2 / unarmed6 (10%), unarmed11 Chakra of Rhythm
  (20%), the Mods BerserkInfluenceAdditionalCombo1 / 2 and AbyssModQuarterstaffAmanamuSuffix..., and Practiced Combo's
  support set (40%); PoB's ModCache leaves "N% chance to build an additional Combo on Hit" unparsed) for every
  builder, and Practiced Combo (bridge usageRules comboChance; it supports Strikes, so the builders) for the builder
  in its group; the total chance is capped at 100%. With Martial Adept the two are added (1 + chance + 1 a gain;
  UNVERIFIED whether Adept's extra also doubles the chanced one: the lower figure). UNVERIFIED and left out, all on
  the side of fewer uses: the Combo skill's own Strikes building its next use and Combo from several hits of one use
  (checked: every game-table row holding a Stats id with "combo", scratch/covc5/where.py; RULE_AUDIT row 12 lists the
  same carriers)
- a banner's placed effect (Requirement kind "placed banner", never a blocker; COVC8, COVC7's find). War, Dread and
  Defiance Banner (the three banners; checked: PoB Data/Skills SkillType.Banner, and the game's ActiveSkills ids
  holding "banner": the same three, each with a _reservation twin) are two effects (PoB: WarBannerReservationPlayer etc., HasReservation and Persistent, which "While
  active, Attacking enemies builds Glory", and WarBannerPlayer etc., the placed banner, with HasUsageCondition and
  active_skill_requires_X_glory 200). The Glory gate is the placed banner's only: bridge usageRules lists that effect's
  stats in `placed` and its usage condition as `placedUsageCondition`, so the reserved banner is allowed (offered as a
  Spirit skill) and placing it is a note. No overcount: PoB applies the placed banner's buff (its Armour / Evasion,
  Attack damage / speed, ailment threshold / resistances ... mods, the Valour multiplier, the ModParser "placed banners
  also ..." lines) only under the condition BannerPlanted, which only PoB's config option "Is Banner Planted?" sets
  (ConfigOptions.lua 260), and the tool never sets it (checked: grep BannerPlanted / bannerPlanted / BannerValour in
  PoB src: SkillStatMap, CalcPerform 1243, ConfigOptions 261 and ModParser 5796-5798, each gated by or setting that
  condition; grep in builder/, engine/, ledger/: none). The reserved effect's own stat set holds base_deal_no_damage only,
  so PoB counts the banner at nothing today: the placed buff at its Glory uptime (models.glory_uses) is an UNDERCOUNT
  routed to MECH-C (buffs with uptimes)
- four item / tree skills' own requirements (COVC10; each stat is on that skill's stat set only, checked: the stat's
  columns in every game table, scratch/covc5/where.py; none is in PoB's SkillStatMap or a skill statMap):
  - The Stars Answer (Runeseeker's Call; Requirement kind "mana pool"): mana_required_to_activate_skill 10000, "Cannot
    be cast if you have less than {0} Mana
Cannot be cast if already active" (the_stars_answer.csd; poe2db the same,
    with "Cost: 10000 Mana"). Mana can't exceed its maximum, so barred below 10000 maximum Mana (PoB's Mana); allowed
    at or above it. Its buff's duration is in neither the game's stat sets nor poe2db (UNVERIFIED): PoB counts Starfall
    every 0.3 s (hitTimeOverride, the game's walking_calamity_spawn_meteor_every_ms 300) as if always up, and whether
    the 10000-Mana recast keeps it up is the mechanics lane's (a possible overcount, routed)
  - Runic Reprieve (Requirement kind "ward"): rune_ward_requires_%_maximum_ward_to_perform 20, "Requires at least {0}%
    of maximum [Ward|Runic Ward] to be able to use this Skill" (runic_reprieve.csd). Allowed when the build has any
    Runic Ward (PoB's Ward; it refills between uses: "You cannot recover Runic Ward while this Skill is active" is its
    only pause), barred with none. It deals no damage and PoB applies none of its stats (its block of every Hit while
    channelling is an unmodelled defence: routed to MECH-C)
  - Unbound Avatar (Requirement kind "fury", never a blocker): ailment_bearer_required_stacks_to_use 100 Unbound Fury,
    "Gain [UnboundFury|Unbound Fury] when you inflict an [ElementalAilments|Elemental Ailment] with a [Hit] on an Enemy
    / Maximum 100 Unbound Fury / Cannot gain Unbound Fury while Unbound" (stat_descriptions.csd). PoB applies the
    Unbound buff and every "while Unbound" line only under Condition:Unbound, which only its config box "Are you
    Unbound?" sets, and the tool never sets it (checked: grep Unbound in PoB Modules (ConfigOptions 267, ModParser
    3371-3376) and builder/, engine/, ledger/: none); the skill's own stats are in no statMap. So allowing it counts
    nothing: no overcount. The buff at its uptime (Fury from the build's elemental ailment rate, 8-11.4 s duration,
    12 s cooldown) is an UNDERCOUNT routed to MECH-C (buffs with uptimes)
  - Compose Requiem (The Last Lament; Requirement kind "anguish"): rapidshot_requiem_base_buff_count_requirement 100,
    "Requires {0} anguish to use", accumulated at rapidshot_requiem_number_of_stacks_per_minute 200 ("Accumulates {0}
    anguish per second", per minute: 3.33/s, so 30 s), for rapidshot_requiem_active_duration_ms 12000 ("Requiem lasts for
    {0} seconds"; requiem_ammo.csd). No other stat or source changes either (checked: every Stats id holding requiem /
    anguish, where.py: rapidshot_requiem_buff_count_requirement is on nothing). The Requiem shot is up 12 / (30 + 12) =
    29% of the fight, or 40% if anguish also builds while it's up (UNVERIFIED). PoB's gem (Gems.lua "Requiem") grants
    the shot (CrossbowRequiemPlayer) beside it and counts it fired at will (checked: no Requiem / anguish /
    rapidshot_requiem line in PoB's Modules or SkillStatMap), so it stays barred, labelled with that
    share, until something scales its damage to it (routed to MECH-B with the press hook)
- everything else (Glory a skill builds from its own hits, standing still, recent blocks, Volatility,
  ...): never, since the skill wouldn't be used at the rate PoB calculates

Talismans (Usage.talisman_blockers; the manager's ruling, 12 Nov 2026). The game (KeywordPopups 'Talisman', poe2db's
Talisman page quotes the same): "While you have a Talisman in your active weapon set you will Shapeshift into its
associated form. As a result, Skills that cannot be used in a Shapeshift form cannot be used with Talismans. Triggered
Skills and Persistent Skills will continue to function." Neither PoB nor the search enforced it (checked by COVC3: grep
PoB Modules/*.lua and builder/search for Shapeshift / UsableWhileShapeshifted / Talisman). A skill is allowed in a
Talisman build when it is a form skill (PoB types Shapeshift, Bear, Wolf, Wyvern), UsableWhileShapeshifted,
Persistent (or reserves, HasReservation), Triggered, triggered by a Persistent meta gem in its group, carries the
Talisman exemption stat `usable_with_talisman` (bridge usageRules talismanExempt; a support in its group with it would
exempt it too), or sits in a group the build holds on a weapon set without a Talisman. The game's other two Talisman
stats are not exemptions (AUDIT7 A7-3): `ignore_talisman_inherent_form_restriction` and
`ignore_shapeshift_talisman_requirement` (bridge socketedFormFree; Feral Invocation's support set, Spirit Vessel's skill
and support sets, Deadly Resolve's trigger, and the keystone Chimeral Blood, which the bridge doesn't read) free the
Shapeshift skills socketed in them from the Talisman's form ("visages which attack once with a socketed Shapeshifting
Attack", "a Shapeshifting Spirit Vessel Companion that uses copies of socketed Bear, Werewolf, and Wyvern Skills":
ActiveSkills descriptions; the Talisman "allow[s] you to use Shapeshifting Skills associated with the Talisman's
current form": KeywordPopups 'Talisman'), so they never free a non-Shapeshift skill grouped with them. That meaning is
UNVERIFIED (neither stat has a stat-description line); it frees nothing here today, since form skills are allowed
anyway (assumption 1). (checked: every Stats id holding "talisman" or "shapeshift" and every game-table row holding
the three, scratch/covc5/where.py: usable_with_talisman on 12 skill sets (Blink, Dodge Roll, Tame Beast, Bind Spectre,
Sanguine Revelry, Primal Avatar, Savage Fury, Wolf Pounce's wolf and monster/tutorial copies), no support set; the other two only on the
carriers named above; RULE_AUDIT AUDIT7 A7-3 and A7-8 as the second reading.) ASSUMPTIONS, labelled: (1) a skill of a different form
than the Talisman's (a Bear skill with a Wolf Talisman) stays allowed, since "You can change the Talisman's form at
any time" (UNVERIFIED whether a use changes form freely); (2) a skill socketed in a meta gem that isn't Persistent (Spell
Totem, Mortar Cannon, Ancestral Warrior Totem) follows that meta gem (UNVERIFIED); (3) a reserving skill without the
Persistent type counts as Persistent.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from builder.search.conditions import UPTIME
from builder.search.flasks import THROW_CHARGES, THROW_POOL
from builder.search.models import DODGE_ROLL_SECONDS
from builder.search.objective import number

# Stats that mention a requirement but don't gate a use
IGNORED = re.compile(r"facing_angle|if_shapeshift_required|glory_required_\+%|difficulty_of_meeting|after_meeting|"
                     r"chance_to_not_consume|talisman")  # (Talisman exemptions: talisman_blockers)
REQUIRED = re.compile(r"require|cannot_be_used_unless")
CHARGES = re.compile(r"requires_X_(frenzy|power|endurance|power_or_endurance)_charges$")
CHARGE_VARS = {"frenzy": ("useFrenzyCharges",), "power": ("usePowerCharges",), "endurance": ("useEnduranceCharges",),
               "power_or_endurance": ("usePowerCharges", "useEnduranceCharges")}
GLORY = "active_skill_requires_X_glory"
GLORY_FROM_LIFE = "active_skill_generates_%_of_life_spent_as_glory"
FLASK = re.compile(r"consume_%_of_maximum_(life|mana)_flask_charges_on_skill_use$")
# The Concoctions' flat flask cost: flask_throw_base_charges_used 5 (Mana Flask charges a throw; Shattering, Acidic,
# Fulminating, Explosive, Bleeding Concoction; checked: game GrantedEffectStatSets and PoB c.py, AUDIT6 A6-5/A6-14).
# The requirement comes from flasks.THROW_CHARGES by name (module docstring), so the stat itself is skipped here.
FLASK_THROW = "flask_throw_base_charges_used"
# Combo a use needs (module docstring)
COMBO = re.compile(r"required_number_of_combo_stacks$")
# Distance travelled before each use (module docstring)
MOVED = "skill_unusable_until_moved_X_distance"
UNITS_PER_METRE = 10  # (the stat text's divide_by_ten; HIGH)
BASE_RUN = 3.7  # metres a second at 0% increased movement speed (MEDIUM: Character.ot base_speed 37, unit inferred; module docstring)
ROLL_METRES = 4.6  # a dodge roll's distance (DodgeRollPlayer dodge_roll_base_travel_distance 46)
# "N% chance to build an additional Combo on Hit" (module docstring; A13-2)
COMBO_CHANCE = re.compile(r"^(?:(\d+(?:\.\d+)?)% chance to build|build) an additional combo on hit$", re.I)
ANCESTRAL_BOND = "Ancestral Bond"  # the keystone (module docstring: totems' charges)
# The Invoker's Combo passives (module docstring)
MARTIAL_MASTER = "Martial Master"
MARTIAL_ADEPT = "Martial Adept"
# Build-up and pool requirements of four item / tree skills (module docstring: COVC10)
MANA_POOL = "mana_required_to_activate_skill"  # The Stars Answer: 10000
WARD_SHARE = "rune_ward_requires_%_maximum_ward_to_perform"  # Runic Reprieve: 20
FURY = "ailment_bearer_required_stacks_to_use"  # Unbound Avatar: 100 Unbound Fury
ANGUISH = "rapidshot_requiem_base_buff_count_requirement"  # Compose Requiem: 100 anguish
ANGUISH_PER_MINUTE = "rapidshot_requiem_number_of_stacks_per_minute"  # 200 a minute
REQUIEM_MS = "rapidshot_requiem_active_duration_ms"  # 12000
FIELDS = ["Speed", "LifePerSecondCost", "Mana", "Ward"]
TALISMAN = "Talisman"
# PoB skill types a Talisman doesn't shut out (module docstring)
FORM_FREE = {"Shapeshift", "Bear", "Wolf", "Wyvern", "UsableWhileShapeshifted", "Persistent", "HasReservation",
             "Triggered"}

_RULES: dict[str, dict] | None = None  # PoB's gem data doesn't change within a process


@dataclass(frozen=True)
class Requirement:
    kind: str  # "charges", "glory from life", "flask", "combo", "travel", "mana pool", "ward", "anguish", "placed
    # banner" or "fury" (never block), or "never"
    label: str  # what a use needs, for notes and tests
    amount: float = 0.0
    rate: float = 0.0  # Glory per Life spent (fraction), or the share of flask charges a use takes
    pool: str = ""  # "Life" or "Mana" (flask)
    vars: tuple[str, ...] = ()  # resolver conditions that supply it (charges)


def requirements(name: str, rule: dict) -> list[Requirement]:
    """What a use of this gem needs, from its bridge `usageRules` entry."""
    stats = rule.get("stats") or {}
    placed = set(rule.get("placed") or [])  # (a banner's placed effect: module docstring)
    found = []
    for stat, value in stats.items():
        if IGNORED.search(stat) or stat == FLASK_THROW:
            continue
        if stat in placed:
            if REQUIRED.search(stat):
                found.append(Requirement("placed banner", f"placing the banner: {stat.replace('_', ' ')} "
                                         f"{number(value):g} (not counted: PoB's BannerPlanted is never set)",
                                         number(value)))
            continue
        if m := CHARGES.search(stat):
            kind = m.group(1)
            found.append(Requirement("charges", f"{number(value):g} {kind.replace('_', ' or ')} charges per use",
                                     number(value), vars=CHARGE_VARS[kind]))
        elif stat == GLORY and GLORY_FROM_LIFE in stats:
            rate = number(stats[GLORY_FROM_LIFE]) / 100
            found.append(Requirement("glory from life", f"{number(value):g} Glory per use, built from "
                                     f"{rate:.0%} of Life spent on skills", number(value), rate))
        elif m := FLASK.search(stat):
            pool = m.group(1).capitalize()
            found.append(Requirement("flask", f"{number(value):g}% of the {pool} Flask's charges per use",
                                     number(value), number(value) / 100, pool))
        elif COMBO.search(stat):
            found.append(Requirement("combo", f"{number(value):g} Combo per use", number(value)))
        elif stat == MOVED:
            found.append(Requirement("travel", f"move {number(value) / UNITS_PER_METRE:g} m before each use",
                                     number(value)))
        elif stat == MANA_POOL:
            found.append(Requirement("mana pool", f"at least {number(value):g} Mana to cast (and its cost)", number(value),
                                     pool="Mana"))
        elif stat == WARD_SHARE:
            found.append(Requirement("ward", f"at least {number(value):g}% of maximum Runic Ward to use", number(value),
                                     pool="Ward"))
        elif stat == FURY:
            found.append(Requirement("fury", f"{number(value):g} Unbound Fury, gained by inflicting Elemental Ailments "
                                     "with Hits (not counted: PoB's Unbound condition is never set)", number(value)))
        elif stat == ANGUISH:
            per_second = number(stats.get(ANGUISH_PER_MINUTE)) / 60
            fill = number(value) / per_second if per_second > 0 else math.inf
            active = number(stats.get(REQUIEM_MS)) / 1000
            share = (active / (fill + active), active / fill if fill > active else 1.0) if math.isfinite(fill) else (0, 0)
            found.append(Requirement("anguish", f"{number(value):g} anguish, accumulated at {per_second:.2f} a second "
                                     f"({fill:.0f} s), for a {active:g} s Requiem: up {share[0]:.0%}-{share[1]:.0%} of "
                                     "the fight", number(value), share[0]))
        elif REQUIRED.search(stat):
            found.append(Requirement("never", f"{stat.replace('_', ' ')} {number(value):g}", number(value)))
    if (rule.get("usageCondition") or rule.get("addsUsageCondition")) and not any(r.kind != "placed banner"
                                                                                for r in found):
        found.append(Requirement("never", "a usage condition PoB doesn't model (e.g. standing still, recent blocks)"))
    if name in THROW_CHARGES:
        found.append(Requirement("flask throw", f"{THROW_CHARGES[name]:g} {THROW_POOL} Flask charges per throw (before "
                                 "the build's Flask Charges used modifiers, MEDIUM)", THROW_CHARGES[name], pool=THROW_POOL))
    return found


def usage_rules(build) -> dict[str, dict]:
    """The bridge's `usageRules` for every gem, read once."""
    global _RULES
    if _RULES is None:
        _RULES = build.call("usageRules")["gems"]
    return _RULES


class Usage:
    def __init__(self, build, conditions, flask_rate: Callable[[str], tuple[float, float]] | None = None,
                 weapons: dict[int, str | None] | None = None) -> None:
        """`flask_rate(pool)`: (charges generated per second, maximum charges) of that flask. `weapons`: the main-hand
        item type of each weapon set the build has ({1: ..., 2: ...}; set 2 only with weapon swap), for Talismans."""
        self.build = build
        self.conditions = conditions
        self.flask_rate = flask_rate
        self.weapons = weapons or {}

    @property
    def rules(self) -> dict[str, dict]:
        return usage_rules(self.build)

    def blockers(self, name: str, stats: dict | None = None) -> list[str]:
        """Why this gem can't be used at the rate PoB calculates in the build as it is ([] if it can)."""
        talisman = self.talisman_blockers(name)
        rule = self.rules.get(name)
        if not rule and name not in THROW_CHARGES:
            return talisman
        needs = requirements(name, rule or {})
        if not needs:
            return talisman
        if stats is None and any(r.kind in ("glory from life", "flask", "mana pool", "ward") for r in needs):
            stats = self.build.call("stats", fields=FIELDS)
        blocked = []
        for need in needs:
            if need.kind in ("placed banner", "fury"):
                continue  # (the reserved banner is used at will, Unbound Avatar's buff isn't counted: module docstring)
            if need.kind == "anguish":
                blocked.append(f"{name}: {need.label}, but PoB counts the Requiem shot fired at will, and the tool "
                               "doesn't yet scale it to that share (routed to the mechanics lane)")
                continue
            if need.kind == "combo":
                if reason := self.combo_blocker(name, need):
                    blocked.append(f"{name}: {need.label}, {reason}")
            elif need.kind == "flask throw":
                # (the main skill's throws are rated by Assumptions.flask_main, once the gear is on; a pressed one is
                # capped by the press hook, Assumptions.press_caps: barred here only when no throw is ever paid for)
                if not self.is_main(name) and self.throw_rate(need) <= 0:
                    blocked.append(f"{name}: {need.label}, and the {need.pool} Flask gains no charges in a boss fight "
                                   "(none equipped, or no charges a second without kills)")
            elif need.kind == "travel":
                if reason := self.travel_blocker(name, need):
                    blocked.append(f"{name}: {need.label} ({travel_seconds(need.amount):.1f} s of walking at a base "
                                   f"{BASE_RUN:g} m/s*, MEDIUM; less with movement speed), {reason}")
            elif need.kind == "charges" and self.totem_bond(name):
                continue  # (Ancestral Bond: no charges needed to place a totem)
            elif not self.sustained(need, stats or {}):
                blocked.append(f"{name}: {need.label}")
        return talisman + blocked

    def combo_groups(self, name: str) -> list[dict]:
        """The enabled groups holding `name` (as a skill or a support)."""
        return [g for g in self.build.groups() if g["enabled"] and any(x["name"] == name for x in g["gems"])]

    def combo_builders(self, name: str) -> list[tuple[int | None, str]]:
        """(group index, skill) of the build's skills that build `name`'s Combo (module docstring): Strikes in another
        enabled group sharing a weapon set with it (any set when `name` isn't in the build yet)."""
        mine = self.combo_groups(name)
        master = self.has_passive(MARTIAL_MASTER)  # (any Attack Hit, any weapon set)
        sets = {s for g in mine for s in (1, 2) if g.get(f"set{s}", True)} if mine and not master else {1, 2}
        found = []
        for group in self.build.groups():
            if not group["enabled"] or group in mine or not any(group.get(f"set{s}", True) for s in sets):
                continue
            for gem in group["gems"]:
                types = set(self.gem(gem["name"]).get("skillTypes") or [])
                builds = "Attack" in types if master else {"Attack", "Melee"} <= types and "Slam" not in types
                if not gem["support"] and builds:
                    found.append((group.get("index"), gem["name"]))
        return found

    def has_passive(self, name: str) -> bool:
        return any(p["name"] == name for p in self.build.call("facts", fields=[]).get("passives") or [])

    def totem_bond(self, name: str) -> bool:
        """Does Ancestral Bond lift this totem skill's charge requirement (module docstring)?"""
        return self.has_passive(ANCESTRAL_BOND) and "SummonsTotem" in (self.gem(name).get("skillTypes") or [])

    def combo_blocker(self, name: str, need: Requirement) -> str:
        """Why the Combo gem `name` is barred, or "" (module docstring, the press hook): barred with no builder in the
        build, or where a group holding it counts in Full DPS without a press cap (uncapped_groups)."""
        builders = self.combo_builders(name)
        if not builders:
            return "and no skill of the build Strikes to build it (Combo comes from Strikes on the same weapon set)"
        if self.combo_groups(name) and self.combo_rate(name, need.amount) is None:
            return "and no builder's Strikes reach it (no group of the build Strikes at a use rate PoB gives)"
        if uncapped := self.uncapped_groups(name):
            return (f"and its group {uncapped[0]} counts in Full DPS without a press cap (neither the main skill nor a "
                    "pressed skill: PoB would count it used at will)")
        return ""

    def travel_blocker(self, name: str, need: Requirement) -> str:
        """Why the walking gem `name` is barred, or "" (module docstring, the press hook): only where a group holding it
        counts in Full DPS without a press cap (uncapped_groups). Walking needs nothing from the build."""
        if uncapped := self.uncapped_groups(name):
            return (f"and its group {uncapped[0]} counts in Full DPS without a press cap (neither the main skill nor a "
                    "pressed skill: PoB would count it used at will)")
        return ""

    def uncapped_groups(self, name: str) -> list[str]:
        """Labels of the enabled groups holding `name` that count in PoB's Full DPS but that the press hook doesn't cap:
        neither the main group (Assumptions.main_cap) nor a pressed group (assumptions.pressed_group, capped by
        Assumptions.press_caps). A triggered, totem, Invocation, Mark-carrier or item-granted group is used at its own
        rate, which the hook doesn't hold to Combo or walking (module docstring)."""
        from builder.search.assumptions import pressed_group

        main = self.build.main_group() if hasattr(self.build, "main_group") else None
        gems = getattr(getattr(self.build, "ctx", None), "gems", None) or {}
        found = []
        for group in self.combo_groups(name):
            if not group.get("fullDPS") or (main is not None and group.get("index") == main.get("index")):
                continue
            if pressed_group({"fromItem": False, **group, "gems": [{"enabled": True, **g} for g in group["gems"]]}, gems):
                continue
            found.append(group.get("label") or next((g["name"] for g in group["gems"] if not g["support"]), "?"))
        return found

    def play_note(self, name: str) -> str:
        """What the player does to feed the Combo or walking gem `name` in the build as it is, with its rate and the
        "without it / with it" figures (CLAUDE.md, the play guide's rows; "" when it has no such need or no rate). The
        rate is the press hook's (Assumptions.press_caps / main_cap); this is its plain-English line."""
        rule = self.rules.get(name)
        for need in requirements(name, rule) if rule else []:
            if need.kind == "combo" and (found := self.combo_rate(name, need.amount)):
                uses, share, builder = found
                return (f"{name}: Strike with {builder} until you hold {need.amount:g} Combo, then use it: about "
                        f"{uses:.2f} uses a second, x{share:.3f} of PoB's at-will damage (without a {builder} between "
                        "uses: none; with it: that share)")
            if need.kind == "travel" and (found := self.travel_rate(name)):
                uses, share, walking, rolled = found
                return (f"{name}: walk 10 m between uses ({walking:.0%} of the fight spent walking, no skill used then): "
                        f"about {uses:.2f} uses a second, x{share:.3f} of PoB's at-will damage (standing still: none; "
                        f"with dodge rolls counted as moving, UNVERIFIED: {rolled:.2f} a second)")
        return ""

    def combo_rate(self, name: str, amount: float) -> tuple[float, float, str] | None:
        """(uses per second, share of PoB's at-will damage, builder) of the Combo skill in `name`'s group, built by the
        fastest builder (combo_rotation), or None without a builder or a group holding `name`. For the hook that will
        scale its Full DPS (module docstring)."""
        builders = [b for b in self.combo_builders(name) if b[0] is not None]
        mine = self.combo_groups(name)
        if not builders or not mine or mine[0].get("index") is None:
            return None
        rates = self.build.call("useRates", indices=sorted({mine[0]["index"]} | {i for i, _ in builders}))["rates"]
        own = next((number(r.get("speed")) for r in rates if r["index"] == mine[0]["index"]), 0.0)
        builder = max((r for r in rates if r["index"] != mine[0]["index"]), key=lambda r: number(r.get("speed")),
                      default=None)
        if builder is None or own <= 0 or number(builder.get("speed")) <= 0:
            return None
        adept = 1.0 if self.has_passive(MARTIAL_ADEPT) else 0.0
        chance = self.combo_chance()
        gain = lambda r: 1.0 + adept + min(chance + self.group_combo_chance(r["index"]), 100.0) / 100
        builder = max((r for r in rates if r["index"] != mine[0]["index"] and number(r.get("speed")) > 0),
                      key=lambda r: number(r.get("speed")) * gain(r), default=builder)
        uses, share = combo_rotation(amount, number(builder["speed"]), own, gain(builder))
        return uses, share, builder["name"]

    def combo_chance(self) -> float:
        """% chance to build an additional Combo on Hit from the build's passives and items (module docstring)."""
        total = 0.0
        for line in self.build.call("sourceLines").get("lines") or []:
            if str(line.get("source", "")).startswith(("passive:", "item:")):
                m = COMBO_CHANCE.match(str(line.get("text", "")).strip())
                if m:
                    total += float(m.group(1)) if m.group(1) else 100.0
        return total

    def group_combo_chance(self, index: int | None) -> float:
        """% chance to build an additional Combo from the supports in group `index` (Practiced Combo)."""
        group = next((g for g in self.build.groups() if g.get("index") == index), None)
        return sum(number((self.rules.get(g["name"]) or {}).get("comboChance")) for g in (group or {}).get("gems", [])
                   if g.get("enabled", True))

    def travel_rate(self, name: str) -> tuple[float, float, float, float] | None:
        """(uses per second, share of PoB's at-will damage, share of the fight spent walking, uses per second if dodge
        rolls count) of the skill in the first enabled group holding `name` with a travel requirement (module
        docstring), at the build's movement speed; None when `name` has none or isn't in an enabled group. For the
        hook that will scale its Full DPS and the main skill's time (module docstring)."""
        rule = self.rules.get(name)
        need = next((r for r in requirements(name, rule) if r.kind == "travel"), None) if rule else None
        mine = self.combo_groups(name)
        if need is None or not mine or mine[0].get("index") is None:
            return None
        rate = next(iter(self.build.call("useRates", indices=[mine[0]["index"]])["rates"]), None) or {}
        speed = number(rate.get("speed"))
        if speed <= 0:
            return None
        movement = number(self.build.call("stats", fields=["EffectiveMovementSpeedMod"]).get(
            "EffectiveMovementSpeedMod")) or 1.0
        cooldown = number(rate.get("cooldown"))
        uses, share, walking = travel_rotation(need.amount, movement, speed, cooldown)
        rolled = travel_rotation(need.amount, movement, speed, cooldown, rolls=True)[0]
        return uses, share, walking, rolled

    def gem(self, name: str) -> dict:
        ctx = self.build.ctx
        return (getattr(ctx, "all_gems", None) or {}).get(name) or ctx.gems.get(name) or {}

    def form_free(self, name: str) -> bool:
        """Can this skill be used while a Talisman Shapeshifts the player (module docstring)?"""
        return bool(FORM_FREE & set(self.gem(name).get("skillTypes") or []))             or bool((self.rules.get(name) or {}).get("talismanExempt"))

    def talisman_blockers(self, name: str) -> list[str]:
        """The Talisman rule: a skill that can't be used in a Shapeshift form can't be used from a weapon set holding a
        Talisman. Blocked when every group of the build that uses it is held only on Talisman sets with nothing that
        frees it (a gem not in the build yet would go in a new group, on every set)."""
        if TALISMAN not in self.weapons.values():
            return []
        gem = self.gem(name)
        if not gem or gem.get("support") or self.form_free(name):
            return []
        groups = [g for g in self.build.groups()
                  if g["enabled"] and any(x["name"] == name and not x["support"] for x in g["gems"])]
        freed = any(self.group_frees(g, name) for g in groups) if groups             else any(w != TALISMAN for w in self.weapons.values())
        return [] if freed else [f"{name}: can't be used in a Shapeshift form, so not with a Talisman (the game's Talisman rule)"]

    def group_frees(self, group: dict, name: str) -> bool:
        """This group lets `name` be used despite a Talisman: a set without one, a Persistent (trigger) meta gem, or an
        exempting support."""
        if any(w != TALISMAN for s, w in self.weapons.items() if group.get(f"set{s}", True)):
            return True
        for other in group["gems"]:
            if other["name"] == name:
                continue
            types = set(self.gem(other["name"]).get("skillTypes") or [])
            if not other["support"] and "Meta" in types and self.form_free(other["name"]):
                return True
            # (usable_with_talisman only; socketedFormFree frees socketed Shapeshift skills, which are form-free anyway)
            if other["support"] and (self.rules.get(other["name"]) or {}).get("talismanExempt"):
                return True
        return False

    def usable(self, name: str, stats: dict | None = None) -> bool:
        return not self.blockers(name, stats)

    def sustained(self, need: Requirement, stats: dict) -> bool:
        speed = number(stats.get("Speed"))
        if need.kind == "charges":
            return any(var in self.conditions.active for var in need.vars)
        if need.kind == "glory from life":
            return speed > 0 and need.rate * number(stats.get("LifePerSecondCost")) >= need.amount * speed * UPTIME
        if need.kind == "flask":
            if self.flask_rate is None:
                return False
            generated, maximum = self.flask_rate(need.pool)
            return speed > 0 and maximum > 0 and generated >= need.rate * maximum * speed * UPTIME
        if need.kind == "flask throw":
            return self.throw_rate(need) > 0
        if need.kind == "mana pool":  # (Mana can't exceed its maximum: below it, never castable)
            return number(stats.get("Mana")) >= need.amount
        if need.kind == "ward":  # (any Runic Ward refills to the share between uses; module docstring)
            return number(stats.get("Ward")) > 0
        return False

    def is_main(self, name: str) -> bool:
        """Is `name` the skeleton's main skill (Conditions.main_skill) or the main group's first active skill?"""
        if name == getattr(self.conditions, "main_skill", None):
            return True
        group = self.build.main_group() if hasattr(self.build, "main_group") else None
        return bool(group) and next((g["name"] for g in group["gems"] if not g["support"]), None) == name

    def throw_cost(self, need: Requirement) -> float:
        """Mana Flask charges a throw takes on average with the build's modifiers (throw_cost, module docstring)."""
        mods = self.build.call("flaskThrowCost")
        return throw_cost(need.amount, number(mods.get("usedInc")), number(mods.get("usedMore", 1)),
                          number(mods.get("notConsume")))

    def throw_rate(self, need: Requirement) -> float:
        """Throws a second the flask's own charge generation pays for (flasks.charge_use_rate's formula: charges a
        second / charges a throw, the cost with the build's modifiers), 0 without the flask or a flask_rate, inf when
        no throw consumes charges. For the main skill's cap and the press hook."""
        if self.flask_rate is None or need.amount <= 0:
            return 0.0
        gained = self.flask_rate(need.pool)[0]
        if gained <= 0:
            return 0.0
        cost = self.throw_cost(need)
        return gained / cost if cost > 0 else math.inf


def throw_cost(base: float, used_inc: float = 0.0, used_more: float = 1.0, not_consume: float = 0.0) -> float:
    """Average flask charges a Concoction throw takes (module docstring; MEDIUM): `base` (5) x (1 + increased / reduced
    Flask Charges used %/100) x their more / less multiplier x (1 - chance to not consume %/100), never below 0."""
    return max(0.0, base * (1 + used_inc / 100) * used_more * (1 - min(max(not_consume, 0.0), 100.0) / 100))


def combo_rotation(amount: float, builder_speed: float, speed: float, gain: float = 1.0) -> tuple[float, float]:
    """(uses per second, share of the at-will rate) of a skill needing `amount` Combo per use, when the player
    alternates Strikes of a builder (`builder_speed` uses/s, `gain` Combo each) with one use of it (`speed` uses/s at
    will): each cycle takes amount / (gain x builder_speed) + 1 / speed seconds."""
    if amount <= 0:
        return speed, 1.0
    cycle = amount / (gain * builder_speed) + 1.0 / speed
    uses = 1.0 / cycle
    return uses, uses / speed


def travel_seconds(distance: float, movement: float = 1.0, rolls: bool = False) -> float:
    """Seconds to cover `distance` units (10 a metre) at `movement` (PoB's EffectiveMovementSpeedMod: 1 + increased
    movement speed, with its penalties), walking at BASE_RUN (MEDIUM), or by whole dodge rolls (UNVERIFIED that they count)."""
    metres = distance / UNITS_PER_METRE
    if rolls:
        return math.ceil(metres / (ROLL_METRES * movement) - 1e-9) * DODGE_ROLL_SECONDS
    return metres / (BASE_RUN * movement)


def travel_rotation(distance: float, movement: float, speed: float, cooldown: float = 0.0,
                    rolls: bool = False) -> tuple[float, float, float]:
    """(uses per second, share of the at-will rate, share of the fight spent moving) of a skill usable once per
    `distance` units moved: a use takes 1 / `speed` s, then the player moves (travel_seconds), and the next use waits
    for its cooldown too. The time moving is time no skill is used (the headline: no skill used while walking)."""
    if speed <= 0:
        return 0.0, 0.0, 0.0
    moving = travel_seconds(distance, movement, rolls)
    uses = 1.0 / max(cooldown, 1.0 / speed + moving)
    return uses, min(uses / speed, 1.0), min(moving * uses, 1.0)

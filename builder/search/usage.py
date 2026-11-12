"""Usage requirements (PLAN.md §5.4): skills and supports that can't be used at will.

PoB marks them two ways: the HasUsageCondition skill type (a skill's own, or one a support adds to
the skills it supports: Hit and Run, Combo Finisher, Dauntless, ...) and constant stats naming what
a use needs (`active_skill_requires_X_glory`, `..._required_number_of_combo_stacks`, ...). It reads
them from PoB's gem data (bridge `usageRules`), not from a list of names. PoB itself calculates such
a skill as if it were used at will, so the search allows one only where the build has a real,
sustainable source for what a use needs:
- charges a use needs (Charged Staff, Spell Totem): while the resolver has evidence for that charge
- Glory a support adds together with its own source (Zerphi's Infamy: 5% of Life spent on skills
  becomes Glory, 50 per use): while the main skill's Life spending pays for UPTIME of its uses, so
  never on a build that spends no Life
- flask charges consumed on use (Ambrosia, Concoct): while the flask's charge generation pays for
  UPTIME of the uses (a boss fight refills flasks only by generation)
- distance travelled before each use (`skill_unusable_until_moved_X_distance` 100: Echoing Cry and Hit and Run, the
  two stat sets that carry it; checked: the stat's columns in every game table (no Mod, passive or other stat set
  holds it) and PoB src (Data/Skills sup_str/sup_dex only, no ModParser line)): never yet,
  labelled. The rate is the build's (how fast and how much it moves between uses), but the tool's boss fight has no
  movement model and the game data gives no base movement speed in distance units to turn the build's movement speed
  into uses a second (UNVERIFIED; for RESEARCH). Zero, not a guessed rate (CLAUDE.md: no source means 0, with a note);
  this is an undercount of those two supports, never an overcount (AUDIT6 A6-2)
- Combo (Requirement kind "combo"; RULE_AUDIT row 2b): Combo Finisher I needs 3 and II needs 5
  (support_combo_finisher_required_number_of_combo_stacks), Tempest Bell and Mantra of Destruction their own
  (active_skill_required_number_of_combo_stacks). The game (KeywordPopups 'Combo'): "Skills gain Combo when you Strike
  Enemies ... Combo can only be built while using the same weapon as the Combo Skill is bound to"; 'Strike': "a Melee
  attack that directly hits with weapons or body parts. Slam attacks do not count as Strikes". So the build's other
  skills build it: Usage.combo_builders finds them (PoB types Attack and Melee, not Slam, in an enabled group sharing a
  weapon set with the Combo skill). Barred with "no skill of the build Strikes" when there's none. Where there is one,
  combo_rotation gives the real use rate (the player alternates: Combo-per-use Strikes of the fastest builder, then
  one use; uses/s = 1 / (amount / builder speed + 1 / its own speed)); it's still barred, labelled with that rate,
  because nothing yet scales the skill's Full DPS to it (PoB counts it used at will: an overcount if allowed as it
  is). The hook it needs (scale the Combo skill's Full DPS entry by combo_rotation's share, as models.press_model does
  for presses, with a play row "Strike with X N times, then use Y") is routed to the mechanics lane (COVC6's report).
  UNVERIFIED and left out, all on the side of fewer uses: the Combo skill's own Strikes building its next use, Combo
  from several hits of one use, extra Combo gain (additional_combo_gain_chance_%: Practiced Combo, 7 passives, 3 mods;
  Martial Adept's additional_combo_gain_on_hit) and Martial Master's Combo from any Attack Hit (can_gain_combo_from_any
  _attack_hit) (checked: every game-table row holding a Stats id with "combo", scratch/covc5/where.py; RULE_AUDIT row 12
  lists the same carriers)
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

import re
from collections.abc import Callable
from dataclasses import dataclass

from builder.search.conditions import UPTIME
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
# Fulminating, Explosive, Bleeding Concoction; checked: game GrantedEffectStatSets and PoB c.py, AUDIT6 A6-5/A6-14). Read here but left unacted on, by the manager's ruling (12 Nov 2026):
# PoB throws them at will, an OPEN OVERCOUNT, until MECH-C's rate function (charges gained a second / 5 caps the throws)
# is on main and wired in (COVC2's report). Kept out of `needs` so the Concoctions' numbers don't change before then.
FLASK_THROW = "flask_throw_base_charges_used"
# Combo a use needs (module docstring)
COMBO = re.compile(r"required_number_of_combo_stacks$")
# Distance travelled before each use (module docstring)
MOVED = "skill_unusable_until_moved_X_distance"
FIELDS = ["Speed", "LifePerSecondCost"]
TALISMAN = "Talisman"
# PoB skill types a Talisman doesn't shut out (module docstring)
FORM_FREE = {"Shapeshift", "Bear", "Wolf", "Wyvern", "UsableWhileShapeshifted", "Persistent", "HasReservation",
             "Triggered"}

_RULES: dict[str, dict] | None = None  # PoB's gem data doesn't change within a process


@dataclass(frozen=True)
class Requirement:
    kind: str  # "charges", "glory from life", "flask", "combo" or "never"
    label: str  # what a use needs, for notes and tests
    amount: float = 0.0
    rate: float = 0.0  # Glory per Life spent (fraction), or the share of flask charges a use takes
    pool: str = ""  # "Life" or "Mana" (flask)
    vars: tuple[str, ...] = ()  # resolver conditions that supply it (charges)


def requirements(name: str, rule: dict) -> list[Requirement]:
    """What a use of this gem needs, from its bridge `usageRules` entry."""
    stats = rule.get("stats") or {}
    found = []
    for stat, value in stats.items():
        if IGNORED.search(stat) or stat == FLASK_THROW:
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
            found.append(Requirement("never", f"travel {number(value):g} units before each use (the tool has no "
                                     "movement model for the boss fight)", number(value)))
        elif REQUIRED.search(stat):
            found.append(Requirement("never", f"{stat.replace('_', ' ')} {number(value):g}", number(value)))
    if (rule.get("usageCondition") or rule.get("addsUsageCondition")) and not found:
        found.append(Requirement("never", "a usage condition PoB doesn't model (e.g. standing still, recent blocks)"))
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
        if not rule:
            return talisman
        needs = requirements(name, rule)
        if not needs:
            return talisman
        if stats is None and any(r.kind in ("glory from life", "flask") for r in needs):
            stats = self.build.call("stats", fields=FIELDS)
        blocked = []
        for need in needs:
            if need.kind == "combo":
                blocked.append(f"{name}: {need.label}, {self.combo_reason(name, need.amount)}")
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
        sets = {s for g in mine for s in (1, 2) if g.get(f"set{s}", True)} if mine else {1, 2}
        found = []
        for group in self.build.groups():
            if not group["enabled"] or group in mine or not any(group.get(f"set{s}", True) for s in sets):
                continue
            for gem in group["gems"]:
                types = set(self.gem(gem["name"]).get("skillTypes") or [])
                if not gem["support"] and {"Attack", "Melee"} <= types and "Slam" not in types:
                    found.append((group.get("index"), gem["name"]))
        return found

    def combo_reason(self, name: str, amount: float) -> str:
        builders = self.combo_builders(name)
        if not builders:
            return "and no skill of the build Strikes to build it (Combo comes from Strikes on the same weapon set)"
        return (f"built by {', '.join(sorted({b for _, b in builders}))} (Strikes), but the tool doesn't yet scale its "
                "damage to the use rate that Combo allows (Usage.combo_rate; PoB counts it used at will)")

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
        uses, share = combo_rotation(amount, number(builder["speed"]), own)
        return uses, share, builder["name"]

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
        return False


def combo_rotation(amount: float, builder_speed: float, speed: float, gain: float = 1.0) -> tuple[float, float]:
    """(uses per second, share of the at-will rate) of a skill needing `amount` Combo per use, when the player
    alternates Strikes of a builder (`builder_speed` uses/s, `gain` Combo each) with one use of it (`speed` uses/s at
    will): each cycle takes amount / (gain x builder_speed) + 1 / speed seconds."""
    if amount <= 0:
        return speed, 1.0
    cycle = amount / (gain * builder_speed) + 1.0 / speed
    uses = 1.0 / cycle
    return uses, uses / speed

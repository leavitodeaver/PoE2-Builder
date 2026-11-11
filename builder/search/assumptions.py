"""Assumptions for mechanics PoB doesn't calculate, so every skill can be optimized (PLAN.md §6:
the ledger's "inject a modifier" effect, ahead of the full ledger in Phase 4).

Each assumption is computed from the build itself and handed to PoB as a modifier, so it stacks
with everything else; the result lists it with an asterisk, its basis and its confidence.

First entry, Explosive Transmutation: PoB has no trigger for its "Transmutation Explosion"
(it fires when you collect a Remnant), so the explosion gets a trigger rate:
    min(Remnants per second from the build's sources × (1 + additional-Remnant chance),
        the explosion's cooldown cap including cooldown recovery)
On a single boss, Remnants come only from sources that create them without kills; their rates are
the user's audit (legacy/Custom builds/Explosive Transmutation - Shaman vs Acolyte.md, 0.5.4).

The custom models (builder/search/models.py) add Elemental Archon, meta-skill Energy, Elemental
Expression, Iron Ward's nova and the Traveller's Wisdom and Darkness notes. Skills they trigger besides the main
skill (spells in a meta gem, Elemental Expression) count through Full DPS: their groups are
included in it and the objective switches to Full DPS while any exist.
"""

from __future__ import annotations

import math
import os
import re

from builder.ledger import conditions
from builder.search import models
from builder.search.conditions import INFUSED_SET, curse_uses, natural_distance
from builder.search.usage import usage_rules
from builder.search.state import Build

# Remnants per second on a single boss, at gem level 20, while the stated condition holds. Rates are the game files'
# level-20 stats (build 26476662, GrantedEffectStatSetsPerLevel; scratch search of 8 Nov, MECH18): Into the Breach's and
# Wildwood's Gifts' breach_flame_spawn_rate_ms 1210, Life Remnants' on-hit 3000 ms, Mana Remnants' and Sanguine
# Revelry's 2000 ms, Snap's 4 s and Frost Bomb's 6 s cooldowns, Orb of Storms' 12 s duration, Lingering Illusion's
# 76% chance of an additional Remnant, Remnants of Kalguur's 50% on Stun. The kill-only sources (Rip, Runic Extraction)
# make none on a lone boss. Where the files give no cadence on a boss, the rate is a labelled assumption.
REMNANT_SOURCES = {
    "Into the Breach": (0.82645, "Acolyte of Chayula: Flames of Chayula in your Breach"),
    "Wildwood's Gifts": (0.82645, "The Hollow Mask: Ancient Blooms"),
    "Sanguine Revelry": (0.5, "while repeatedly hitting a Bleeding target"),
    "Mana Remnants": (0.5, "while repeatedly critting an elementally ailmented target"),
    "Life Remnants": (0.3333, "on hit"),
    "Snap": (0.25, "per cast, before cooldown recovery"),
    "Frost Bomb": (0.1667, "per cast, before cooldown recovery"),
    "Lingering Illusion": (0.352, "the boss destroys one of your dodge-roll copies every 5 s (the user's rate, 9 Nov), "
                                  "1.76 Power Charge Remnants each"),
    "Living Bomb": (0.2, "assumed: one seed detonated every 5 s"),
    "Grim Feast": (0.1, "assumed: one Reviving minion dies every 10 s"),
    "Orb of Storms": (0.0833, "one Orb kept up: a Remnant when each 12 s Orb expires"),
    "Remnants of Kalguur": (0.0, "50% per Stun, at most one per 3 s: the boss's Heavy Stuns (models.heavy_stun_rate, "
                                 "from the build's own buildup; light stuns of a boss not counted)"),
    "Siphon Elements": (0.0, "a Cold Remnant on each Freeze (5% per Power, a boss's 20 Power: 100%), the boss's Freezes "
                             "from the main skill's buildup (models.buildup_rate); Ignite and Shock make one only on a "
                             "target without that ailment, which a boss you keep ailing rarely is: not counted, an "
                             "undercount"),
}
# Remnants of Kalguur (MetaRemnantsOfKalguurPlayer, build 26476662): ward_remnants_chance_to_spawn_remnant_on_stun_% 50,
# ward_remnants_spawn_remnant_on_stun_cooldown_ms 3000 ("no more than once every # seconds"): a Remnant every
# 3 s + the wait for the next successful Stun. Without the cap a fast stunner counted 50% of every Stun, an overcount
KALGUUR, KALGUUR_CHANCE, KALGUUR_COOLDOWN = "Remnants of Kalguur", 0.5, 3.0
# Siphon Elements (SiphonElementsPlayer): infusion_remnants_%_chance_to_spawn_cold_infusion_on_freezing_an_enemy 5, "per
# Power" (skill_stat_descriptions); "Unique monsters always have 20 Power" (KeywordPopups Power)
SIPHON, SIPHON_FREEZE_PER_POWER = "Siphon Elements", 0.05
# created_remnants_have_%_chance_to_duplicate_pick_up_results (the Empowering Remnants passive, the "of Accumulation" to
# "of Multiplication" item suffixes): "#% chance for Remnants you pick up to count as picking up an additional Remnant"
# (stat_descriptions), "... you create to grant their effects twice" (passive_skill_stat_descriptions): one stat, both
# wordings. Counted as that share of pickups counting twice (a second Infusion, a second explosion trigger)
DUPLICATE_PICKUP = re.compile(r"(\d+(?:\.\d+)?)% chance for Remnants you (?:pick up to count as picking up an "
                              r"additional|create to grant their effects twice)", re.I)  # (PoB splits the line before "Remnant")


def kalguur_rate(stuns: float) -> float:
    """Remnants of Kalguur per second from `stuns` Stuns per second: one per KALGUUR_COOLDOWN + the expected wait for
    the next Stun that makes one (KALGUUR_CHANCE each)."""
    return 1.0 / (KALGUUR_COOLDOWN + 1.0 / (KALGUUR_CHANCE * stuns)) if stuns > 0 else 0.0


def siphon_freezes(stats: dict) -> tuple[float, str]:
    """Siphon Elements' Cold Remnants per second: the boss's Freezes (models.buildup_rate) x the chance per Freeze."""
    freezes = models.buildup_rate(stats, "Freeze")
    chance = min(1.0, SIPHON_FREEZE_PER_POWER * models.BOSS_POWER)
    return freezes * chance, (f"{chance:.0%} per Freeze (5% per Power, 20 Power), the boss frozen {freezes:.3f}/s by the "
                              "main skill's buildup; Ignite and Shock Remnants not counted (an undercount)")


def duplicate_pickups(texts: list[str]) -> float:
    """The share of Remnant pickups that count twice (DUPLICATE_PICKUP), capped at 1."""
    return min(1.0, sum(float(m.group(1)) for t in texts if (m := DUPLICATE_PICKUP.search(t))) / 100.0)
NEEDS_MINIONS = {"Grim Feast"}  # its Remnants come from your minions' deaths
REAPPEAR = re.compile(r"Remnants you create reappear once", re.I)  # Storm's Recollection: each is collected twice
COOLDOWN_SOURCES = {"Snap", "Frost Bomb"}  # their rate scales with their own cooldown recovery
INFUSED_NOTE = ("the Infused cast needs an Elemental Infusion of its element, collected from a Remnant, and none of the "
                "build's skills makes that element's Infusions on a lone boss, so only the plain cast counts")
# Elemental Infusions per second on a single boss, by element (game files, build 26476662: the skills whose description
# leaves an Infusion Remnant, ActiveSkills search of 9 Nov, MECH19): Frost Bomb Cold, Orb of Storms Lightning, Living Bomb
# Fire, Snap the element of the ailment it consumes; rates as REMNANT_SOURCES. Siphon Elements' Cold ones on Freeze
# (siphon_freezes); its Fire and Lightning ones and Powered by Verisium's aren't counted yet (reasons below). The other Remnant sources (Into the Breach, Life, Mana, Power Charge
# Remnants) make no Infusions.
INFUSION_SOURCES = {
    "Frost Bomb": "Cold", "Orb of Storms": "Lightning", "Living Bomb": "Fire", "Snap": None,
}
INFUSION_UNCOUNTED = {
    "Siphon Elements": "Siphon Elements' Fire and Lightning Infusions (Ignite/Shock of a target without that ailment) "
                       "aren't counted: an undercount",
    "Powered by Verisium": "Powered by Verisium's Infusions (from Ward spent) aren't counted yet: an undercount",
}
SNAP_AILMENTS = {"conditionEnemyIgnited": "Fire", "conditionEnemyShocked": "Lightning", "conditionEnemyFrozen": "Cold"}
EQUILIBRIUM = re.compile(r"Create Lightning Infusion Remnants instead of Fire", re.I)  # Elemental Equilibrium
EQUILIBRIUM_SHIFT = {"Fire": "Lightning", "Lightning": "Cold", "Cold": "Fire"}
EXTRA_INFUSION_REMNANT = re.compile(r"(\d+(?:\.\d+)?)% chance to spawn an additional Infusion Remnant", re.I)
EXTRA_INFUSION = re.compile(r"(\d+(?:\.\d+)?)% chance to gain an additional Infusion$", re.I)
OTHER_INFUSION = re.compile(r"(\d+(?:\.\d+)?)% chance when collecting an Elemental Infusion to gain another different", re.I)
KEEP_INFUSION = re.compile(r"^(\d+(?:\.\d+)?)% chance to not remove an Infusion but still count as consuming", re.I)
VERISIUM_EVERY = re.compile(r"Every (?:(\d+(?:\.\d+)?) )?seconds?, gain an? Verisium Infusion", re.I)
INFUSED_ELEMENT = re.compile(r"\b(fire|cold|lightning)-infused\b", re.I)


def infusion_supply(present: dict[str, dict], lines: list[str], ailments: set[str],
                    siphon: tuple[float, str] = (0.0, "")) -> tuple[dict[str, float], list[str]]:
    """Elemental Infusions per second by element ("Any": Verisium Infusions, usable as any element), and what makes them.
    `present`: the build's enabled skills by name; `lines`: its modifier lines; `ailments`: the resolver's active
    condition vars (which ailment Snap consumes); `siphon`: Siphon Elements' Cold Remnants per second and how
    (siphon_freezes), used when the build has it."""
    supply = {"Fire": 0.0, "Cold": 0.0, "Lightning": 0.0, "Any": 0.0}
    used: list[str] = []
    made: dict[str, float] = {}
    for name, element in INFUSION_SOURCES.items():
        skill = present.get(name)
        if skill is None:
            continue
        rate = REMNANT_SOURCES[name][0] * ((skill.get("cooldownRecovery") or 1.0) if name in COOLDOWN_SOURCES else 1.0)
        if element is None:  # Snap: the element of the ailment it consumes, split evenly between those the build keeps up
            elements = [SNAP_AILMENTS[var] for var in sorted(ailments) if var in SNAP_AILMENTS]
            if not elements:
                used.append(f"{name} 0/s (no Ignite, Shock or Freeze on the boss to consume)")
                continue
            for e in elements:
                made[e] = made.get(e, 0.0) + rate / len(elements)
            used.append(f"{name} {rate:.2f}/s ({'/'.join(elements)}, its consumed ailment{'s split evenly' if len(elements) > 1 else ''})")
        else:
            made[element] = made.get(element, 0.0) + rate
            used.append(f"{name} {rate:.2f}/s {element}")
    if SIPHON in present:
        made["Cold"] = made.get("Cold", 0.0) + siphon[0]
        used.append(f"{SIPHON} {siphon[0]:.2f}/s Cold ({siphon[1]})")
    used += [why for name, why in INFUSION_UNCOUNTED.items() if name in present]
    texts = [t.strip() for t in lines]
    if any(EQUILIBRIUM.search(t) for t in texts):
        made = {EQUILIBRIUM_SHIFT[e]: r for e, r in made.items()}
        used.append("Elemental Equilibrium shifts each Remnant's element (Fire to Lightning, Lightning to Cold, Cold to Fire)")
    remnants = 1.0 + sum(float(m.group(1)) for t in texts if (m := EXTRA_INFUSION_REMNANT.search(t))) / 100.0
    if any(REAPPEAR.search(t) for t in texts):
        remnants *= 2.0
    remnants *= 1.0 + duplicate_pickups(texts)
    gain = 1.0 + sum(float(m.group(1)) for t in texts if "weapon" not in t.lower() and (m := EXTRA_INFUSION.search(t))) / 100.0
    other = sum(float(m.group(1)) for t in texts if (m := OTHER_INFUSION.search(t))) / 100.0
    for e, rate in made.items():
        rate *= remnants * gain
        supply[e] += rate
        for o in ("Fire", "Cold", "Lightning"):  # "another different" one: randomly either other element (the user, 9 Nov): evenly, its average
            if o != e:
                supply[o] += rate * other / 2
    for t in texts:
        if m := VERISIUM_EVERY.search(t):
            every = float(m.group(1) or 1.0)
            supply["Any"] += gain / every
            used.append(f"a Verisium Infusion every {every:g} s (any element)")
    if remnants != 1.0:
        used.append(f"x{remnants:g} Infusion Remnants (additional ones, collected twice, or pickups counting twice)")
    if gain != 1.0:
        used.append(f"+{gain - 1:.0%} chance of an additional Infusion")
    if other:
        used.append(f"{other:.0%} chance of another, different Infusion (randomly either other element, the user's word of 9 Nov: counted evenly, its average)")
    return supply, used


ADDITIONAL_REMNANT = re.compile(r"(\d+(?:\.\d+)?)% chance to create an additional Remnant", re.I)
REMNANT_CONSUMERS = {"Transmutation Explosion"}


RESCORE_MODELS = os.environ.get("BUILDER_RESCORE_MODELS") == "1"


def charm_stats(build: Build) -> dict:
    """The charm charges the build's equipped charms spend per second against a boss (flasks.charm_charges_used), for
    Cast on Charm Use (Bijouborne)."""
    from builder.search.flasks import CHARM_SLOTS, Charges, charm_charges_used

    gain = Charges.from_bridge(build.call("chargeGain"))
    raws = [build.equipped[slot].raw for slot in CHARM_SLOTS[:gain.charm_limit] if build.equipped.get(slot) is not None]
    infos = build.call("flaskData", raws=raws)["results"] if raws else []
    spent, basis = charm_charges_used(list(zip(infos, raws)), gain)
    return {"_charm_charges": spent, "_charm_basis": basis}


class Assumptions:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.mods: list[dict] = []
        self.active: list[dict] = []  # what the result shows
        self.cooldown_fallback: set[str] = set()  # triggered main skills PoB gave no rate
        self.extra_groups: dict[int, int | None] = {}  # groups whose triggered skills count through Full DPS
        self.stat_sets: dict[int, int] = {}  # stat sets chosen for them
        # The main skill's Infused stat set while it's the calculated one (Gems.choose_active_skill): label, factor (its
        # damage scaled to plain + share x (Infused - plain)), share, basis. `infused_skipped`: label -> why it isn't
        self.infused: dict | None = None
        self.infused_skipped: dict[str, str] = {}

    def main_skill(self) -> str | None:
        group = self.build.main_group()
        if not group or not group["skills"]:
            return None
        return group["skills"][min(group["mainActiveSkill"], len(group["skills"])) - 1]["name"]

    def models(self, skills: list[dict]) -> tuple[list[dict], list[dict], dict[int, int | None]]:
        """The custom models' lines, notes and extra damage groups ({index: stat set to calculate,
        or None to leave it}) for the current build."""
        build = self.build
        group = build.main_group()
        name = self.main_skill()
        found = [models.Skill.of(s) for s in skills if not s["disabled"]]
        main = next((s for s in found if group and s.group == group["index"] and s.name == name), None)
        main_gems = {g["name"] for g in group["gems"]} if group else set()
        stats = build.call("stats", fields=models.MODEL_FIELDS)
        if any(s.name == "Cast on Charm Use" for s in found):  # (its Energy: charm charges spent, from the flask model)
            stats = {**stats, **charm_stats(build)}
        source_lines = build.call("sourceLines")["lines"]  # (one call: the models below all read the build's lines)
        forbidden = set(build.ctx.request.forbid_ledger_entries)
        inputs = models.read_inputs(source_lines)
        mods, notes = models.archon_model(inputs, stats, main, main_gems)
        supports = {g["index"]: [x["name"] for x in g["gems"] if x["support"] and x.get("enabled", True)]
                    for g in build.groups()}
        energy_mods, energy_notes, energy_groups = models.energy_model(inputs, stats, found, main, supports)
        expression_mods, expression_notes, expression_groups = models.expression_model(found, stats, main)
        levels = {g["name"]: g["level"] for grp in build.groups() for g in grp["gems"] if g.get("level")}
        totem_mods, totem_notes = models.totem_model(found, stats, [e["text"] for e in source_lines], levels, main)
        energy_mods, energy_notes = energy_mods + totem_mods, energy_notes + totem_notes
        ward_mods, ward_notes = models.iron_ward_model(
            found, stats, enabled=models.IRON_WARD_ENTRY not in build.ctx.request.forbid_ledger_entries)
        stat_mods, stat_notes = models.gem_stat_model(found, set(build.ctx.request.forbid_ledger_entries), stats)
        harmony_mods, harmony_notes = models.harmony_model(inputs, stats)
        ring_mods, ring_notes = models.resist_rings_model(source_lines) if models.RESIST_ENTRY not in forbidden else ([], [])
        hand_mods, hand_notes = models.guided_hand_model(inputs, source_lines, stats, main)
        sanguine_mods, sanguine_notes = models.sanguine_model(inputs, stats)
        hand_mods, hand_notes = hand_mods + sanguine_mods, hand_notes + sanguine_notes
        resolve_mods, resolve_notes = models.deadly_resolve_model(found, stats, main)             if "deadly-resolve-rate" not in forbidden else ([], [])
        hand_mods, hand_notes = hand_mods + resolve_mods, hand_notes + resolve_notes
        metres = None
        if any(s.name == models.PREDATORS_MARK for s in found):  # (it counts you within 6 m of the boss)
            metres = models.number(build.call("getConfig")["input"].get("enemyDistance")) / 10 or natural_distance(set())
        mark_mods, mark_notes = models.mark_model(
            found, stats, main, source_lines, metres, [models.Skill.of(s) for s in skills if s["disabled"]])
        hand_mods, hand_notes = hand_mods + mark_mods, hand_notes + mark_notes
        curses = curse_uses(build, stats, [e["text"] for e in source_lines])
        if curses or any(models.CURSED_UNSCALED.search(e["text"]) or p.search(e["text"])
                         for e in source_lines for p, _ in models.CURSED_LINES):
            curse_mods, curse_notes = models.curse_model(curses, found, main, source_lines)
            hand_mods, hand_notes = hand_mods + curse_mods, hand_notes + curse_notes
        roar_mods, roar_notes = models.roar_model(found, stats, main)
        hand_mods, hand_notes = hand_mods + roar_mods, hand_notes + roar_notes
        mirage_mods, mirage_notes, mirage_groups = models.mirage_model(
            found, main, {g["name"]: (g["level"], g.get("quality") or 0) for grp in build.groups() for g in grp["gems"]
                          if g.get("level")}, rolling=any(s.name == "Cast on Dodge" for s in found))
        hand_mods, hand_notes = hand_mods + mirage_mods, hand_notes + mirage_notes
        hollow_mods, hollow_notes = models.hollow_model(found, stats, [e["text"] for e in source_lines], main)
        hand_mods, hand_notes = hand_mods + hollow_mods, hand_notes + hollow_notes
        pulse, pulse_notes = models.maelstrom_model(source_lines, stats) if models.MAELSTROM_ENTRY not in forbidden else (0.0, [])
        build.objective.added_dps = pulse  # separately computed damage (Melting Maelstrom's pulses); what-ifs get their own
        hand_mods += models.maelstrom_inputs(source_lines, pulse) if pulse_notes else []
        # A model whose effect follows the build's own numbers and that the bridge can't work out again per calculation
        # would make every item what-if a real trial (Build.item_deltas). All of today's are worked out in the bridge
        # (models.derived): uptimes from Mana and regeneration, Life spent, attack speed, Mana > Life, Maelstrom's pulses.
        # (BUILDER_RESCORE_MODELS=1 puts the trials back, to measure what they cost: benchmarks/rescore_cost.py)
        build.models_on_stats = bool(stat_notes or harmony_notes or hand_notes or pulse_notes
                                     or totem_notes) and RESCORE_MODELS
        mods += energy_mods + expression_mods + ward_mods + stat_mods + harmony_mods + ring_mods + hand_mods
        notes += energy_notes + expression_notes + ward_notes + stat_notes + harmony_notes + ring_notes + hand_notes + pulse_notes
        mods, corpse_notes = models.corpse_model(found, mods)  # last: it overrides the trigger rates above
        notes += corpse_notes + models.spirit_vessel_notes(skills)
        notes += models.granted_level_notes([(o.name, o.raw) for o in build.equipped.values() if o is not None and o.unique])
        chosen = [n for n in build.call("attributeNodes")["nodes"] if n["attribute"] >= 4]
        if chosen:
            kinds = {4: "Damage", 5: "Armour, Evasion and Energy Shield", 6: "Cost Efficiency"}
            counts = {kinds[a]: sum(1 for n in chosen if n["attribute"] == a) for a in kinds}
            notes.append({
                "label": "Traveller's Wisdom: attribute passives that take 5% instead of +5 to an attribute",
                "value": len(chosen),
                "basis": ", ".join(f"{count} take 5% increased {kind}" for kind, count in counts.items() if count)
                         + "; PoB can't store the choice, so the PoB code shows these passives as Strength (choose "
                         "them in game)",
                "confidence": "datamined"})
        if note := models.darkness_note(build.stats):
            notes.append(note)
        vessel_groups = models.spirit_vessel_groups(skills)  # (its companion's damage is in Full DPS only)
        return mods, notes, {**{g: None for g in energy_groups | mirage_groups | vessel_groups}, **expression_groups}

    def wanted(self) -> tuple[list[dict], list[dict]]:
        skills = self.build.call("activeSkills", levelStats=[models.IRON_WARD, models.FEROCIOUS_ROAR,
                                                             *models.gem_stat_skills()])["skills"]
        mods, notes, self.extra_groups = self.models(skills)
        explosion = next((s for s in skills if s["name"] == "Transmutation Explosion"), None)
        if explosion is not None:
            present = {s["name"]: s for s in skills if not s["disabled"]}
            supply, used = 0.0, []
            for name, (rate, condition) in REMNANT_SOURCES.items():
                skill = present.get(name)
                if skill is None:
                    continue
                if name in NEEDS_MINIONS and not any(s.get("minions") for s in present.values()):
                    used.append(f"{name} 0/s (no minions)")
                    continue
                if name in COOLDOWN_SOURCES:
                    rate *= skill.get("cooldownRecovery") or 1.0
                if name == KALGUUR:
                    stuns, how = models.heavy_stun_rate(self.build.call("stats", fields=models.MODEL_FIELDS))
                    rate, condition = kalguur_rate(stuns), f"50% per Stun, at most one per 3 s, {how}"
                elif name == SIPHON:
                    rate, condition = siphon_freezes(self.build.call("stats", fields=models.IMMOBILISE_FIELDS))
                supply += rate
                used.append(f"{name} {rate:.2f}/s ({condition})")
            lines = self.build.call("sourceLines")["lines"]
            extra = sum(float(m.group(1)) for line in lines if (m := ADDITIONAL_REMNANT.search(line["text"]))) / 100.0
            supply *= 1.0 + extra
            twice = any(REAPPEAR.search(line["text"]) for line in lines)
            if twice:
                supply *= 2.0
            duplicate = duplicate_pickups([line["text"] for line in lines])
            supply *= 1.0 + duplicate
            cooldown = explosion.get("cooldown") or 0.0
            cap = (explosion.get("cooldownRecovery") or 1.0) / cooldown if cooldown else math.inf
            rate = min(supply, cap)
            mods += [
                {"kind": "SkillData", "skill": "Transmutation Explosion", "key": "triggerRate", "value": rate},
                {"kind": "SkillData", "skill": "Transmutation Explosion", "key": "triggered", "value": True},
            ]
            basis = ", ".join(used) if used else "none of the build's skills makes Remnants on a lone boss"
            notes.append({
                "label": "Explosive Transmutation explosions per second",
                "value": round(rate, 3),
                "basis": f"Remnants {supply:.2f}/s from {basis}"
                         + (f", +{extra:.0%} additional Remnants" if extra else "")
                         + (", each collected twice (they reappear once)" if twice else "")
                         + (f", {duplicate:.0%} of pickups counting twice" if duplicate else "")
                         + f"; cooldown cap {cap:.2f}/s",
                "confidence": "game files' rates; assumed cadences as labelled (launch-test the pickup rate)",
            })
        # Fallback for any other triggered main skill with a cooldown whose trigger PoB doesn't
        # model (it calculates 0 uses per second): assume it fires whenever the cooldown allows
        main = self.main_skill()
        info = next((s for s in skills if s["name"] == main), None)
        flare = self.mana_flare(skills)
        if flare is not None:
            mods += flare[0]
            notes.append(flare[1])
        glory = self.glory_main(skills, main)
        if glory is not None:
            mods += glory[0]
            notes.append(glory[1])
        if (not (flare is not None and main == models.MANA_FLARE) and glory is None
                and info is not None and main not in REMNANT_CONSUMERS and info.get("triggered") and info.get("cooldown")
                and not info.get("corpses")  # (models.corpse_model rates those)
                and (main in self.cooldown_fallback
                     or not self.build.call("stats", fields=["Speed"]).get("Speed"))):
            self.cooldown_fallback.add(main)
            rate = (info.get("cooldownRecovery") or 1.0) / info["cooldown"]
            mods += [
                {"kind": "SkillData", "skill": main, "key": "triggerRate", "value": rate},
                {"kind": "SkillData", "skill": main, "key": "triggered", "value": True},
            ]
            notes.append({
                "label": f"{main} uses per second", "value": round(rate, 3),
                "basis": f"fires whenever its {info['cooldown']:g}s cooldown allows (its trigger isn't modeled in PoB; "
                         "assumes the build provides the trigger often enough)",
                "confidence": "theory",
            })
        group = self.build.main_group()
        infused = sorted({label for skill in (group or {}).get("skills", []) for label in skill.get("statSets") or []
                          if INFUSED_SET.search(label)})
        chosen = self.infused if self.infused is not None and self.infused["label"] in infused else None
        if chosen is not None:
            mods.append(models.line("1% more Damage", 100.0 * (chosen["factor"] - 1.0), skill=main))
            notes.append({"label": f"{main}: share of casts that are {chosen['label']}", "value": round(chosen["share"], 2),
                          "basis": chosen["basis"] + "; the rest are the plain cast, so its damage counts as plain + "
                                   f"{chosen['share']:.0%} of the Infused cast's gain",
                          "play": "Collect each Infusion Remnant as it appears; one left lying stops counting.",
                          "confidence": "game files' rates; assumed cadences as labelled (launch-test the pickup rate)"})
        for label in infused:
            if chosen is None or label != chosen["label"]:
                notes.append({"label": f"{main}: {label} cast not counted", "value": 0,
                              "basis": self.infused_skipped.get(label, INFUSED_NOTE), "confidence": "theory"})
        found, condition_notes = self.ledger_conditions(skills)
        mods += [{"kind": "Condition", "name": name} for name in found]
        return mods, notes + condition_notes

    def stats_as_main(self, group: dict, position: int, main_index: int, fields: list[str]) -> dict:
        """PoB's stats with the skill at `position` in `group` calculated as the main skill for a moment (its group made
        the main group if it isn't), then the group's main skill and the main group put back."""
        index, current = group["index"], group.get("mainActiveSkill") or 1
        if index == main_index and current == position:
            return self.build.call("stats", fields=fields)
        if index == main_index:
            stats = self.build.call("setGroup", index=index, mainActiveSkill=position, fields=fields)["stats"]
            self.build.call("setGroup", index=index, mainActiveSkill=current, fields=[])
            return stats
        stats = self.build.call("setGroup", index=index, main=True, mainActiveSkill=position, fields=fields)["stats"]
        if position != current:
            self.build.call("setGroup", index=index, mainActiveSkill=current, fields=[])
        self.build.call("setGroup", index=main_index, main=True, fields=[])
        return stats

    def glory_main(self, skills: list[dict], main: str | None) -> tuple[list[dict], dict] | None:
        """A Glory skill as the main skill (models.GLORY_SKILLS): PoB calculates it at will; it's used as often as the
        build makes its Glory. The Glory comes from another skill's hits (the first other enabled skill in the main
        group, else in a Full DPS group), calculated as the main skill for a moment, through models.glory_uses; with no
        other skill, from its own hits (assumed to count for it), at will if a use builds its own cost, else never. Its
        uses per second become its trigger rate (capped at one per cast time). None for any other main skill."""
        if main not in models.GLORY_SKILLS:
            return None
        info = next((s for s in skills if s["name"] == main and not s["disabled"]), None)
        if info is None:
            return None
        listing = self.build.call("groups")
        main_index = listing["mainSocketGroup"]
        builder = None
        for group in sorted(listing["groups"], key=lambda g: g["index"] != main_index):
            if not group.get("enabled", True) or not (group["index"] == main_index or group.get("fullDPS")):
                continue
            names = [s["name"] for s in group.get("skills") or []]
            position = next((i for i, n in enumerate(names, 1) if n != main and n not in models.GLORY_SKILLS
                             and any(s["name"] == n and not s["disabled"] for s in skills)), None)
            if position is not None:
                builder = (group, position, names[position - 1])
                break
        skill = models.Skill.of(info)
        cap = 1 / skill.cast_time if skill.cast_time else math.inf
        if builder is not None:
            group, position, name = builder
            stats = self.stats_as_main(group, position, main_index, models.MODEL_FIELDS)
            source = next((models.Skill.of(s) for s in skills if s["name"] == name), None)
            uses, how = models.glory_uses([main], stats, source)
            rate, basis = min(uses, cap), f"Glory from {name}'s hits: {how}"
        else:
            stats = self.build.call("stats", fields=models.MODEL_FIELDS)
            # (at one use per second, so the rate this model set before doesn't decide it)
            per_use, how = models.glory_uses([main], dict(stats, Speed=1.0), skill)
            rate = cap if per_use >= 1.0 else 0.0
            basis = (f"no other skill builds its Glory; its own hits (assumed to count for it), per use at one use/s: "
                     f"{how}: " + ("a use builds its own cost: at will" if rate else "less than a use's cost: never"))
        return models.trigger_rate(main, rate), {
            "label": f"{main} uses per second (Glory)", "value": round(rate, 3),
            "basis": basis + "; the time spent on the skill that builds it isn't taken from that skill's own DPS",
            "play": f"Use {main} whenever you have the Glory for it.",
            "confidence": "theory: game files' Glory costs and gains; event rates as glory_uses labels them",
        }

    def mana_flare(self, skills: list[dict]) -> tuple[list[dict], dict] | None:
        """Mana Flare (models.mana_flare), as the main group's main skill or counted through Full DPS beside the spell it
        supports, in the main group or another one (2b75e: in Ice Nova's own group): its rate from that spell's Critical
        Hits, and its damage scaled to the mana it finds. The spell is calculated as the main skill for a moment for that
        (its group made the main group if it isn't). None when no group counts a flare beside another skill. PoB applies
        one rate to every Mana Flare by name, so with flares in several counted groups the first (the main group's
        first) sets it, noted."""
        info = next((s for s in skills if s["name"] == models.MANA_FLARE), None)
        if info is None:
            return None
        listing = self.build.call("groups")
        main_index = listing["mainSocketGroup"]
        counted = []  # (group, the spell's position, the group's main skill)
        for group in sorted(listing["groups"], key=lambda g: g["index"] != main_index):
            names = [s["name"] for s in group.get("skills") or []]
            if models.MANA_FLARE not in names or not group.get("enabled", True):
                continue
            flare, current = names.index(models.MANA_FLARE) + 1, group.get("mainActiveSkill") or 1
            spell = current if current != flare else next((i for i, n in enumerate(names, 1)
                                                           if n != models.MANA_FLARE), None)
            is_main = group["index"] == main_index
            if spell is None or not (group.get("fullDPS") or (is_main and current == flare)):
                continue
            if not is_main and main_index < 1:
                continue
            counted.append((group, spell, current))
        if not counted:
            return None
        group, spell, current = counted[0]
        index, names = group["index"], [s["name"] for s in group["skills"]]
        stats = self.stats_as_main(group, spell, main_index, models.MANA_FLARE_FIELDS)
        rate, share, basis = models.mana_flare(stats, info.get("cooldown") or 1.0, info.get("cooldownRecovery") or 1.0)
        mods = models.trigger_rate(models.MANA_FLARE, rate)
        if share < 1.0:  # (PoB calculates the flare at full mana)
            mods.append(models.line("1% more Damage", 100.0 * (share - 1.0), skill=models.MANA_FLARE))
        return mods, {
            "label": f"Mana Flare uses per second (supporting {names[spell - 1]})", "value": round(rate, 3),
            "basis": basis + ("" if group["index"] == main_index else f" (in socket group {index}, beside the main skill: "
                              "mana the main skill spends isn't taken from the flare's)")
                     + "".join(f"; the flare in socket group {g['index']} takes the same rate (PoB rates every Mana "
                               "Flare by name; not modelled apart)" for g, _, _ in counted[1:]),
            "play": "Mana Flare fires on the supported spell's Critical Hits, at most once a second, and spends a "
                    "quarter of the mana you have: keep casting the spell, and its damage depends on how much mana "
                    "you keep.",
            "confidence": "game files' trigger, cooldown and mana share; Critical Hits taken at their average rate",
        }

    def infusion_share(self, element: str, ailments: set[str]) -> tuple[float, str]:
        """The share of the main skill's casts that find an Elemental Infusion of `element` (one consumed per cast):
        the build's supply (infusion_supply) over its casts per second, less those that keep the Infusion."""
        build = self.build
        present = {s["name"]: s for s in build.call("activeSkills")["skills"] if not s["disabled"]}
        lines = [line["text"] for line in build.call("sourceLines")["lines"]]
        siphon = siphon_freezes(build.call("stats", fields=models.IMMOBILISE_FIELDS)) if SIPHON in present else (0.0, "")
        supply, used = infusion_supply(present, lines, ailments, siphon)
        keep = min(1.0, sum(float(m.group(1)) for t in lines if (m := KEEP_INFUSION.search(t.strip()))) / 100.0)
        uses = float(build.call("stats", fields=["Speed"]).get("Speed") or 0.0)
        have = supply[element] + supply["Any"]
        needed = uses * (1.0 - keep)
        share = min(1.0, have / needed) if needed > 0 else float(have > 0)
        basis = (f"{have:.2f} {element} Infusions/s from {', '.join(used) or 'nothing'} for {uses:.2f} casts/s"
                 + (f", {keep:.0%} of casts keeping theirs" if keep else ""))
        return share, basis

    def ledger_conditions(self, skills: list[dict]) -> tuple[list[str], list[dict]]:
        """Ledger conditions the build's skills switch on (e.g. it uses a skill that requires Glory), and notes on those
        that need a rate (glory_every: one use of a Glory skill per that many seconds, models.glory_uses)."""
        wanted = conditions()
        if not wanted:
            return [], []
        actives = [s["name"] for s in skills if not s["disabled"]]
        names = actives + [gem["name"] for group in self.build.groups() for gem in group["gems"]
                           if gem.get("enabled", True)]
        texts = [(self.build.ctx.all_gems.get(name) or {}).get("description") or "" for name in actives]
        main = self.main_skill()
        main_types = set((self.build.ctx.all_gems.get(main) or {}).get("skillTypes") or []) if main else set()
        found, notes = [], []
        for condition in wanted:
            by_name = condition.get("skills") and any(re.search(condition["skills"], n, re.I) for n in names)
            by_text = condition.get("skill_text") and any(re.search(condition["skill_text"], t, re.I) for t in texts)
            if by_text and condition.get("usage_stat"):
                rules = usage_rules(self.build)
                by_text = any(condition["usage_stat"] in ((rules.get(name) or {}).get("stats") or {})
                              for name, text in zip(actives, texts) if re.search(condition["skill_text"], text, re.I))
            # skill_types: the main skill has every one of these PoB skill types (e.g. Spell and Multicastable: it can Repeat)
            by_type = condition.get("skill_types") and set(condition["skill_types"]) <= main_types
            if (by_name or by_text or by_type) and condition.get("glory_every"):
                stats = self.build.call("stats", fields=models.MODEL_FIELDS)
                main_skill = next((models.Skill.of(s) for s in skills if not s["disabled"] and s["name"] == main), None)
                uses, how = models.glory_uses(names, stats, main_skill)
                every = condition["glory_every"]
                notes.append({"label": f"{condition['title']}: Glory skill uses on a boss",
                              "value": f"one per {1 / uses:.1f} s" if uses > 0 else "none",
                              "basis": f"{how}; its ledger lines assume one per {every:g} s, so they count only at that "
                                       "rate or faster", "confidence": "theory"})
                if uses * every < 1:
                    continue
            if by_name or by_text or by_type:
                found.append(condition["id"])
        return sorted(found), notes

    def apply(self) -> bool:
        """Match PoB's assumed modifiers to the build. True if they changed."""
        mods, notes = self.wanted()
        self.active = notes
        build = self.build
        flagged = False
        # (scored on Full DPS once there are extra groups: the main skill's group must count in it too, which a loaded
        # build's own flags don't promise)
        main = build.main_group() if self.extra_groups else None
        wanted = {**({main["index"]: None} if main else {}), **self.extra_groups}
        for group in build.groups():
            index = group["index"]
            if index not in wanted:
                continue
            stat_set = wanted[index]
            if not group["fullDPS"] or (stat_set is not None and self.stat_sets.get(index) != stat_set):
                build.set_group(index=index, fullDPS=True, **({"statSet": stat_set} if stat_set else {}))
                if stat_set:
                    self.stat_sets[index] = stat_set
                flagged = True
        extra = bool(self.extra_groups)
        switched = build.objective.extra != extra
        build.objective.extra = extra
        if mods == self.mods:
            if switched or flagged:
                build.refresh()
            return switched or flagged
        self.mods = mods
        build._committed(build.call("setAssumptions", mods=mods, fields=build.objective.fields))
        return True

    def reinstall(self) -> None:
        """After a build was (re)loaded, which drops engine-side assumptions (and may not keep a
        granted skill's chosen stat set)."""
        for index, stat_set in self.stat_sets.items():
            if self.extra_groups.get(index) == stat_set:
                self.build.call("setGroup", index=index, fullDPS=True, statSet=stat_set, fields=[])
        if self.mods:
            self.build._committed(self.build.call("setAssumptions", mods=self.mods, fields=self.build.objective.fields))

    def relevant(self, gem_name: str) -> bool:
        """Would adding this gem (or a passive granting this skill) change an assumption, so it
        deserves a real trial?"""
        if self.mods and gem_name in REMNANT_SOURCES:
            return True
        if gem_name == models.EXPRESSION:
            return True  # its trigger rate and Full DPS share only arrive with the assumptions
        text = (self.build.ctx.all_gems.get(gem_name) or {}).get("description") or ""
        return any((c.get("skills") and re.search(c["skills"], gem_name, re.I))
                   or (c.get("skill_text") and re.search(c["skill_text"], text, re.I)) for c in conditions())

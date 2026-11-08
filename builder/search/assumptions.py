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
from builder.search.state import Build

# Remnants per second on a single boss, at gem level 20, while the stated condition holds
REMNANT_SOURCES = {
    "Into the Breach": (0.82645, "Acolyte of Chayula: Flames of Chayula in your Breach"),
    "Wildwood's Gifts": (0.82645, "The Hollow Mask: Ancient Blooms"),
    "Sanguine Revelry": (0.5, "while repeatedly hitting a Bleeding target"),
    "Mana Remnants": (0.5, "while repeatedly critting an elementally ailmented target"),
    "Life Remnants": (0.3333, "on hit"),
    "Snap": (0.25, "per cast, before cooldown recovery"),
    "Frost Bomb": (0.1667, "per cast, before cooldown recovery"),
    "Orb of Storms": (0.1, "roughly, in a normal cast cadence"),
}
COOLDOWN_SOURCES = {"Snap", "Frost Bomb"}  # their rate scales with their own cooldown recovery
ADDITIONAL_REMNANT = re.compile(r"(\d+(?:\.\d+)?)% chance to create an additional Remnant", re.I)
REMNANT_CONSUMERS = {"Transmutation Explosion"}


RESCORE_MODELS = os.environ.get("BUILDER_RESCORE_MODELS") == "1"


class Assumptions:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.mods: list[dict] = []
        self.active: list[dict] = []  # what the result shows
        self.cooldown_fallback: set[str] = set()  # triggered main skills PoB gave no rate
        self.extra_groups: dict[int, int | None] = {}  # groups whose triggered skills count through Full DPS
        self.stat_sets: dict[int, int] = {}  # stat sets chosen for them

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
        mark_mods, mark_notes = models.mark_model(found, stats, main)
        hand_mods, hand_notes = hand_mods + mark_mods, hand_notes + mark_notes
        mirage_mods, mirage_notes, mirage_groups = models.mirage_model(
            found, main, {g["name"]: (g["level"], g.get("quality") or 0) for grp in build.groups() for g in grp["gems"]
                          if g.get("level")}, rolling=any(s.name == "Cast on Dodge" for s in found))
        hand_mods, hand_notes = hand_mods + mirage_mods, hand_notes + mirage_notes
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
        return mods, notes, {**{g: None for g in energy_groups | mirage_groups}, **expression_groups}

    def wanted(self) -> tuple[list[dict], list[dict]]:
        skills = self.build.call("activeSkills", levelStats=[models.IRON_WARD, *models.gem_stat_skills()])["skills"]
        mods, notes, self.extra_groups = self.models(skills)
        explosion = next((s for s in skills if s["name"] == "Transmutation Explosion"), None)
        if explosion is not None:
            present = {s["name"]: s for s in skills if not s["disabled"]}
            supply, used = 0.0, []
            for name, (rate, condition) in REMNANT_SOURCES.items():
                skill = present.get(name)
                if skill is None:
                    continue
                if name in COOLDOWN_SOURCES:
                    rate *= skill.get("cooldownRecovery") or 1.0
                supply += rate
                used.append(f"{name} {rate:.2f}/s ({condition})")
            lines = self.build.call("sourceLines")["lines"]
            extra = sum(float(m.group(1)) for line in lines if (m := ADDITIONAL_REMNANT.search(line["text"]))) / 100.0
            supply *= 1.0 + extra
            cooldown = explosion.get("cooldown") or 0.0
            cap = (explosion.get("cooldownRecovery") or 1.0) / cooldown if cooldown else math.inf
            rate = min(supply, cap)
            mods += [
                {"kind": "SkillData", "skill": "Transmutation Explosion", "key": "triggerRate", "value": rate},
                {"kind": "SkillData", "skill": "Transmutation Explosion", "key": "triggered", "value": True},
            ]
            basis = ", ".join(used) if used else "no Remnant source that works on a single boss"
            notes.append({
                "label": "Explosive Transmutation explosions per second",
                "value": round(rate, 3),
                "basis": f"Remnants {supply:.2f}/s from {basis}"
                         + (f", +{extra:.0%} additional Remnants" if extra else "")
                         + f"; cooldown cap {cap:.2f}/s",
                "confidence": "your audit of 0.5.4 (launch-test the pickup rate)",
            })
        # Fallback for any other triggered main skill with a cooldown whose trigger PoB doesn't
        # model (it calculates 0 uses per second): assume it fires whenever the cooldown allows
        main = self.main_skill()
        info = next((s for s in skills if s["name"] == main), None)
        if (info is not None and main not in REMNANT_CONSUMERS and info.get("triggered") and info.get("cooldown")
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
        mods += [{"kind": "Condition", "name": name} for name in self.ledger_conditions(skills)]
        return mods, notes

    def ledger_conditions(self, skills: list[dict]) -> list[str]:
        """Ledger conditions the build's skills switch on (e.g. it uses a skill that requires Glory)."""
        wanted = conditions()
        if not wanted:
            return []
        actives = [s["name"] for s in skills if not s["disabled"]]
        names = actives + [gem["name"] for group in self.build.groups() for gem in group["gems"]
                           if gem.get("enabled", True)]
        texts = [(self.build.ctx.all_gems.get(name) or {}).get("description") or "" for name in actives]
        main = self.main_skill()
        main_types = set((self.build.ctx.all_gems.get(main) or {}).get("skillTypes") or []) if main else set()
        found = []
        for condition in wanted:
            by_name = condition.get("skills") and any(re.search(condition["skills"], n, re.I) for n in names)
            by_text = condition.get("skill_text") and any(re.search(condition["skill_text"], t, re.I) for t in texts)
            # skill_types: the main skill has every one of these PoB skill types (e.g. Spell and Multicastable: it can Repeat)
            by_type = condition.get("skill_types") and set(condition["skill_types"]) <= main_types
            if by_name or by_text or by_type:
                found.append(condition["id"])
        return sorted(found)

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

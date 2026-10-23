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
"""

from __future__ import annotations

import math
import re

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


class Assumptions:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.mods: list[dict] = []
        self.active: list[dict] = []  # what the result shows
        self.cooldown_fallback: set[str] = set()  # triggered main skills PoB gave no rate

    def main_skill(self) -> str | None:
        group = self.build.main_group()
        if not group or not group["skills"]:
            return None
        return group["skills"][min(group["mainActiveSkill"], len(group["skills"])) - 1]["name"]

    def wanted(self) -> tuple[list[dict], list[dict]]:
        skills = self.build.call("activeSkills")["skills"]
        mods, notes = [], []
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
        return mods, notes

    def apply(self) -> bool:
        """Match PoB's assumed modifiers to the build. True if they changed."""
        mods, notes = self.wanted()
        self.active = notes
        if mods == self.mods:
            return False
        self.mods = mods
        self.build._committed(self.build.call("setAssumptions", mods=mods, fields=self.build.objective.fields))
        return True

    def reinstall(self) -> None:
        """After a build was (re)loaded, which drops engine-side assumptions."""
        if self.mods:
            self.build._committed(self.build.call("setAssumptions", mods=self.mods, fields=self.build.objective.fields))

    def relevant(self, gem_name: str) -> bool:
        """Would adding this gem change an assumption (so it deserves a real trial)?"""
        return bool(self.mods) and gem_name in REMNANT_SOURCES

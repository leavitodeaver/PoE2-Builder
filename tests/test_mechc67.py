"""MECHC67's tests (MECH-C): the charge-blocking rule by stat (AUDIT39 A39-66)."""
import re

import pytest
from pathlib import Path

from builder.search import conditions

POB_DATA = Path(__file__).resolve().parents[1] / "vendor" / "PathOfBuilding-PoE2" / "src" / "Data"


def _groups(*groups, enabled=True):
    return [{"enabled": enabled, "gems": [{"name": n} for n in g]} for g in groups]


def test_charge_blockers_are_every_gem_whose_effect_carries_the_stat():
    """A39-66: CHARGE_BLOCKERS is every gem whose own or granted effect lists cannot_consume_power_frenzy_endurance_
    charges in PoB's skill data (Inhibitor's support, the Mirage / illusion hidden supports, the triggered Surges), and
    cannot_consume_infusions is Inhibitor's alone."""
    def carriers(stat):
        effects = set()
        for f in (POB_DATA / "Skills").glob("*.lua"):
            text = f.read_text(encoding="utf-8")
            for m in re.finditer(r'^skills\["([^"]+)"\] = \{(.*?)^\}', text, re.S | re.M):
                if f'"{stat}"' in m.group(2):
                    effects.add(m.group(1))
        gems = {}
        text = (POB_DATA / "Gems.lua").read_text(encoding="utf-8")
        for m in re.finditer(r'\["Metadata/Items/Gems/[^"]+"\] = \{(.*?)\n\t\},', text, re.S):
            body = m.group(1)
            own = re.search(r'\bgrantedEffectId = "([^"]+)"', body)
            extra = re.findall(r'additionalGrantedEffectId\d = "([^"]+)"', body)
            name = re.search(r'name = "([^"]+)"', body).group(1)
            if own and own.group(1) in effects:
                gems[name] = "self" if not own.group(1).startswith("Support") else "group"
            elif any(e in effects for e in extra):
                gems[name] = "group"
        return gems

    assert carriers(conditions.CHARGE_BLOCK_STAT) == conditions.CHARGE_BLOCKERS
    assert carriers("cannot_consume_infusions") == conditions.INFUSION_BLOCKERS


def test_a_mirage_archer_copy_spends_no_charges_but_your_own_copy_does():
    """A39-66: a skill socketed in Mirage Archer (or Mirage Deadeye, Hollow Form) is the Mirage's: it can't spend your
    charges. The same skill in a group of your own still can; a self-carrier (a Surge) is blocked by itself."""
    assert conditions.inhibited_skills(_groups(["Mirage Archer", "Barrage", "Execute"])) == {"Barrage", "Execute"}
    assert conditions.inhibited_skills(_groups(["Mirage Archer", "Barrage"], ["Barrage", "Lasting Shock"])) == set()
    assert conditions.inhibited_skills(_groups(["Hollow Form", "Whirling Assault"])) == {"Whirling Assault"}
    assert conditions.inhibited_skills(_groups(["Hollow Form", "Inhibitor"])) == {"Hollow Form"}
    assert conditions.inhibited_skills(_groups(["Gemini Surge"])) == {"Gemini Surge"}
    assert conditions.inhibited_skills(_groups(["Mirage Archer", "Barrage"], enabled=False)) == set()


def test_only_inhibitor_blocks_infusions():
    """A39-66: cannot_consume_infusions is Inhibitor's alone, so a Mirage's copy still takes Infusions."""
    assert conditions.inhibited_skills(_groups(["Mirage Archer", "Lightning Arrow"]), True) == set()
    assert conditions.inhibited_skills(_groups(["Inhibitor", "Arc"]), True) == {"Arc"}


def test_the_note_names_the_gem_that_blocks():
    """A39-66: the note names the blocking gem (Inhibitor's wording unchanged)."""
    from builder.search.assumptions import Assumptions

    class Stub:
        def call(self, name, **_):
            return {"groups": [{"enabled": True, "gems": [{"name": "Mirage Archer"}, {"name": "Barrage"}]}]}

    a = Assumptions.__new__(Assumptions)
    a.build = Stub()
    assert a.inhibited() == {"Barrage"} and a.charge_blocker("Barrage") == "Mirage Archer"
    note = Assumptions.inhibited_note("Barrage", "x", a.charge_blocker("Barrage"))
    assert note["label"].startswith("Mirage Archer:") and "Mirage or illusions" in note["basis"]
    assert Assumptions.inhibited_note("Oil Barrage", "x")["basis"].startswith("Inhibitor supports Oil Barrage:")


def test_remnants_made_at_the_boss_are_collected_by_walking_in_from_range():
    """Item 4 (RESEARCH64 R64-9): Life / Mana / Kalguur Remnants appear near the target. Melee collects them where it
    stands; standing still at range collects none; moving at range walks in once per Remnant life (8 s)."""
    from builder.search.assumptions import target_walk

    assert target_walk(2.0, 1.5, 3.7, 0.3, 8.0, False)[:2] == (1.0, 0.0)  # (within 1.5 + 1.5 m)
    assert target_walk(2.0, 1.5, 3.7, 0.3, 8.0, True)[:2] == (1.0, 0.0)  # (melee standing still: still in reach)
    assert target_walk(8.0, 1.5, 3.7, 0.3, 8.0, True)[:2] == (0.0, 0.0)
    share, walk, how = target_walk(8.0, 1.5, 3.7, 0.3, 8.0, False)
    trip = 2 * (8.0 - 3.0) / 3.7
    assert share == 1.0 and walk == pytest.approx(trip / 8.0) and "walk in and back" in how
    # slower than one a life: one visit a Remnant
    assert target_walk(8.0, 1.5, 3.7, 0.05, 8.0, False)[1] == pytest.approx(0.05 * trip)
    # a longer pickup radius shortens the walk
    assert target_walk(8.0, 3.0, 3.7, 0.3, 8.0, False)[1] < walk


def test_the_remnant_pickup_base_is_the_users_measured_2_6_m():
    """RESEARCH66 R66-1 (THE USER's recording, MEDIUM): base 2.6 m, rows 2.4 / 2.8 (its range) and 3.0 (without
    Harmonic Remnants II); was 1.5 m with no source."""
    from builder.search import remnant_walk

    assert remnant_walk.BASE_PICKUP == 2.6 and remnant_walk.PICKUP_ROWS == (2.4, 2.8, 3.0)


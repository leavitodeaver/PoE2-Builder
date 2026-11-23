"""SEARCHB6 (the manager's 08:30, 23 Nov): supports whose value lives in our models (MECHB58: PoB adds no hit for a
spell's Echo, so Spell Echo's what-if reads only its 30% less cast speed) get a real trial in the support rounds when the
group's active skill fits the model (state.MODEL_HOOKED_SUPPORTS, Build._hooked_picks)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from builder.search import state
from builder.search.state import Build

TABLES = Path(__file__).resolve().parent.parent / "data" / "game-26650308" / "tables"
ECHO_STAT = "support_spell_echo_number_of_echo_cascades"


def rows(name: str) -> list[dict]:
    data = json.loads((TABLES / name).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("rows", data)


@pytest.mark.skipif(not TABLES.exists(), reason="no game tables for build 26650308")
def test_the_hooked_list_is_every_support_that_grants_the_echo_stat():
    """The claim in MODEL_HOOKED_SUPPORTS' note, rechecked by stat: every granted effect whose stat sets (constant or per
    level) carry the Echo count, through GrantedEffects' StatSet / AdditionalStatSets."""
    stat = next(i for i, r in enumerate(rows("Stats.json")) if r.get("Id") == ECHO_STAT)
    sets = {i for i, r in enumerate(rows("GrantedEffectStatSets.json"))
            if stat in (r.get("ConstantStats") or []) or stat in (r.get("ImplicitStats") or [])}
    for r in rows("GrantedEffectStatSetsPerLevel.json"):
        if any(stat in (r.get(k) or []) for k in ("FloatStats", "AdditionalStats", "AdditionalFlags")):
            sets.add(r.get("StatSet"))
    effects = {g["Id"] for g in rows("GrantedEffects.json")
               if g.get("StatSet") in sets or set(g.get("AdditionalStatSets") or []) & sets}
    assert effects == set(state.MODEL_HOOKED_SUPPORTS)


GEMS = {
    "Comet": {"skillTypes": ["Spell", "Cascadable", "Area"]},
    "Spark": {"skillTypes": ["Spell", "Projectile"]},
    "Spell Echo": {"support": True, "grantedEffectId": "SupportSpellEchoPlayer"},
    "Zarokh's Refrain": {"support": True, "grantedEffectId": "SupportZarokhsRefrainPlayer"},
    "Elemental Focus": {"support": True, "grantedEffectId": "SupportElementalFocusPlayer"},
}


def group(index: int, active: str, supports: list[str]) -> dict:
    return {"index": index, "gems": [{"name": active, "enabled": True, "support": False},
                                      *({"name": s, "enabled": True, "support": True} for s in supports)]}


def test_only_hooked_supports_on_a_fitting_group_are_picked_best_put_in_first():
    build = SimpleNamespace(ctx=SimpleNamespace(gems=GEMS))
    groups = {1: group(1, "Comet", ["Spell Echo"]), 2: group(2, "Spark", [])}
    cases = [{"group": 1, "slot": 3, "gem": "Zarokh's Refrain"},  # 0
             {"group": 1, "slot": 2, "gem": "Zarokh's Refrain"},  # 1 (better what-if)
             {"group": 1, "slot": 2, "remove": True},  # 2 (takes Spell Echo out)
             {"group": 1, "slot": 3, "gem": "Elemental Focus"},  # 3 (not hooked)
             {"group": 2, "slot": 2, "gem": "Spell Echo"}]  # 4 (Spark: not Cascadable)
    support = {0: "Zarokh's Refrain", 1: "Zarokh's Refrain", 2: "Spell Echo", 3: "Elemental Focus", 4: "Spell Echo"}
    scored = [(1.0, {}), (2.0, {}), (5.0, {}), (9.0, {}), (9.0, {})]
    assert Build._hooked_picks(build, cases, groups, support, scored) == [1, 2]


def test_the_switch_is_on_by_default(monkeypatch):
    monkeypatch.delenv(state.HOOKED_SWITCH, raising=False)
    assert state.hooked_rescore_enabled()
    monkeypatch.setenv(state.HOOKED_SWITCH, "0")
    assert not state.hooked_rescore_enabled()

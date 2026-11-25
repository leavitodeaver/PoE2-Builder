"""GEM43: Shockwave Totem's "Jagged Ground Eruption" set counted as Melee (RESEARCH59 R59-3, MEDIUM), with the "not
melee" row."""
from __future__ import annotations

import pytest

from builder.search import models
from tests.test_gem41 import engine  # noqa: F401

MELEE_LINE = "100% increased Melee Damage"


def test_the_eruption_note_gives_the_number_without_the_melee_reading():
    sets = [{"label": "Slam", "TotalDPS": 24.5},
            {"label": "Jagged Ground Eruption", "TotalDPS": 88.4, "TotalDPSWithoutAdded": 44.2, "addedFlags": "melee"}]
    mods, notes = models.set_flag_model("Shockwave Slam", "Jagged Ground Eruption", sets)
    assert mods == []  # the headline is PoB's own, with the flag
    (note,) = notes
    assert note["confidence"] == "MEDIUM" and "R59-3" in note["basis"]
    assert "88.4 DPS" in note["value"] and "x2.000" in note["value"]
    assert "Row: not melee: 44.2 DPS" in note["basis"] and "44.2" in note["play"]


def test_no_note_for_another_set_or_skill():
    sets = [{"label": "Slam", "TotalDPS": 24.5}]
    assert models.set_flag_model("Shockwave Slam", "Slam", sets) == ([], [])
    assert models.set_flag_model("Rolling Slam", "Jagged Ground Eruption", sets) == ([], [])
    # a set our reading didn't touch (no addedFlags from the bridge) gives no note
    plain = [{"label": "Jagged Ground Eruption", "TotalDPS": 44.2}]
    assert models.set_flag_model("Shockwave Slam", "Jagged Ground Eruption", plain) == ([], [])


def test_melee_lines_reach_the_eruption_set_and_the_row_reads_without_them(engine):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Shockwave Totem", ascendancies=["Warbringer"], level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Shockwave Totem"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    group = build.main_group()

    def eruption():
        sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
        return next(s for s in sets if s["label"] == "Jagged Ground Eruption")

    bare = eruption()
    assert bare["addedFlags"] == "melee"
    assert bare["TotalDPSWithoutAdded"] == pytest.approx(bare["TotalDPS"])  # no Melee-only line: no difference
    build.call("setConfig", vars={"customMods": MELEE_LINE}, fields=[])
    try:
        got = eruption()
        assert got["TotalDPS"] > 1.5 * bare["TotalDPS"]  # the Melee line now reaches the set
        assert got["TotalDPSWithoutAdded"] == pytest.approx(bare["TotalDPS"])  # the row: as PoB scored it before
    finally:
        build.call("setConfig", vars={"customMods": "nil"}, fields=[])


# (GEM43 turn 2, the manager 08:40) Cursed Ground's "[Limit] 2 areas" with the build's Limit sources (MEDIUM-LOW)
from builder.search.models import Skill  # noqa: E402


def test_a_raised_curse_area_limit_lets_hayoxis_areas_erupt_more_often_with_the_two_area_row():
    despair = Skill("Despair", group=4, spell=True, cast_time=0.5, cooldown=2.0)
    blast = Skill(models.ANNIHILATION, group=4, spell=True, triggered=True)
    main = Skill("Spark", group=1, spell=True)
    by_hand = models.CurseUse("Despair", 1.0, "cast by hand every 8 s", True, 8.0)
    plain = models.annihilation_model([despair, blast, main], [by_hand], main)
    same = models.annihilation_model([despair, blast, main], [by_hand], main, {"Despair": (2.0, models.CURSE_AREAS_WHY)})
    assert plain == same  # the support's own 2: nothing changes
    mods, notes, _ = models.annihilation_model([despair, blast, main], [by_hand], main,
                                               {"Despair": (3.0, models.CURSE_AREAS_WHY)})
    rate = next(m for m in mods if m.get("key") == "triggerRate")
    assert rate["value"] == pytest.approx(3 / 7 * 6, abs=1e-3)  # an area every 7/3 s (the 2 s cooldown allows it)
    assert notes[0]["confidence"] == "MEDIUM-LOW" and "[Limit]" in notes[0]["basis"]
    assert "row: the support's 2 areas, 0.286/s" in notes[0]["basis"] and "0.286" in notes[0]["play"]
    # four areas: the curse's 2 s cooldown binds (an area every 2 s, not 1.75)
    four = models.annihilation_model([despair, blast, main], [by_hand], main, {"Despair": (4.0, models.CURSE_AREAS_WHY)})
    assert four[1][0]["value"] == pytest.approx(0.5)


def test_the_toad_rows_take_the_raised_area_limit():
    toad = Skill(models.DARK_CONSEQUENCES, group=3, spell=True, triggered=True)
    despair = Skill("Despair", group=3, spell=True)
    curse = models.CurseUse("Despair", 1.0, "cast by hand every 8 s", True, 8.0)
    plain = models.toad_model([toad, despair], [curse])
    raised = models.toad_model([toad, despair], [curse], {"Despair": (3.0, models.CURSE_AREAS_WHY)})
    assert plain[0] == raised[0]  # the headline is one area's, as before
    assert "3 areas kept on the boss" in raised[1][0]["basis"] and "[Limit]" in raised[1][0]["basis"]
    assert "2 areas kept on the boss" in plain[1][0]["basis"]


def test_an_item_limit_line_raises_cursed_grounds_areas(engine):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Despair 20/0 1\nHayoxi's Fulmination 1/0 1")

    def rate():
        search.assumptions.apply()
        search.assumptions.apply()
        return next(m for m in search.assumptions.mods
                    if m.get("skill") == models.ANNIHILATION and m.get("key") == "triggerRate")["value"]

    assert rate() == pytest.approx(2 / 7 * 6, abs=1e-3)
    # an item line (the models read the build's source lines: the tree and items, not PoB's custom-mods box)
    build.call("equip", slot="Belt", raw=chr(10).join(["Rarity: RARE", "Probe Cord", "Heavy Belt",
                                                         "Skills have +1 to Limit"]), fields=[])
    assert rate() == pytest.approx(3 / 7 * 6, abs=1e-3)  # probe scratch/gem43/probe_curse.log: 1.714 -> 2.571

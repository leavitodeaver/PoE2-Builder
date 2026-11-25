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

"""GEM41: COV57's gem-stat rows. Shockwave Totem is flagged is_melee_attack_totem by the game (its own skill types lack
Attack and Melee), so "Melee Attack Skills have +N to maximum number of Summoned Totems" (Ancestral Artifice) counts
toward its Totem Limit (MEDIUM reading), which PoB never applied."""
import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import models
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.models import Skill
from builder.search.skeletons import enumerate_skeletons

MELEE_LINE = "Melee Attack Skills have +1 to maximum number of Summoned Totems"


def test_totem_count_names_the_melee_attack_totem_lines_and_the_count_without_them():
    slam = Skill("Shockwave Slam", 1, attack=True, melee=True)
    totem = Skill("Shockwave Totem", 1, totem_limit=2, totem_limit_own=1, melee_totem_added=1)
    mods, notes = models.totem_count_model([slam, totem], slam)
    assert mods[0]["scale"] == pytest.approx(100.0)  # (2 totems: +100% speed on the slam)
    row = next(n for n in notes if "Melee Attack totem lines" in n["label"])
    assert row["value"] == "2 with / 1 without" and "is_melee_attack_totem" in row["basis"]
    plain = Skill("Shockwave Totem", 1, totem_limit=2, totem_limit_own=1)
    assert not any("Melee Attack" in n["label"] for n in models.totem_count_model([slam, plain], slam)[1])


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_the_bridge_adds_melee_attack_totem_lines_to_shockwave_totems_limit(engine):
    payload = make_payload(BuildRequest(main_skill="Shockwave Totem", ascendancies=["Warbringer"], level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Shockwave Totem"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build

    def totem():
        return next(s for s in build.call("activeSkills")["skills"] if s["name"] == "Shockwave Totem")

    bare = totem()
    assert not bare.get("meleeAttackTotemAdded")
    build.call("setConfig", vars={"customMods": MELEE_LINE}, fields=[])
    try:
        got = totem()
        assert got["meleeAttackTotemAdded"] == 1
        assert got["totemLimit"] == bare["totemLimit"] + 1
    finally:
        build.call("setConfig", vars={"customMods": "nil"}, fields=[])


def test_limit_lines_reach_a_skill_by_its_types():
    """GEM41 (the manager's 21:27 item 1): each "+N to Limit" line by the skill types it needs (LIMIT_LINES)."""
    from builder.search import assumptions

    lines = ["Skills have +1 to Limit", "Fissure Skills have +1 to Limit", "+2 to Limit for Elemental Skills",
             "Storm Skills have +1 to Limit", "Orb Skills have +1 to Limit"]
    assert assumptions.limit_line_adds(lines, {"Cold", "Storm", "Limit"}) == 4  # (Icestorm: all, elemental, storm)
    assert assumptions.limit_line_adds(lines, {"Lightning", "Orb", "Limit"}) == 4  # (Galvanic Field)
    assert assumptions.limit_line_adds(lines, {"CreatesFissure"}) == 2  # (Molten Crash: all, fissure)
    assert assumptions.limit_line_adds(["Fissure Skills have +1 to Limit"], {"Fire", "Storm"}) == 0  # (GEM40's slip)


def test_forge_hammers_fissure_cap_is_the_builds_limit():
    hammer = Skill("Forge Hammer", 1, attack=True, melee=True)
    sets = [{"label": "Impact", "TotalDPS": 60.0}, {"label": "Fissures", "TotalDPS": 50.0}]
    args = (hammer, "Fissures", sets, {models.FISSURE_BASE_STAT: 5}, {models.FISSURE_EXTRA_STAT: 9}, [], 0.4)
    base, _ = models.fissure_count_model(*args, det_share=1.0)
    raised, _ = models.fissure_count_model(*args, det_share=1.0, cap=11.0)
    assert base[0]["value"] == pytest.approx(100 * (50.0 / 110.0) * 9.0, abs=0.01)  # (held to 10)
    assert raised[0]["value"] == pytest.approx(100 * (50.0 / 110.0) * 10.0, abs=0.01)  # (held to 11)
    assert models.FISSURE_LIMIT_STATS["Molten Crash"] == ("molten_crash_max_number_of_fissures", 8.0)


def test_icestorms_storms_row_reads_the_builds_limit():
    ice = Skill(models.ICESTORM, 1, spell=True)
    three = models.icestorm_overlap_note(ice, 1.0, 8.0)
    five = models.icestorm_overlap_note(ice, 1.0, 8.0, limit=5)
    assert "3.00 storms" in three[0]["label"] and "5.00 storms" in five[0]["label"]

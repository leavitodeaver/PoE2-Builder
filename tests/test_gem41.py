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

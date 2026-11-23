"""SEARCHB4 (22 Nov, the manager's 19:50 ruling): a gem what-if keeps an item's Spirit.

bridge.gemDeltas ran its what-ifs with PoB's `requirementsItems` fast path. PoB's misc calculator then reuses its
environment from the second call on and skips CalcSetup's item block (1182-1754). A Sceptre's Spirit
(`item.spiritValue`, CalcSetup 1692) is written into env.modDB inside that block, and env.modDB is wiped every call
(wipeEnv, 470-472). So every gem what-if after a calculator's first one read a Sceptre build 100 Spirit short. Build S2
(POE2T6297): Spirit 200 committed, 100 in the what-ifs, a false hard Spirit deficit on nearly every support and
Spirit-skill candidate, so the support rounds ranked them blind. The fix drops requirementsItems (bridge.lua,
gemDeltas). The Charm limit (CalcSetup 1689) is lost the same way but isn't checked here: PoB writes output.CharmLimit
only in breakdown mode (CalcPerform 1781-1787), never in a what-if.
"""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.search.context import normal_item


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_a_gem_what_if_keeps_a_sceptres_spirit_after_the_first_call(engine):
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", raw=normal_item({"name": "Withered Wand"}), fields=[])
    engine.call("equip", slot="Weapon 2", raw=normal_item({"name": "Shrine Sceptre (Purity of Fire)"}), fields=[])
    engine.call("addSkillGroup", text="Spark 20/0  1", main=True, fullDPS=True)
    engine.call("addSkillGroup", text="Herald of Ice 20/0  1")
    committed = engine.call("recalc", fields=["Spirit", "SpiritUnreserved"])["stats"]
    assert committed["Spirit"] >= 100  # (the Sceptre's 100 is in the committed build)
    herald = next(g for g in engine.call("groups")["groups"] if g["gems"] and g["gems"][0]["name"] == "Herald of Ice")
    cases = [{"group": herald["index"], "slot": len(herald["gems"]) + 1, "gem": name}
             for name in ("Precision I", "Clarity I")]
    for _ in range(2):  # (the second call is the one the fast path got wrong)
        results = engine.call("gemDeltas", cases=cases, fields=["Spirit", "SpiritUnreserved"], skipEHP=True)["results"]
        assert [r["Spirit"] for r in results] == [committed["Spirit"]] * len(cases)

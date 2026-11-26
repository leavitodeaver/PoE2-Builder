"""GEM46: a line for some of a skill's stat sets is tagged to those sets (engine/bridge.lua's BridgeStatSet<label>)."""
import pytest

from builder.search import models


def test_a_stat_set_line_carries_its_sets():
    mod = models.line("1% less Damage", 100, skill="Tempest Bell", sets=["Tempest Bell"])
    assert mod["statSets"] == ["Tempest Bell"] and mod["skill"] == "Tempest Bell"
    assert "statSets" not in models.line("1% less Damage", 100, skill="Tempest Bell")


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.data import GameData
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    eng = PobEngine()
    yield eng
    eng.close()
    if enabled:
        faulthandler.enable()


def test_the_base_sets_less_damage_reaches_the_base_set_only(engine):
    # (GEM46, MECHB67's POE2T9071) COVC13's "base set deals no damage" 100% less, tagged to the skill's NAME, zeroed
    # Tempest Bell's Impact / Shockwave whenever setRates scored them with the group on its base set (build 20). Tagged
    # to the base set, the damaging sets keep PoB's numbers.
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Tempest Bell"))
    s = Search(engine, None, payload, enumerate_skeletons(Context(payload), ["Tempest Bell"])[0][0].as_dict(), 0)
    s.setup(quests=False)
    index = s.build.main_group()["index"]
    s.build.set_group(index=index, mainActiveSkill=1, statSet=1)  # (the group on its base set, "Tempest Bell")

    def sets(mods):
        engine.call("setAssumptions", mods=mods)
        return {x["label"]: x.get("AverageDamage") or 0 for x in engine.call("setRates", index=index, active=1)["sets"]}
    plain = sets([])
    assert plain["Impact"] > 0 and plain["Tempest Bell"] > 0  # (PoB scores the base set too: the overcount)
    by_name = sets([models.line("1% less Damage", 100, skill="Tempest Bell")])
    assert by_name["Impact"] == 0 and by_name["Tempest Bell"] == 0  # (the old tag: every set zeroed)
    tagged = sets([models.line("1% less Damage", 100, skill="Tempest Bell", sets=["Tempest Bell"])])
    assert tagged["Tempest Bell"] == 0
    for label in ("Impact", "Shockwave"):
        assert tagged[label] == pytest.approx(plain[label]), label
    # (two sets listed: each moves as under a by-name line, the base set not at all; PoB's Impact falls x0.419 under a
    # 50% less, by name too: scratch/gem46/probe_bell.log)
    half = sets([models.line("1% less Damage", 50, skill="Tempest Bell")])
    two = sets([models.line("1% less Damage", 50, skill="Tempest Bell", sets=["Impact", "Shockwave"])])
    assert two["Tempest Bell"] == pytest.approx(plain["Tempest Bell"]) and half["Tempest Bell"] < plain["Tempest Bell"]
    for label in ("Impact", "Shockwave"):
        assert two[label] == pytest.approx(half[label]) and two[label] < plain[label], label
    engine.call("setAssumptions", mods=[])

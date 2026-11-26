"""MECHB66 (MECH-B): the Skeletal Sniper's Gas Arrow Command (PoB scores a minion on its first skill only, so its arrow
and its cloud's Poison were never counted; MECH110's lead on example build 6)."""
import faulthandler

import pytest

from builder.search import models


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


IMPACT = {"AverageDamage": 30000.0, "Cooldown": 3.0, "CastRate": 0.8}
CLOUD = {"PoisonDPS": 9000.0, "PoisonStackPotential": 0.9, "PoisonStacksMax": 1, "PoisonDuration": 2.0, "Duration": 8.0}
BLAST = {"AverageDamage": 150000.0}


def test_the_snipers_gas_arrow_adds_its_hit_and_one_held_poison_a_command_at_its_cooldown():
    """One arrow a press at its cooldown; the cloud keeps the Sniper's one Poison on the boss (PoB's one-Poison DPS =
    PoisonDPS / its stack potential); Persistent Ground II / III stops the explosions (a row shows them) and adds 50% of
    the 8 s base to the cloud; a minion main loses no time; the Sniper's own counted attack pays for the arrow's action."""
    dps, mods, notes = models.sniper_gas_arrow_model(IMPACT, CLOUD, BLAST, 1.0, "test", True, 50.0, 0.0, 0.0)
    one = 9000.0 / 0.9
    assert dps == pytest.approx(30000.0 / 3.0 + one)
    assert models.added_total(mods) == pytest.approx(dps, abs=0.01) and notes[0]["uses"] == pytest.approx(1 / 3.0)
    assert "12.0 s" in notes[0]["basis"] and "would explode" in notes[0]["basis"]
    # without Persistent Ground every cloud explodes (the Ignited boss): the blast counts, the cloud ends sooner
    open_dps = models.sniper_gas_arrow_model(IMPACT, CLOUD, BLAST, 1.0, "test", False, 0.0, 0.0, 0.0)[0]
    assert open_dps == pytest.approx(10000.0 + 50000.0 + one)  # (a 4 s half-life + 2 s Poison still covers 3 s)
    # the press costs a player main's time; the arrow's 1.25 s costs the Sniper's own counted attack
    paid = models.sniper_gas_arrow_model(IMPACT, CLOUD, BLAST, 0.0, "", True, 0.0, 6000.0, 8000.0)[0]
    assert paid == pytest.approx(10000.0 + one - 6000.0 * 1.25 / 3.0 - 8000.0 * 1.25 / 3.0)
    # a net loss isn't pressed
    lost, mods, notes = models.sniper_gas_arrow_model(IMPACT, CLOUD, BLAST, 0.0, "", True, 0.0, 10 ** 6, 0.0)
    assert lost == 0.0 and not mods and "not pressed" in notes[0]["basis"]


def test_the_snipers_cloud_poison_follows_its_limit_and_the_press_rate():
    """With limit 1, held = (cloud life + Poison duration) / the press period, at most 1; a higher limit holds more."""
    poison, held, one = models.sniper_cloud_poison(CLOUD, 2.0, 10.0)
    assert held == pytest.approx(0.4) and poison == pytest.approx(one * 0.4)
    poison, held, _ = models.sniper_cloud_poison({**CLOUD, "PoisonStacksMax": 3, "PoisonStackPotential": 0.3}, 8.0, 2.0)
    assert held == pytest.approx(min(3, models.gas_cloud_poisons(8.0) * 2.0 / 2.0))
    assert models.sniper_gas_arrow_model({}, {}, {}, 0.0, "", False, 0.0, 0.0, 0.0)[0] == 0.0


def test_the_snipers_gas_arrow_sets_come_from_pob(engine):
    """On real PoB: minionSkillHit gives Gas Arrow's Impact hit and cooldown, its Poison Cloud set's Poison and
    duration, and its Explosion; assumptions.sniper_gas_arrow reads Persistent Ground III's stats from the group and adds
    the Command."""
    from builder.request.schema import BuildRequest
    from builder.search import assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    gems = [{"name": "Skeletal Sniper", "support": False, "level": 20, "quality": 0, "enabled": True},
            {"name": "Persistent Ground III", "support": True, "level": 1, "quality": 0, "enabled": True}]
    build.call("setGroup", gems=gems)
    group = next(g for g in build.groups() if any(models.SNIPER in s["name"] for s in g["skills"]))
    n = next(i for i, s in enumerate(group["skills"], start=1) if models.SNIPER in s["name"])
    impact = build.call("minionSkillHit", index=group["index"], active=n, skill=models.SNIPER_GAS_ARROW, statSet=1)
    cloud = build.call("minionSkillHit", index=group["index"], active=n, skill=models.SNIPER_GAS_ARROW, statSet=2)
    print(impact, cloud)
    assert impact.get("name") == "Gas Arrow" and impact.get("AverageHit", 0) > 0, impact
    assert impact["Cooldown"] == pytest.approx(models.SNIPER_COOLDOWN, rel=0.01), impact  # (PoB rounds to its tick)
    assert cloud.get("PoisonDPS", 0) > 0 and cloud.get("Duration", 0) >= models.SNIPER_CLOUD_SECONDS - 0.5, cloud
    dps, mods, notes = assumptions.sniper_gas_arrow(build, None, lambda life: (1.0, "test"))
    print(dps, notes)
    assert notes and "no cloud explodes" in notes[0]["basis"] and "Persistent Ground" in notes[0]["basis"]
    assert dps == 0.0 or models.added_total(mods) == pytest.approx(dps, abs=0.01)


DECOMP_CLOUD = {"Speed": 0.84, "TotalDPS": 2079.0, "PoisonDPS": 494.4, "PoisonStackPotential": 1.68, "PoisonStacksMax": 1,
                "PoisonDuration": 2.0, "Duration": 4.0}


def test_decomposes_cloud_poisons_without_a_hit_and_explodes_only_when_set_off():
    """PoB counts the cloud as a hit of 10% of the corpse's Life plus its Poison; the game's cloud only Poisons ("as
    though dealing"), and its Explosion needs Burning or a Detonator: the entry is scaled to Poison + Explosion x the
    share set off; Persistent Ground II / III stops the explosions (a row shows them); no entry or no uses: nothing."""
    # (PoB's entry is the scored set's hit, 2,079; its Poison is in the Full DoT DPS entry already)
    notes = models.decompose_model(DECOMP_CLOUD, {"TotalDPS": 249.0}, 2079.0, 0.0, "none", False, 0.0)
    assert notes[0]["full_dps"][models.DECOMPOSE] == pytest.approx(0.0, abs=1e-6)
    notes = models.decompose_model(DECOMP_CLOUD, {"TotalDPS": 249.0}, 2079.0, 1.0, "test", False, 0.0)
    assert notes[0]["full_dps"][models.DECOMPOSE] == pytest.approx(249.0 / 2079.0, rel=1e-4)
    notes = models.decompose_model(DECOMP_CLOUD, {"TotalDPS": 249.0}, 2079.0, 1.0, "test", True, 50.0)
    assert notes[0]["full_dps"][models.DECOMPOSE] == pytest.approx(0.0, abs=1e-6)
    # the Explosion set scored: its entry is the Explosion every use, kept at the share set off
    blast = models.decompose_model(DECOMP_CLOUD, {"TotalDPS": 249.0}, 249.0, 0.5, "half", False, 0.0, "Explosion")
    assert blast[0]["full_dps"][models.DECOMPOSE] == pytest.approx(0.5, rel=1e-4)
    assert "would explode" in notes[0]["basis"] and "6.0 s" in notes[0]["basis"]
    assert models.decompose_model(DECOMP_CLOUD, {}, 0.0, 0.0, "", False, 0.0) == []
    assert models.decompose_model({**DECOMP_CLOUD, "Speed": 0.0}, {}, 100.0, 0.0, "", False, 0.0) == []


def test_decompose_reads_its_sets_and_entry_from_pob(engine):
    """On real PoB: a Decompose group in Full DPS; its Poison Cloud set is scored as a hit plus Poison (probe POE2T9050)
    and assumptions.decompose scales its entry down to the Poison (no Burning or Detonator in the build)."""
    from builder.request.schema import BuildRequest
    from builder.search import assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert assumptions.decompose(build, lambda life: (0.0, "none")) == []
    added = build.call("setGroup", gems=[{"name": "Decompose", "support": False, "level": 20, "quality": 0,
                                           "enabled": True}])
    build.call("setGroup", index=added["group"]["index"], fullDPS=True)
    notes = assumptions.decompose(build, lambda life: (0.0, "none"))
    print(notes)
    assert notes and notes[0]["full_dps"][models.DECOMPOSE] < 0.5, notes
    assert "no hit" in notes[0]["basis"]


def test_the_cost_cap_skips_mana_when_pyromantic_pact_pays_costs_with_infernal_flame():
    """(COV58's b15, x0.077 there) A triggered main skill spending more Mana than it earns is capped (R60-4), unless the
    build's "Gain Infernal Flame instead of spending Mana for Skill costs" (Pyromantic Pact) means no Mana is spent."""
    from types import SimpleNamespace

    from builder.search.assumptions import Assumptions

    def fake(lines):
        stats = {"ManaPerSecondCost": 1000.0, "ManaRegenRecovery": 100.0}
        build = SimpleNamespace(stats=stats, call=lambda method, **kw: {"lines": [{"text": t} for t in lines]})
        return SimpleNamespace(build=build)

    main = models.Skill("Comet", group=1, spell=True, triggered=True)
    mods, notes = Assumptions.mana_cap(fake([]), main, {})
    assert mods and mods[0]["value"] == pytest.approx(-90.0), mods  # (100 / 1000: x0.1)
    pact = ["Gain Infernal Flame instead of spending Mana for Skill costs"]
    assert Assumptions.mana_cap(fake(pact), main, {}) == ([], [])

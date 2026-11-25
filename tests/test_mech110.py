"""MECH110 (MECH-A): COV58's offense rows in MECH-A's topic."""
import pytest

from builder.search import models


def test_persistent_ground_ii_iii_keep_clouds_from_exploding_and_lengthen_them():
    # Persistent Ground III's support stats: "50% increased duration of Ground Surfaces" and "cannot be destroyed except
    # by expiring" (a flag, read as 1): a 4 s cloud whose PoB duration is 5 s (25% increased) becomes 5 + 4 x 0.5 = 7 s,
    # added to the increased duration rather than multiplied, and no cloud explodes
    seconds, persists = models.persistent_cloud(5.0, 4.0, {"ground_effect_duration_+%": 50,
                                                           "ground_effects_cannot_trigger": 1})
    assert (seconds, persists) == (7.0, True)
    # Persistent Ground I: the duration only; its clouds still explode
    assert models.persistent_cloud(4.0, 4.0, {"ground_effect_duration_+%": 50}) == (6.0, False)
    # no Persistent Ground: unchanged
    assert models.persistent_cloud(4.0, 4.0, {}) == (4.0, False)


# Esh's Prowess: Shock chance from its minimum and maximum Lightning rolls (bridge shockChancePerHit) ---------------------

ESH_FIELDS = ["ShockChanceOnHit", "ShockChanceOnCrit", "ShockChancePerHit", "CritChance", "ShockLightningMin",
              "ShockLightningMax", "LightningStoredHitMin", "LightningStoredCritMin", "LightningStoredCritMax",
              "EnemyAilmentThreshold"]


@pytest.fixture(scope="module")
def engine():
    """One real PoB engine for the module (test_engine_models.py's fixture)."""
    import faulthandler

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


def esh_expected(s: dict) -> float:
    """The mean of PoB's Shock-chance formula at the Lightning minimum and maximum, each capped at 100%, with the slope
    read off PoB's own average-hit chance (Spark has no base chance to Shock: the formula through 0)."""
    lo, hi = s["ShockLightningMin"], s["ShockLightningMax"]
    k = s["ShockChanceOnHit"] / ((lo + hi) / 2)
    scale = lo / s["LightningStoredHitMin"]
    hit = (min(100.0, k * lo) + min(100.0, k * hi)) / 2
    crit = (min(100.0, k * s["LightningStoredCritMin"] * scale) + min(100.0, k * s["LightningStoredCritMax"] * scale)) / 2
    c = s["CritChance"] / 100
    return hit * (1 - c) + crit * c


def test_eshs_prowess_shock_chance_is_the_mean_of_its_two_rolls(engine):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    group = next(g for g in build.call("groups")["groups"] if g["gems"] and g["gems"][0]["name"] == "Spark")
    gems = [{"name": n["name"], "level": n.get("level") or 20, "quality": n.get("quality") or 0} for n in group["gems"]]
    capped_somewhere = False
    for esh in (False, True):
        build.set_group(index=group["index"], gems=gems + ([{"name": "Esh's Prowess", "level": 1, "quality": 0}]
                                                            if esh else []), main=True)
        for level in (84, 20, 12, 9, 7, 5, 3, 1):
            s = build.call("setConfig", vars={"enemyLevel": level}, fields=ESH_FIELDS)["stats"]
            print("PROBE esh", esh, level, s)
            c = s["CritChance"] / 100
            pob = s["ShockChanceOnHit"] * (1 - c) + s["ShockChanceOnCrit"] * c
            if not esh or not 0 < s["ShockChanceOnHit"] < 100 or s["ShockLightningMax"] <= s["ShockLightningMin"]:
                if not esh:  # without the support: PoB's average-hit reading, unchanged
                    assert s["ShockChancePerHit"] == pytest.approx(pob)
                continue
            assert s["ShockChancePerHit"] == pytest.approx(esh_expected(s), rel=1e-6)
            assert s["ShockChancePerHit"] <= pob + 1e-9  # the cap can only take chance away
            capped_somewhere = capped_somewhere or s["ShockChancePerHit"] < pob - 1.0
    build.call("setConfig", vars={"enemyLevel": 84}, fields=[])
    assert capped_somewhere  # at some enemy level the maximum roll passes 100% and the mean falls below PoB's


# Herald of Plague: Poisons spread from the preset's killed adds, up to the Poison limit -------------------------------

def test_herald_of_plague_spreads_poison_only_from_killed_adds_and_up_to_the_limit():
    herald = [models.Skill("Herald of Plague", 2)]
    base = {"PoisonStacksMax": 1, "PoisonStackPotential": 0.4, "PoisonDPS": 1000.0, "PoisonDuration": 2.0,
            "PoisonChancePerHit": 50.0}
    # a lone boss: no kills, nothing spread (0 with a note, never a flat kill rate)
    mods, notes = models.herald_of_plague_model(herald, {**base, "AddKillsPerSecond": 0.0})
    assert mods == [] and notes[0]["value"] == "0 DPS" and "no enemy dies" in notes[0]["basis"]
    # 0.2 kills a second x 50% Poisoned x 1 s left = 0.1 Poisons on the boss beside PoB's 0.4: x0.5 / 0.4 = +250
    mods, notes = models.herald_of_plague_model(herald, {**base, "AddKillsPerSecond": 0.2})
    assert models.added_total(mods) == pytest.approx(250.0)
    # the boss already at the limit: a spread Poison adds nothing
    mods, notes = models.herald_of_plague_model(herald, {**base, "PoisonStackPotential": 1.0, "AddKillsPerSecond": 0.2})
    assert models.added_total(mods) == pytest.approx(0.0)
    # no Herald of Plague: nothing at all
    assert models.herald_of_plague_model([models.Skill("Spark", 1)], {**base, "AddKillsPerSecond": 0.2}) == ([], [])

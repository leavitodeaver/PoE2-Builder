"""MECH96: the Poison / Bleed base-chance swap lines (Way of the Stonefist's transform of the Desecrated gloves suffixes),
through a real engine (bridge installBaseChanceSwap). Needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

FIELDS = ["PoisonChanceOnHit", "BleedChanceOnHit"]
POISON_FROM_BLEED = "Chance to Poison is calculated from your base chance to inflict Bleeding instead"
BLEED_FROM_POISON = "Chance to inflict Bleeding is calculated from your base chance to Poison instead"


def gloves(*lines: str) -> str:
    return "\n".join(["Rarity: Rare", "Test Gloves", "Fists of Stone", "Item Level: 82", "Implicits: 0", *lines])


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

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


def test_the_swap_lines_read_the_other_ailments_base_chance_and_keep_their_own_increases(engine):
    """Base 10% Poison, 40% Bleed, +50% increased chance to Poison. PoB parses neither swap line, so before MECH96 the
    gloves changed nothing (Poison 15%). "Chance to Poison is calculated from your base chance to inflict Bleeding
    instead": Poison = 40% x 1.5 = 60%, Bleed stays 40%. The mirror: Bleed = 10%, Poison stays 15%. Both: each reads
    the other's own base (60% / 10%). Without the gloves: back to PoB's own."""
    from builder.search import models

    engine.call("newBuild")
    engine.call("setGroup", gems=[{"name": "Frostbolt", "level": 20, "quality": 0}], main=True)
    mods = [models.raw_mod("PoisonChance", "BASE", 10), models.raw_mod("BleedChance", "BASE", 40),
            models.raw_mod("PoisonChance", "INC", 50)]
    engine.call("setAssumptions", mods=mods, fields=[])
    got = {}
    for name, lines in (("plain", ("+10 to Strength",)), ("poison", (POISON_FROM_BLEED,)),
                        ("bleed", (BLEED_FROM_POISON,)), ("both", (POISON_FROM_BLEED, BLEED_FROM_POISON))):
        got[name] = engine.call("equip", slot="Gloves", raw=gloves(*lines), fields=FIELDS)["stats"]
    got["off"] = engine.call("equip", slot="Gloves", raw=None, fields=FIELDS)["stats"]
    print("PROBE base-chance swap", got)
    engine.call("setAssumptions", mods=[], fields=[])
    want = {"plain": (15, 40), "poison": (60, 40), "bleed": (15, 10), "both": (60, 10), "off": (15, 40)}
    for name, (poison, bleed) in want.items():
        assert got[name]["PoisonChanceOnHit"] == pytest.approx(poison, rel=1e-6), name
        assert got[name]["BleedChanceOnHit"] == pytest.approx(bleed, rel=1e-6), name


MINION_MADNESS = "Minions have 20% chance to inflict Gruelling Madness on Hit"


def test_minions_gruelling_madness_stacks_at_the_builds_minion_hits():
    """MECH96: the minion staff's "Minions have N% chance to inflict Gruelling Madness on Hit" (read by nothing before):
    10 held at one inflict per 2 s or more (20% x 3 hits/s = 0.6/s), else the mean of one stack held 2 s per inflict
    (20% x 1 hit/s: 0.4); none without minions hitting. The weapon and Presence sources keep their old counts."""
    from builder.search import models

    assert models.madness_stacks([MINION_MADNESS], {"_minion_hits": 3.0})[0] == 10
    assert models.madness_stacks([MINION_MADNESS], {"_minion_hits": 1.0})[0] == pytest.approx(0.4)
    assert models.madness_stacks([MINION_MADNESS], {"_minion_hits": 0.0})[0] == 0
    # (no _minion_hits: the main skill's minions from PoB's Minion.Speed x hit chance)
    assert models.madness_stacks([MINION_MADNESS], {"Minion.Speed": 4.0, "Minion.HitChance": 50.0})[0] == pytest.approx(0.8)
    weapon = ["Hits with this Weapon inflict 3 Gruelling Madness"]
    assert models.madness_stacks(weapon, {"Speed": 2.0, "HitChance": 100.0}, attack=True)[0] == 10
    assert models.madness_stacks(weapon, {"Speed": 0.25, "HitChance": 100.0}, attack=True)[0] == 3
    presence = ["Enemies in your Presence gain 1 Gruelling Madness each second"]
    assert models.madness_stacks(presence, {}, in_presence=False)[0] == 0


def test_gruelling_madness_slowing_potency_mod():
    """+10% Slowing Potency per stack past the first: 10 stacks +90%; under 2 stacks nothing (the first slows movement
    only); no Madness line, no mod. The Presence source needs the boss in your Presence."""
    from builder.search import models

    presence = ["Enemies in your Presence gain 1 Gruelling Madness each second"]
    mods, notes = models.madness_slow_mods(presence, {"PresenceRadius": 80.0})
    assert [(m["name"], m["type"], m["value"]) for m in mods] == [(models.MADNESS_POTENCY_MOD, "INC", 90.0)]
    assert models.madness_slow_mods(presence, {"PresenceRadius": 20.0})[0] == []  # the boss 5 m away, Presence 2 m
    assert models.madness_slow_mods([MINION_MADNESS], {"_minion_hits": 1.0})[0] == []  # 0.4 stacks
    assert models.madness_slow_mods(["+10 to Strength"], {}) == ([], [])


def test_gruelling_madness_potency_scales_the_bosss_chill_slow(engine):
    """Through PoB (bridge installBossSlow): a 30% Chill on the default Unique boss slows it by 15% (its 50% less);
    with +90% Slowing Potency (10 Gruelling Madness) by 30% x 0.5 x 1.9 = 28.5%."""
    from builder.search import bosses, models

    slow_taken = bosses.PRESETS[bosses.DEFAULT].slow_taken
    engine.call("newBuild")
    engine.call("setAssumptions", mods=[], fields=[])
    plain = engine.call("setConfig", vars={"conditionEnemyChilled": False}, fields=["enemySkillTime"])["stats"][
        "enemySkillTime"]
    engine.call("setConfig", vars={"conditionEnemyChilled": True, "conditionEnemyChilledEffect": 30})
    got = {}
    for name, value in (("chill", 0), ("madness", 90)):
        mods = [models.raw_mod(models.MADNESS_POTENCY_MOD, "INC", value)] if value else []
        engine.call("setAssumptions", mods=mods, fields=[])
        got[name] = engine.call("setConfig", vars={}, fields=["enemySkillTime"])["stats"]["enemySkillTime"]
    print("PROBE madness potency", plain, got)
    engine.call("setAssumptions", mods=[], fields=[])
    engine.call("setConfig", vars={"conditionEnemyChilled": False, "conditionEnemyChilledEffect": False})
    assert got["chill"] == pytest.approx(plain / (1 - 0.30 * slow_taken), rel=1e-3)
    assert got["madness"] == pytest.approx(plain / (1 - 0.30 * slow_taken * 1.9), rel=1e-3)


def test_a_warcrys_use_ceiling_is_its_cast_rate_under_its_cooldown():
    """MECH96 (MECHB47's castRate): a Warcry row's most uses a second is 1 / WarcryCastTime, not PoB's Speed (which
    counts the action speed twice), still capped by its cooldown; other rows keep PoB's Speed; never below it."""
    from builder.search import conditions

    cry = {"speed": 0.334, "castRate": 1 / 1.547, "warcry": True}
    assert conditions.use_ceiling(cry) == pytest.approx(1 / 1.547)
    assert conditions.use_ceiling({**cry, "cooldown": 4.0}) == pytest.approx(0.334)  # PoB's capped Speed is higher
    assert conditions.use_ceiling({**cry, "cooldown": 2.0}) == pytest.approx(0.5)
    assert conditions.use_ceiling({**cry, "warcry": None}) == pytest.approx(0.334)
    each, _, _ = conditions.skill_use_rate({**cry, "cooldown": 2.0})
    assert each == pytest.approx(0.5)  # was min(0.5, 0.334)


def test_fear_incarnate_counts_at_the_presets_add_culls():
    """MECH96 (kw-fear-incarnate.md): Horror's Flight's "Gain 1 Fear Incarnate when you Cull a target": held stacks =
    min(20, N x add Culls/s x 10 s), each +10% Culling Strike Threshold (PoB's CullPercent INC); the Stonefist form's
    Fear Overwhelming +5% Area of Effect for Attacks each; 0 with a note against a boss with no killable adds."""
    from builder.search import models

    line = "Gain 1 Fear Incarnate when you Cull a target"
    mods, notes = models.fear_mods([line], {models.CULLS_STAT: 0.1})
    assert [(m["name"], m["type"], m["value"]) for m in mods] == [("CullPercent", "INC", pytest.approx(10.0))]
    mods, _ = models.fear_mods([line], {models.CULLS_STAT: 5.0})
    assert mods[0]["value"] == pytest.approx(200.0)  # held at 20
    mods, notes = models.fear_mods([line], {models.CULLS_STAT: 0.0})
    assert mods == [] and notes[0]["value"] == "0"
    mods, _ = models.fear_mods(["Gain 1 Fear Overwhelming when you Cull a target"], {models.CULLS_STAT: 0.2})
    assert [(m["name"], m["value"]) for m in mods] == [("AreaOfEffect", pytest.approx(10.0))]
    assert models.fear_mods(["+10 to Strength"], {models.CULLS_STAT: 1.0}) == ([], [])

"""MECH94 (MECH-A): Slow Magnitude ("Debuffs you inflict have N% increased Slow Magnitude"; PoB parses none of it, so
the boss's Slow from the build's Chill / Temporal Chains ignored it: an UNDERCOUNT of the boss's Slow, defence only)."""
import faulthandler

import pytest


def test_slow_magnitude_lines_sum_to_one_pob_mod():
    from builder.search import models

    mods, notes = models.slow_magnitude_mods([
        "Debuffs you inflict have 20% increased Slow Magnitude",
        "Debuffs you inflict have 5% reduced Slow Magnitude",
        "Debuffs inflicted by Hazards have 30% increased Slow Magnitude",  # (Hazards' Debuffs only: not read)
        "Debuffs inflicted with Supported Skills have 25% increased Slow Magnitude"])  # (one skill's: not read)
    assert [(m["name"], m["type"], m["value"]) for m in mods] == [(models.SLOW_MAGNITUDE_MOD, "INC", 15.0)]
    assert notes[0]["confidence"] == "MEDIUM" and "UNVERIFIED" in notes[0]["basis"]
    assert models.slow_magnitude_mods(["+10% to Fire Resistance"]) == ([], [])


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


def test_slow_magnitude_scales_the_boss_chill_up_to_its_maximum(engine):
    """A 30% Chill on the default (Unique) boss: 15% slower (MECHD2's half). +50% Slow Magnitude: a 45% Chill slow,
    22.5% slower; +100%: 60%, held at the Chill's 50% maximum (UNVERIFIED past it), 25% slower."""
    from builder.search import bosses, models

    slow_taken = bosses.PRESETS[bosses.DEFAULT].slow_taken
    engine.call("newBuild")
    plain = engine.call("setConfig", vars={"conditionEnemyChilled": False}, fields=["enemySkillTime"])["stats"]
    engine.call("setConfig", vars={"conditionEnemyChilled": True, "conditionEnemyChilledEffect": 30})
    got = {}
    for inc in (0, 50, 100):
        mods = models.slow_magnitude_mods([f"Debuffs you inflict have {inc}% increased Slow Magnitude"])[0]
        engine.call("setAssumptions", mods=mods, fields=[])
        got[inc] = engine.call("setConfig", vars={}, fields=["enemySkillTime"])["stats"]["enemySkillTime"]
    print("PROBE slow magnitude", plain["enemySkillTime"], got)
    for inc, chill in ((0, 0.30), (50, 0.45), (100, 0.50)):
        assert got[inc] == pytest.approx(plain["enemySkillTime"] / (1 - chill * slow_taken), rel=1e-3)
    engine.call("setAssumptions", mods=[], fields=[])
    engine.call("setConfig", vars={"conditionEnemyChilled": False, "conditionEnemyChilledEffect": False})


def test_snap_reads_the_boss_ailments_at_the_main_skills_share_of_the_fight():
    """snap_stats (Snap's chance to find an ailment to Consume, from the main skill's hits) carries the main skill's share
    of the fight (assumptions.main_time_share), so its Ignite / Shock / Freeze readers take the hits at that share (an
    OVERCOUNT for builds pressing side skills before: PoB's full Speed)."""
    from types import SimpleNamespace

    from builder.search import assumptions

    class Build:
        objective = SimpleNamespace(press_scales={"Fireball": 0.5}, busy=lambda: 0.0)

        def main_group(self):
            return {"index": 1, "mainActiveSkill": 1, "skills": [{"name": "Fireball"}]}

        def call(self, name, **_):
            return {"Speed": 0.4, "HitChance": 100.0, "IgniteChancePerHit": 20.0, "IgniteDuration": 4.0}

    stats = assumptions.snap_stats(Build())
    assert stats["_main_share"] == 0.5
    full = {k: v for k, v in stats.items() if k != "_main_share"}
    half = {**full, "Speed": 0.2}
    from builder.search.conditions import ailment_uptime

    assert ailment_uptime(stats, "Ignite") == ailment_uptime(half, "Ignite") < ailment_uptime(full, "Ignite")


def test_a_pressed_cooldown_skill_takes_its_cast_not_its_cooldown_from_the_main_skill():
    """PoB's Time is 1 / Speed after the cooldown cap, so a cooldown-capped row's `time` is the whole interval (probe
    POE2T5059: Frost Bomb, 6.006 s cooldown, 0.8 s cast, time 6.006): a press takes the cast time, not 6 s."""
    from builder.search import models

    bomb = {"index": 2, "name": "Frost Bomb", "speed": 1 / 6.006, "cooldown": 6.006, "castTime": 0.8, "time": 6.006,
            "repeats": 1}
    assert models.press_action(bomb) == pytest.approx(0.8)
    scales, _ = models.press_model([{"index": 1, "name": "Spark", "speed": 0.74}, bomb], 1, {2})
    assert scales["Spark"] == pytest.approx(1 - 0.8 / 6.006)
    assert models.press_action({**bomb, "time": 0.4}) == pytest.approx(0.4)  # (a HitTime shorter than the interval)
    assert models.press_action({**bomb, "cooldown": None}) == pytest.approx(6.006)  # (no cap: 1 / speed)

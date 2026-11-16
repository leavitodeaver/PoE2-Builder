"""Olrovasara (COV35, PATCH_CHECK G3-3): "On Hitting an enemy, gains maximum added Lightning damage equal to the enemy's
Power for 20 seconds, up to a total of 500" (game stat ..._equal_to_total_monster_power_of_enemies_hit_in_past_20_seconds_
up_to_X). PoB parses none of it; the ledger counts it at EnemyPowerHit20s: the boss's Power (20, more with the build's
Power lines), plus the adds the build kills in 20 s at Power 1 each, capped at 500; 0 with no hits."""
from types import SimpleNamespace

from builder.ledger import translate
from builder.search.assumptions import Assumptions


class FakeBuild:
    def __init__(self, speed, kills=None, texts=()):
        self.speed, self.kills, self.texts = speed, kills, list(texts)

    def call(self, name, **_):
        if name == "stats":
            return {"Speed": self.speed, "HitChance": 100, "PresenceRadius": 40}
        if name == "sourceLines":
            return {"lines": [{"source": "item", "text": t} for t in self.texts]}
        if name == "getConfig":
            return {"input": {}}
        raise AssertionError(name)


def count(build, cap=500.0):
    a = Assumptions.__new__(Assumptions)
    a.build = build
    return a.enemy_power_hit_count(20.0, cap, "Boneshatter", {"Attack", "Melee"})


def test_a_lone_boss_gives_its_power():
    power, how = count(FakeBuild(1.5))
    assert power == 20.0 and "lone boss" in how


def test_no_hits_count_nothing():
    assert count(FakeBuild(0.0))[0] == 0.0


def test_the_adds_killed_add_their_power_up_to_the_cap():
    power, how = count(FakeBuild(1.5, kills=(0.5, 0.5, "preset")))
    assert power == 20.0 + 0.5 * 20 * 1.0 and "adds" in how
    assert count(FakeBuild(1.5, kills=(50.0, 50.0, "preset")))[0] == 500.0


def test_the_lines_reach_the_ledger():
    first = translate("On Hitting an enemy, gains maximum added Lightning damage equal to")
    second = translate("the enemy's Power for 20 seconds, up to a total of 500")
    assert first and first[1] == [] and first[0].on_items
    assert second and second[1][0].startswith("share:EnemyPowerHit20s ") and second[0].on_items

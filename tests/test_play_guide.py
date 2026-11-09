"""The "How to play it" guide (builder/search/play_guide.py) on a fake build: no PoB needed."""

from types import SimpleNamespace

from pytest import approx

from builder.report.checklist import checklist
from builder.search import play_guide


class FakeBuild:
    """DPS from the config: x1.25 at 2 m (Presence), x1.10 standing still, x1.50 on a Low Life boss."""

    def __init__(self, config):
        self.config = dict(config)
        self.objective = SimpleNamespace(dps=lambda stats: stats["dps"])

    @property
    def stats(self):
        dps = 1000.0
        if self.config.get("enemyDistance") == 20:
            dps *= 1.25
        if self.config.get("conditionStationary"):
            dps *= 1.10
        if self.config.get("conditionEnemyLowLife"):
            dps *= 1.50
        if self.config.get("conditionBeenHitRecently"):
            dps *= 1.001  # below MIN_CHANGE: no row
        return {"dps": dps}

    def set_config(self, values):
        self.config.update(values)

    def call(self, method, **kwargs):
        assert method == "getConfig"
        return {"input": dict(self.config)}


def search_with(config, active):
    build = FakeBuild(config)
    return SimpleNamespace(build=build, conditions=SimpleNamespace(active=active),
                           assumptions=SimpleNamespace(apply=lambda: False))


def test_rows_for_player_and_fight_conditions_and_build_left_as_it_was():
    active = {"enemyDistance": {"label": "Boss distance", "value": 20},
              "conditionBeenHitRecently": {"label": "Been hit", "value": True},
              "conditionEnemyShocked": {"label": "Shocked", "value": True}}  # build-applied: no row
    search = search_with({"enemyDistance": 20, "conditionBeenHitRecently": True, "conditionEnemyShocked": True}, active)
    rows = play_guide.guide(search)
    by_var = {r["var"]: r for r in rows}
    assert set(by_var) == {"enemyDistance", "conditionStationary", "conditionEnemyLowLife"}
    distance = by_var["enemyDistance"]
    assert distance["counted"] and distance["kind"] == "player"
    assert distance["dps_with"] == approx(1251.25) and distance["dps_without"] == approx(1001) and "at 5 m instead" in distance["label"]
    low = by_var["conditionEnemyLowLife"]
    assert not low["counted"] and low["kind"] == "fight" and low["dps_with"] == approx(1876.875) and low["dps_without"] == approx(1251.25)
    assert rows[0]["var"] == "conditionEnemyLowLife"  # largest change first
    assert search.build.config["enemyDistance"] == 20 and not search.build.config["conditionStationary"]
    items = [i for i in checklist({"play_guide": rows}) if i["kind"] == "how to play it *"]
    assert len(items) == 3 and all(i["title"].endswith("*") for i in items)


def test_every_classified_condition_is_known_and_build_is_the_default():
    assert play_guide.kind("conditionEnemyIgnited") == play_guide.BUILD
    assert play_guide.kind("enemyDistance") == play_guide.PLAYER

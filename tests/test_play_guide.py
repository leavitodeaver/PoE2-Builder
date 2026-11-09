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
    assert distance["play"] == "stay within 2 m of the boss"
    low = by_var["conditionEnemyLowLife"]
    assert not low["counted"] and low["kind"] == "fight" and low["dps_with"] == approx(1876.875) and low["dps_without"] == approx(1251.25)
    assert rows[0]["var"] == "conditionEnemyLowLife"  # largest change first
    assert search.build.config["enemyDistance"] == 20 and not search.build.config["conditionStationary"]
    items = [i for i in checklist({"play_guide": rows}) if i["kind"] == "how to play it *"]
    assert len(items) == 3 and all(i["title"].endswith("*") for i in items)


def test_every_classified_condition_is_known_and_build_is_the_default():
    assert play_guide.kind("conditionEnemyIgnited") == play_guide.BUILD
    assert play_guide.kind("enemyDistance") == play_guide.PLAYER


def test_distance_candidates_follow_the_build_lines():
    from builder.search.conditions import distance_candidates

    assert distance_candidates(["10% increased Spell Damage"], {"Spell"}) == []
    assert distance_candidates(["Enemies in your Presence have -10% to Fire Resistance"], {"Spell"}) == [5.0, 2.0]
    assert distance_candidates(["20% more Damage against Enemies further than 6m"], {"Spell"}) == [5.0, 2.0, 6.5]
    assert distance_candidates(["Enemies in your Presence are Blinded"], {"Melee", "Attack"}) == []  # already close


class FakeObjective:
    def __init__(self):
        self.life_factor = 1.0

    def dps(self, stats):
        return stats["dps"] * self.life_factor

    def score(self, stats):
        return self.dps(stats)


class FakeSearchBuild(FakeBuild):
    def __init__(self, config, lines):
        super().__init__(config)
        self.objective = FakeObjective()
        self.lines = lines
        self.score = 0.0

    def call(self, method, **kwargs):
        if method == "sourceLines":
            return {"lines": [{"source": "tree", "text": t} for t in self.lines]}
        return super().call(method, **kwargs)


def fake_search(lines, notes=()):
    from builder.search.inner import Search

    search = SimpleNamespace(build=FakeSearchBuild({}, lines), play_notes={},
                             assumptions=SimpleNamespace(apply=lambda: False, active=list(notes)),
                             conditions=SimpleNamespace(stationary_off=None, rolling=0.0, active={}), settle=lambda: False)
    for name in ("weigh_life", "choose_stationary", "rolling_share"):
        setattr(search, name, getattr(Search, name).__get__(search))
    return search


def test_low_life_is_weighted_by_its_share_of_the_boss_life():
    search = fake_search(["30% more Damage against Enemies on Low Life"])
    search.weigh_life()
    # 1000 above 35%, 1500 below: 1 / (0.65 + 0.35 / 1.5)
    assert search.build.objective.life_factor == approx(1 / (0.65 + 0.35 / 1.5))
    assert search.build.score == approx(1000 / (0.65 + 0.35 / 1.5))
    assert not search.build.config["conditionEnemyLowLife"] and "low_life" in search.play_notes
    rows = play_guide.guide(SimpleNamespace(build=search.build, conditions=SimpleNamespace(active={}),
                                            assumptions=search.assumptions))
    low = next(r for r in rows if r["var"] == "conditionEnemyLowLife")
    assert low["counted"] and low["dps_with"] == approx(1500) and low["dps_without"] == approx(1000)
    assert search.build.objective.life_factor == approx(1 / (0.65 + 0.35 / 1.5))  # put back after the trials
    plain = fake_search(["10% increased Spell Damage"])
    plain.weigh_life()
    assert plain.build.objective.life_factor == 1.0 and not plain.play_notes


def test_dodge_rolls_keep_a_build_from_counting_standing_still():
    rolling = fake_search(["20% more Damage while Stationary"],
                          [{"label": "Time spent dodge rolling (Spark)", "value": "37.5% less Attack and Cast Speed"}])
    assert rolling.rolling_share() == approx(0.375)
    rolling.choose_stationary()
    assert rolling.conditions.stationary_off == "dodge rolls" and "dodge rolls 38%" in rolling.play_notes["stationary"]["basis"]


def test_standing_still_is_on_for_a_build_with_stationary_lines():
    from builder.search.conditions import _standing_still

    assert _standing_still(["20% more Damage while Stationary"], {})[0] is True
    assert _standing_still(["20% more Damage while Stationary"], {"_stationary_off": "dodge rolls"})[0] == 0
    assert _standing_still(["10% increased Spell Damage"], {})[0] == 0

"""GEM48: Frost Bomb cast at the boss or away from it (Assumptions.frost_bomb_spot; example build 7's inverted Comet)."""
from types import SimpleNamespace

from builder.search import models
from builder.search.assumptions import Assumptions


class _Build:
    """PoB's DPS stand-in: 100 base, x(1 + Exposure effect x `per`) for Frost Bomb's DebuffEffect MORE (`per` < 0:
    the main hit inverts resistances, so Exposure costs damage)."""

    def __init__(self, per: float, full: bool = False):
        self.per, self.full, self.calls = per, full, []
        self.objective = SimpleNamespace(fields=["FullDPS"], dps=lambda stats: stats["FullDPS"])

    def groups(self):
        return [{"index": 3, "enabled": True, "fullDPS": self.full}]

    def call(self, method, mods=(), fields=()):
        self.calls.append([m.get("value") for m in mods])
        more = sum(m["value"] for m in mods if m.get("name") == "DebuffEffect")
        return {"stats": {"FullDPS": 100.0 * (1 + self.per * (1 + more / 100))}}


def _spot(build, own_more=80.0, previous=None):
    me = SimpleNamespace(build=build, mods=previous)
    own = [models.raw_mod("DebuffEffect", "MORE", own_more, models.FROST_BOMB, tag=models.FROST_BOMB_ENTRY)]
    other = models.raw_mod("Speed", "MORE", 10)
    bomb, main = models.Skill(models.FROST_BOMB, group=3, spell=True), models.Skill("Comet", group=9, spell=True)
    old = [{"label": "Frost Bomb: its Elemental Exposure on the boss*", "entry": models.FROST_BOMB_ENTRY}, {"label": "x"}]
    return Assumptions.frost_bomb_spot(me, [bomb, main], main, [other] + own, old, set())


def test_an_inverted_main_hit_casts_frost_bomb_away_from_the_boss():
    mods, notes = _spot(_Build(per=-0.07))
    assert [m["value"] for m in mods if m["name"] == "DebuffEffect"] == [-100]
    assert any(m["name"] == "Speed" for m in mods)  # (the other mods kept)
    assert notes[-1]["label"] == models.FROST_BOMB_SPOT_LABEL and notes[-1]["value"] == "away from the boss"
    assert [n["label"] for n in notes] == ["x", models.FROST_BOMB_SPOT_LABEL]  # (its Exposure note dropped)
    assert "Without its Exposure on the boss: 100 DPS" in notes[-1]["play"]


def test_exposure_that_pays_keeps_frost_bomb_at_the_boss():
    mods, notes = _spot(_Build(per=0.07))
    assert [m["value"] for m in mods if m["name"] == "DebuffEffect"] == [80]
    assert notes[-1]["value"] == "at the boss" and len(notes) == 3


def test_the_trial_leaves_frost_bomb_alone_when_its_own_hits_are_scored_or_forbidden():
    build = _Build(per=-0.07, full=True)
    mods, notes = _spot(build)
    assert len(notes) == 2 and build.calls == [] and [m["value"] for m in mods if m["name"] == "DebuffEffect"] == [80]
    me = SimpleNamespace(build=_Build(per=-0.07), mods=None)
    bomb = models.Skill(models.FROST_BOMB, group=3)
    assert Assumptions.frost_bomb_spot(me, [bomb], None, [], [], {models.FROST_BOMB_ENTRY}) == ([], [])


def test_the_trial_scores_on_the_previous_pass_without_its_own_exposure_and_restores_it():
    previous = [models.raw_mod("DebuffEffect", "MORE", 60, models.FROST_BOMB, tag=models.FROST_BOMB_ENTRY),
                models.raw_mod("Speed", "MORE", 10)]
    build = _Build(per=-0.07)
    _spot(build, previous=previous)
    assert build.calls == [[10, 80], [10, -100], [60, 10]]  # (boss, away, then the previous pass's mods put back)


"""GEM39: the build's own Limit and duration on the stat set that holds the Limit stat (COV55's fixed-Limit list; probe
POE2T7318): Storm Lance's spears (4 at 20% quality, not the table's 3), Gas Arrow's / Gas Grenade's Poison Cloud,
Forge Hammer's duration."""
from builder.search import models
from builder.search.assumptions import Assumptions


class FakeBuild:
    def __init__(self, rates: dict):
        self.rates, self.asked = rates, []

    def call(self, method, **kw):
        assert method == "useRates"
        self.asked.append(kw)
        key = (kw.get("statSets") or [None])[0]
        return {"rates": [self.rates.get(key, {})]}


def read(rates, label, sets, limit=6, seconds=4.0):
    a = Assumptions.__new__(Assumptions)
    a.build = FakeBuild(rates)
    return a.set_limit_and_duration({"index": 3, "mainActiveSkill": 1}, sets, label, limit, seconds), a.build.asked


GAS_SETS = [{"index": 1, "label": "Arrow"}, {"index": 2, "label": "Poison Cloud"}, {"index": 3, "label": "Explosion"}]


def test_the_cloud_set_is_read_not_the_scored_one():
    (limit, seconds), asked = read({None: {}, 2: {"instanceLimit": 7, "duration": 2.0}}, "Poison Cloud", GAS_SETS)
    assert (limit, seconds) == (7, 2.0)  # (Overabundance I: +1 Limit, -50% duration)
    assert asked[0]["statSets"] == [2] and asked[0]["indices"] == [3]


def test_no_reading_keeps_the_table():
    (limit, seconds), _ = read({2: {}}, "Poison Cloud", GAS_SETS)
    assert (limit, seconds) == (6, 4.0)


def test_no_label_reads_the_scored_set():
    (limit, seconds), asked = read({None: {"duration": 6.006}}, None, [{"index": 1, "label": "Impact"}], 1, 12.0)
    assert (limit, seconds) == (1, 6.006) and "statSets" not in asked[0]


def test_storm_lance_limit_moves_the_detonated_share():
    """At 0.565 uses a second and 6 s, 3.39 spears would lie there: the table's 3 held them under the build's 4."""
    low = models.detonated_share("Storm Lance", 0.565, 0.05)
    real = models.detonated_share("Storm Lance", 0.565, 0.05, 4, 6.006)
    assert real > low and abs(real / low - 0.565 * 6.006 / 3) < 1e-6


def test_limit_set_skills_are_detonated_or_gas():
    for name in models.LIMIT_SET:
        assert name in models.DETONATED_SETS or name == models.GAS_ARROW

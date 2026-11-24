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


def test_vruun_frozen_locus_reads_the_builds_limit_and_duration():
    """(GEM39) Frozen Locus's crystals in Vruun's Aftermath: its group's useRates Limit and duration, not the fixed 1 / 10 s."""
    from builder.search.models import Skill
    cry = Skill("Seismic Cry", 2, support_stats={models.VRUUN_STAT: 3})
    locus = Skill("Frozen Locus", 3)
    plain = {2: {"name": "Seismic Cry", "speed": 0.1}, 3: {"name": "Frozen Locus", "speed": 1.0}}
    raised = {2: plain[2], 3: dict(plain[3], instanceLimit=2, duration=5.0)}
    _, a = models.vruun_aftermath_model([cry, locus], {}, None, plain)
    _, b = models.vruun_aftermath_model([cry, locus], {}, None, raised)
    assert "1.00 there" in a[0]["basis"] and "2.00 there" in b[0]["basis"]
    assert "2 allowed (the build's Limit and duration)" in b[0]["basis"]


def test_hailstorm_emergency_reload_refills_the_whole_stock_all_empowered():
    """(GEM39, R57-4 MEDIUM) Emergency Reload: the whole stock (55) every 45 s added to the bolts fired, each 31% more,
    under the same 0.2 s limit; GEM10's one-clip reading is a row, and emergency_reload_share gives 0 on Hailstorm."""
    import pytest
    from builder.search.models import Skill
    hail = Skill("Hailstorm Rounds", 1, attack=True)
    stats = {"Speed": 0.634, "ReloadTime": 0.3, "BoltCount": 55, "AreaOfEffectRadius": 10,
             "AreaOfEffectRadiusSecondary": 40}
    plain, _ = models.hailstorm_rounds_model(hail, stats, (0.3, "a"), (1.5, "b"))
    mods, notes = models.hailstorm_rounds_model(hail, stats, (0.3, "a"), (1.5, "b"), refill=(45.0, 31.0, 5.58, "ER"))
    made, extra = 1 / 0.3, 55 / 45.0
    land = (1.3 / 4) ** 2 * 0.5
    want = (made + extra) * land * (1 + 0.31 * extra / (made + extra)) / 0.634
    assert mods[0]["value"] == pytest.approx(100 * (want - 1), abs=0.01)
    assert mods[0]["value"] > plain[0]["value"]
    assert "R57-4" in notes[0]["basis"] and f"x{made * land / 0.634 * 1.0558:.3f}" in notes[0]["basis"]
    assert "Use Emergency Reload right after a volley" in notes[0]["play"]
    er = Skill("Emergency Reload", 2, level_stats={"emergency_reload_damage_+%_final": 31}, cooldown=45.0)
    share, how, _ = models.emergency_reload_share(er, {"BoltCount": 55, "Speed": 0.634, "FiringRate": 10}, hail)
    assert share == 0.0 and "counts the refill" in how
    other, _, _ = models.emergency_reload_share(er, {"BoltCount": 5, "Speed": 1.0, "FiringRate": 2}, Skill("Basic Bolt", 1))
    assert other > 0

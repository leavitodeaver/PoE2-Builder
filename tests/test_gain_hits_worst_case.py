"""MECHC20 (MECHC19's Handover; the same rule as AUDIT29 A29-1, an OVERCOUNT): "when hit" gains (Rage, charges,
Onslaught, debuffs on the boss, meta Energy, Iron Ward, Deadly Resolve, "Hit Recently", the recovery lines) read the
boss's landed hits a second. On the worst-case pinnacle that was its HIGHEST real red-flash share over the slowest real
cadence, a mix above the lowest real boss's rate for a build that Evades or Blocks. A gain now reads the lowest real
preset's landed rate (boss_hits.gain_hits); a named boss reads its own, as before."""
import pytest

from builder.search import boss_hits, bosses, conditions


def _stats(boss, evade=0.0, block=0.0):
    return {"_boss": boss, "enemySkillTime": bosses.defence_hit_every(boss), "EvadeChance": evade,
            "EffectiveBlockChance": block, **{f"{t}TakenHit": 1000.0 for t in boss_hits.TYPES}}


def _old(stats):
    return boss_hits.land_share(stats) / bosses.benefit_skill_time(stats)


def test_a_gain_on_the_worst_case_reads_the_lowest_real_bosss_landed_hits():
    worst = _stats(bosses.WORST, evade=40, block=40)
    rate, how = boss_hits.gain_hits(worst)
    real = [_old(s) for _, s in boss_hits.real_readings(worst)]
    assert rate == pytest.approx(min(real)) and "lowest real boss" in how, how
    # the old mix (the highest red-flash share over the slowest cadence) counted more hits than any real low
    assert rate < _old(worst)
    # a named boss: its own landed hits, unchanged
    ash = _stats("Arbiter of Ash", evade=40, block=40)
    assert boss_hits.gain_hits(ash)[0] == pytest.approx(_old(ash))


def test_rage_when_hit_and_a_charge_on_a_crit_taken_read_it():
    line = "Gain 5 Rage when Hit by an Enemy"
    worst = {**_stats(bosses.WORST, evade=50), "MaximumRage": 30, "Speed": 1.0}
    gain, events, how = conditions._rage_gain(line, worst, False)
    assert events == pytest.approx(boss_hits.gain_hits(worst)[0]) and gain == pytest.approx(5 * events), how
    assert events < _old(worst)
    # "Gain an Endurance Charge when you take a Critical Hit": 5% of the hits that LAND (before, of every boss skill use,
    # evaded ones too)
    rate, how = conditions._charge_rate("Gain an Endurance Charge when you take a Critical Hit", "Endurance", worst)
    assert rate == pytest.approx(0.05 * boss_hits.gain_hits(worst)[0]), how
    ash = {**_stats("Arbiter of Ash", evade=50), "Speed": 1.0}
    rate, _ = conditions._charge_rate("Gain an Endurance Charge when you take a Critical Hit", "Endurance", ash)
    assert rate == pytest.approx(0.05 * _old(ash)) and rate < 0.05 / bosses.benefit_skill_time(ash)


def test_adaptive_skins_hit_recently_share_on_the_worst_case_is_the_lowest_real_bosss():
    from builder.search.assumptions import Assumptions

    share, how = Assumptions.boss_hit_recently_share(bosses.WORST, "Physical")
    lows = [Assumptions.boss_hit_recently_share(n, "Physical")[0] for n in bosses.REAL]
    assert share == pytest.approx(min(lows)) and "lowest real boss" in how, how
    # an element one real boss never deals: 0 on the worst case
    assert Assumptions.boss_hit_recently_share(bosses.WORST, "Cold")[0] == 0.0


def test_hit_recently_share_reads_the_landed_uses_after_dodge_evade_and_block():
    """(MECHC50; the manager's 08:45 note from MECHD8, an OVERCOUNT before) a gain from being Hit Recently reads the boss's
    hits that LAND: its uses, past PoB's evade / block, 2/3 of the rest dodged (the user's rate), red-flash avoided; rows
    for no dodge and every strike (the old upper reading) sit above it."""
    import math

    from builder.search import boss_hits
    from builder.search.assumptions import Assumptions

    olroth = "Olroth, Origin of the Fall"
    head, how = Assumptions.boss_hit_recently_share(olroth, "Cold", {"EvadeChance": 40})
    red, rest = boss_hits.kind_land_parts({"_boss": olroth, "EvadeChance": 40}, "Cold")
    counts = boss_hits.hit_table(olroth)[1]["Cold"] / boss_hits.RED_FLASH[olroth][2]
    every = (getattr(bosses, "USE_EVERY", None) or bosses.HIT_EVERY).get(olroth) or bosses.HIT_EVERY[olroth]
    assert head == pytest.approx(1 - math.exp(-4 * counts / every * rest / 3)) and "dodged" in how
    nododge = Assumptions.boss_hit_recently_share(olroth, "Cold", {"EvadeChance": 40}, "no dodge")[0]
    strike = Assumptions.boss_hit_recently_share(olroth, "Cold", {"EvadeChance": 40}, "every strike")[0]
    assert head < nododge < strike
    assert Assumptions.boss_hit_recently_share(olroth, "Cold", {"EvadeChance": 100})[0] == 0.0  # (red-flash avoided)

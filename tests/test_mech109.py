"""MECH109: Blind on Hit from skills' own stats and their supports' (conditions.BLIND_HIT_STATS, Conditions._skill_blind),
and the on-hit debuff share at slow hits (Conditions._on_hit_events + periodic_absent, the Maim support's too). Pure
Python: a stand-in build answers the bridge calls."""
import pytest

from builder.search import conditions, models

GLOBAL, ATTACKS = conditions.BLIND_HIT_STATS


class StandIn:
    def __init__(self, skills, main_group=1):
        self.skills, self.main_group = skills, main_group

    def call(self, name, **kw):
        if name == "groups":
            return {"mainSocketGroup": self.main_group, "groups": []}
        assert name == "activeSkills"
        out = []
        for s in self.skills:
            row = {k: v for k, v in s.items() if k not in ("level", "support")}
            row["levelStats"] = dict(s.get("level") or {}) if kw.get("levelStats") else {}
            want = kw.get("supportStats") or ()
            row["supportStats"] = {k: v for k, v in (s.get("support") or {}).items() if k in want}
            out.append(row)
        return {"skills": out}


def conds(skills, main):
    c = conditions.Conditions.__new__(conditions.Conditions)
    c.build, c.main_skill = StandIn(skills), main
    return c


MAIN = {"Speed": 3.5, "HitChance": 100}


def test_twisters_own_blind_on_its_hits():
    c = conds([{"name": "Twister", "group": 1, "attack": True, "level": {ATTACKS: 100}}], "Twister")
    up, how = c._skill_blind(dict(MAIN), {"Attack"}, [])
    assert up == pytest.approx(1.0) and "Twister" in how


def test_a_slow_side_blind_is_up_its_share_not_always():
    # (a 100% chance at 0.16 hits/s, 4 s each: up 64% of the fight, not "always on")
    skills = [{"name": "Spark", "group": 1, "attack": False},
              {"name": "Frost Bomb", "group": 2, "attack": False, "support": {GLOBAL: 100}}]
    rows = [{"index": 2, "name": "Frost Bomb", "hits": 0.16, "how": "x"}]
    up, _ = conds(skills, "Spark")._skill_blind(dict(MAIN), {"Spell"}, [], rows)
    assert up == pytest.approx(0.64)
    # (the "increased Blind Duration" lines scale the 4 s)
    up, _ = conds(skills, "Spark")._skill_blind(dict(MAIN), {"Spell"}, ["50% increased Blind Duration"], rows)
    assert up == pytest.approx(0.96)


def test_blindside_and_spells_attack_stat_blind_nothing():
    blindside = [{"name": "Twister", "group": 1, "attack": True, "level": {ATTACKS: 100},
                  "support": {conditions.NO_BLIND_STAT: 1}}]
    assert conds(blindside, "Twister")._skill_blind(dict(MAIN), {"Attack"}, []) is None
    spell = [{"name": "Spark", "group": 1, "attack": False, "level": {ATTACKS: 100}}]
    assert conds(spell, "Spark")._skill_blind(dict(MAIN), {"Spell"}, []) is None
    # (a supported skill with no hit rate read: 0, nothing counted)
    side = [{"name": "Spark", "group": 1, "attack": False},
            {"name": "Living Lightning", "group": 4, "attack": False, "support": {GLOBAL: 100}}]
    assert conds(side, "Spark")._skill_blind(dict(MAIN), {"Spell"}, [], []) is None


def test_repulsion_wave_blinds_at_its_detonations(monkeypatch):
    monkeypatch.setattr(conditions, "repulsion_minion_hits", lambda build, stats: (0.0, ""))
    skills = [{"name": "Spark", "group": 1, "attack": False},
              {"name": conditions.REPULSION_WAVE, "group": 4, "attack": True, "support": {GLOBAL: 100}}]
    stats = {"Speed": 0.2, "HitChance": 100,
             "_curse_uses": [models.CurseUse(models.REPULSION, 0.5, "x")]}
    up, how = conds(skills, "Spark")._skill_blind(stats, {"Spell"}, [], [])
    assert up == pytest.approx(0.4)  # (0.5 of the fight x 0.2 hits/s = 0.1 waves/s, 4 s each)
    assert "Repulsion curses it" in how


def test_the_maim_support_at_slow_side_hits_is_its_share():
    # (MECH109: before, 1 - (1 - 1)^(h x 4) read a 100% Maim as always on at 0.1 hits/s)
    skills = [{"name": "Rain of Arrows", "group": 1, "attack": True},
              {"name": "Snipe", "group": 2, "attack": True, "support": {conditions.MAIM_STAT: 100}}]
    rows = [{"index": 2, "name": "Snipe", "hits": 0.1, "how": "x"}]
    up, _ = conds(skills, "Rain of Arrows")._support_maim(dict(MAIN), {"Attack"}, [], rows)
    assert up == pytest.approx(0.4)


def test_exposure_supports_on_other_groups_count_at_their_rows():
    # (MECH109, EXPOSURE_SIDE: b03's Lightning Exposure on Voltaic Grenade; before, the main skill's supports only)
    rows = [{"index": 9, "name": "Voltaic Grenade", "hits": 0.13, "shock": 0.25, "ignite": 0.0, "crits": 0.01,
             "types": ["Lightning"], "how": "x"},
            {"index": 10, "name": "Rain of Arrows", "hits": 2.0, "crits": 0.5, "types": ["Physical"], "how": "x"}]
    stats = {"_side_rows": rows, "_exposure_side": [
        ("Voltaic Grenade", 9, {"inflict_exposure_for_x_ms_on_shock": 8000}),
        ("Rain of Arrows", 10, {"inflict_exposure_for_x_ms_on_cold_crit": 8000}),  # (no Cold hit damage: 0)
        ("Ancestral Spirits", 2, {"inflict_exposure_for_x_ms_on_ignite": 8000})]}  # (no row: 0)
    got = conditions._exposure_support_sources(stats)
    assert [round(up, 4) for up, _ in got] == [round(0.13 * 0.25 * 8, 4), 0.0, 0.0]
    assert "Voltaic Grenade beside the main skill" in got[0][1]
    rows[1]["types"] = ["Cold"]
    assert conditions._exposure_support_sources(stats)[1][0] == pytest.approx(1.0)  # (0.5 crits/s x 8 s, capped)


def test_other_groups_chills_join_the_chilled_share():
    # (MECH109, SIDE_CHILL: before, chilled_share read the main skill's hits, grounds and auras only)
    rows = [{"index": 7, "name": "Frost Wall", "hits": 0.1, "chill": 1.0},  # (0.1 hits/s x 8 s: 80%, evenly spaced)
            {"index": 8, "name": "Spark", "hits": 3.0, "chill": 0.0}]
    up, how = conditions.side_chill_share({"_side_rows": rows})
    assert up == pytest.approx(0.8) and "Frost Wall" in how and "Spark" not in how
    assert conditions.chilled_share({"_side_rows": rows}) == pytest.approx(0.8)  # (the main skill Chills nothing)
    assert conditions.side_chill_share({"_side_rows": rows[1:]}) == (0.0, "")


def test_crit_recently_counts_every_groups_crits_at_its_share():
    # (MECH109, CRIT_RECENTLY: before, the main skill alone, all or nothing at 90% in 4 s)
    main = {"CritChance": 27.6, "Speed": 1.0, "HitChance": 100}
    alone = conditions.crit_recently_share(main)
    assert alone == pytest.approx(1 - conditions.periodic_absent(0.276, 4.0))  # (~73%: was off under the 90% cut)
    rows = [{"name": "Tempest Bell", "hits": 1.8, "crits": 0.5}]
    both = conditions.crit_recently_share({**main, "_side_rows": rows})
    assert both == pytest.approx(1 - conditions.periodic_absent(0.276, 4.0)
                                 * conditions.periodic_absent(0.5 / 1.8, 7.2))
    # (a main skill with no uses: the side skills alone; none that crits: 0)
    assert conditions.crit_recently_share({"CritChance": 100, "Speed": 0, "_side_rows": rows}) > 0.9
    assert conditions.crit_recently_share({"CritChance": 100, "Speed": 0}) == 0.0


def test_deceptions_unbroken_chain_is_medium_low_with_the_broken_chain_row():
    # (MECH109, RESEARCH60 R60-10: x4 stays at a use within the clone's life; what resets the chain is UNVERIFIED)
    factor, how = models.deception_factor(1.57)
    assert factor == pytest.approx(1 + models.DECEPTION_CAP)
    assert "MEDIUM-LOW" in how and "x1.50" in how
    assert "in runs" in models.DECEPTION_RUNS and "Brutality before a run" in models.DECEPTION_RUNS


def test_cloud_poisons_stack_only_to_the_poison_limit():
    # (MECH109, R60-12: PoB's cloud PoisonDPS is capped at min(stacks, limit); the cloud's extra applications fill it up
    # to the limit, no further; before, they multiplied it whole)
    assert models.cloud_poison_scale({"PoisonStacksMax": 1, "PoisonStackPotential": 0.8}, 2.5) == pytest.approx(1.25)
    assert models.cloud_poison_scale({"PoisonStacksMax": 1, "PoisonStackPotential": 1.0}, 2.5) == pytest.approx(1.0)
    assert models.cloud_poison_scale({"PoisonStacksMax": 1, "PoisonStackPotential": 0.2}, 2.5) == pytest.approx(2.5)
    assert models.cloud_poison_scale({"PoisonStacksMax": 10, "PoisonStackPotential": 0.1}, 2.5) == pytest.approx(2.5)
    assert models.cloud_poison_scale({}, 2.5) == pytest.approx(2.5)  # (no stack fields: as before)

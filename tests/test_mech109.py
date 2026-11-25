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

"""MECHB52: the main skill's hit-rate cut handed to the ailment readers (build.main_hit_cut, MECH98's hook)."""
from __future__ import annotations

import pytest

from builder.search import models


def test_the_hit_cut_is_the_models_dps_more():
    """Flame Breath's x0.40 channel share and Hailstorm Rounds' x0.28 bolts are hit-rate cuts: the ailment readers
    (Speed x HitChance) take the same factor through build.main_hit_cut (the manager 13:36 21 Nov, an OVERCOUNT)."""
    fb = models.Skill("Flame Breath", 1, attack=True)
    mods, notes = models.flame_breath_model(fb, 7.257, 10.032, 4.0, 0.0, "no Rage")
    assert models.hit_cut_of(mods) == pytest.approx(4.0 / 10.032, abs=1e-4)
    assert "build.main_hit_cut" in notes[0]["basis"]
    hail = models.Skill("Hailstorm Rounds", 1, attack=True)
    stats = {"Speed": 0.634, "ReloadTime": 0.3, "BoltCount": 55, "AreaOfEffectRadius": 10,
             "AreaOfEffectRadiusSecondary": 40}
    mods, notes = models.hailstorm_rounds_model(hail, stats, (0.3, "a"), (1.5, "b"))
    target = (1 / 0.3) * (1.3 / 4) ** 2 * 0.5
    assert models.hit_cut_of(mods) == pytest.approx(target / 0.634, abs=1e-4)
    assert models.hit_cut_of(mods, 0.5) == pytest.approx(0.5 * target / 0.634, abs=1e-4)  # (two cuts multiply)
    assert "build.main_hit_cut" in notes[0]["basis"]
    assert models.hit_cut_of([]) is None and models.hit_cut_of([], 0.7) == 0.7
    assert models.hit_cut_of([models.raw_mod("Damage", "MORE", -50)]) is None  # (a damage cut isn't a hit cut)


def test_tornado_deals_each_absorbed_ground_element_at_its_share():
    """R42-10: the gem's elemental DoT (per minute) for each element whose ground is at the boss, at that share."""
    tornado = models.Skill("Tornado", 1, spell=True)
    level = {models.TORNADO_STAT: 17530}
    mods, notes = models.tornado_ground_model(tornado, level, {"Cold": (0.5, "a"), "Fire": (0.0, ""),
                                                               "Lightning": (1.0, "b")})
    assert {m["key"]: m["value"] for m in mods} == {"ColdDot": pytest.approx(17530 / 60 * 0.5, abs=0.01),
                                                    "LightningDot": pytest.approx(17530 / 60, abs=0.01)}
    assert all(m["kind"] == "SkillData" and m["skill"] == "Tornado" for m in mods)
    assert notes[0]["confidence"] == "LOW" and "UNVERIFIED" in notes[0]["basis"]
    assert models.tornado_ground_model(tornado, level, {})[0] == []  # (no ground: nothing added, the note says so)
    assert models.tornado_ground_model(models.Skill("Twister", 1), level, {"Cold": (1.0, "")}) == ([], [])
    assert models.tornado_ground_model(tornado, level, {"Cold": (1.0, "")}, {models.TORNADO_ENTRY}) == ([], [])


def test_icestorms_overlapping_storms_are_a_row_only():
    """R42-3: the headline keeps PoB's one storm; up to 3 overlapping storms a row, UNVERIFIED."""
    ice = models.Skill("Icestorm", 1, spell=True)
    note = models.icestorm_overlap_note(ice, 1.25, 4.0)[0]
    assert note["value"] == "x1.000" and "3.00 storms" in note["label"] and "UNVERIFIED" in note["basis"]
    assert models.icestorm_overlap_note(ice, 0.2, 4.0) == []  # (0.8 storms: nothing overlaps)
    assert models.icestorm_overlap_note(models.Skill("Spark", 1), 2.0, 4.0) == []


def test_vivid_stampede_runs_at_the_wisps_the_build_makes():
    """un-morrigans-guidance: Stags = movement Wisps (rolls x 4.6 m / 10 m) + with The Mórrigan's Guidance one a
    Stampede every max(0.3 s, 1 s to end + the next attack); 4 landings a Stag; 20% more a leap (additive, x1.30)."""
    main = models.Skill("Twister", 1, attack=True)
    vs = models.Skill("Vivid Stampede", 7, attack=True)
    mods, notes = models.vivid_stampede_model(main, vs, [], 0.3, 2.0)
    moved = 0.3 * 4.6 / 10
    assert mods[0]["key"] == "triggerRate" and mods[0]["value"] == pytest.approx(moved * 4, abs=1e-3)
    assert len(mods) == 2 and notes[0]["confidence"] == "LOW"
    mods, notes = models.vivid_stampede_model(main, vs, ["Gain a Vivid Wisp when Vivid Stampede ends"], 0.3, 2.0)
    assert mods[0]["value"] == pytest.approx((moved + 1 / 1.5) * 4, abs=1e-3)
    assert mods[2]["name"] == "Damage" and mods[2]["value"] == pytest.approx(30.0, abs=0.01)
    assert "0.3 s:" in notes[0]["basis"] and "compounding: x1.342" in notes[0]["basis"]
    assert models.vivid_stampede_model(models.Skill("Spark", 1, spell=True), vs, [], 0.3, 2.0)[0] == []
    assert models.vivid_stampede_model(main, vs, [], 0.0, 2.0)[0] == []  # (no Wisps: 0, said)
    assert models.vivid_stampede_model(main, None, [], 0.3, 2.0) == ([], [])


def test_primal_bounty_adds_projectiles_only_where_they_hit_one_boss():
    """R42-15: a feather every 4 s (50% faster with The Mhacha's Gift) Empowers that share of the main skill's uses with
    +2 Projectiles; counted on a shotgun skill (its fragments Merge), 0 with a row elsewhere."""
    lines = ["Gain a Primal Owl Feather every 4 seconds, up to a maximum of 3", "Gain Owl Feathers 50% faster"]
    frag = models.Skill("Fragmentation Rounds", 1, attack=True)
    mods, notes = models.primal_bounty_model(frag, {"Projectile"}, lines, 1.5, 10)
    assert mods[0]["value"] == pytest.approx(100 * (0.375 / 1.5) * 2 / 10, abs=0.01)
    arrow = models.Skill("Lightning Arrow", 1, attack=True)
    mods, notes = models.primal_bounty_model(arrow, {"Projectile"}, lines[:1], 1.0, 1)
    assert mods == [] and notes[0]["value"] == "x1.000" and "x1.500" in notes[0]["basis"]
    assert models.primal_bounty_model(models.Skill("Maul", 1, attack=True), {"Melee"}, lines, 1.0, 1)[0] == []
    assert models.primal_bounty_model(arrow, {"Projectile"}, [], 1.0, 1) == ([], [])


def test_the_hit_cut_reads_only_the_main_skills_mods():
    """The manager 16:00 (MECH99's hit-cut survey): each owner's HIT-CUT model hands its factor on; a mod tagged to a
    side skill is not the main skill's hits."""
    from builder.search import assumptions

    mods = [models.raw_mod("DPS", "MORE", -40, "Spark"), models.raw_mod("DPS", "MORE", -50, "Vile Effusion")]
    assert models.hit_cut_of(mods, None, "Spark") == pytest.approx(0.6)
    assert models.hit_cut_of(mods, 0.5, "Vile Effusion") == pytest.approx(0.25)
    assert models.hit_cut_of(mods, None, "Arc") is None

    class Stub:
        main_hit_cut = None
    build = Stub()
    assumptions.hit_cut(build, mods, "Spark")
    assumptions.hit_cut(build, [models.raw_mod("DPS", "MORE", -50, "Spark")], "Spark")
    assert build.main_hit_cut == pytest.approx(0.3)
    assumptions.hit_cut(build, mods, None)  # (no main skill: unchanged)
    assert build.main_hit_cut == pytest.approx(0.3)
    windup = models.channel_windup_model(models.Skill("Hollow Form", 1, attack=True), 2.0, 2.0, 3.0, 2 / 3)[0]
    assert 0 < models.hit_cut_of(windup, None, "Hollow Form") < 1


def test_volcanos_hit_cut_is_its_eruptions_not_its_stages():
    """A MIXED model names its hit part: Volcano's (1 + 1.5 k) / (2 + k) is 1 / (2 + k) eruptions a cast time (the hit
    cut) times its stage more (damage per hit)."""
    volcano = models.Skill("Volcano", 1, spell=True, stat_set="Eruption")
    mods, _ = models.volcano_channel_model(volcano, "Eruption", [{"label": "Eruption", "AverageDamage": 100.0}])
    assert mods and 0 < mods[0]["hit_cut"] < 1
    assert models.hit_cut_of(mods, None, "Volcano") == pytest.approx(mods[0]["hit_cut"])

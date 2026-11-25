"""GEM42: Combo's decay in models.vruun_combo_share (Culmination / Vruun's Inevitability; GEM41's find)."""

from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill

CULMINATION_I = {"skill_maximum_number_of_combo_stacks": 10, "base_combo_stacks_decay_delay_ms": 4000}


def _main(stats: dict | None = None) -> Skill:
    return Skill("Earthquake", 1, attack=True, melee=True, support_stats=dict(stats or CULMINATION_I))


def test_the_decay_stat_is_summed_by_the_bridge():
    assert models.COMBO_DECAY_STAT in models.gem_stat_support_stats()


def test_random_timing_matches_a_monte_carlo():
    # (GEM41's Monte Carlo, scratch/gem41/combo_decay.py; capped: scratch/gem42/check.py's 40,000 runs)
    assert models.combo_at_random_timing(0.2, 4.0, 10.0, 10) == pytest.approx(1.159, abs=0.02)
    assert models.combo_at_random_timing(3.0, 4.0, 5.0, 10) == pytest.approx(9.864, abs=0.05)
    assert models.combo_at_random_timing(1.0, 4.0, 6.0, 3) == pytest.approx(2.867, abs=0.02)
    assert models.combo_at_random_timing(0.0, 4.0, 6.0, 10) == 0.0


def test_the_chain_cap_only_binds_when_builders_cannot_strike_within_the_decay():
    assert models.combo_chain_cap([0.5], 4.0) is None
    assert models.combo_chain_cap([5.0, 5.0], 4.0) is None  # (staggered: a hit every 2.5 s)
    assert models.combo_chain_cap([8.0], 4.0) == 1.0
    assert models.combo_chain_cap([9.0, 9.0], 4.0) == 2.0


def test_fast_builders_keep_the_plain_combo_and_note_the_random_timing_reading():
    main = _main()
    skills = [main, Skill("Boneshatter", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Boneshatter", "speed": 2.0, "HitChance": 100, "_press": 0.1}}
    combo, why, play = models.vruun_combo_share(main, {"Speed": 0.2, "_groups": groups, "_skills": skills}, main)
    # 0.2 hits/s over 0.2 uses/s: 1 Combo pressed right before each use; at random times the 4 s decay cuts it
    assert combo == pytest.approx(1.0)
    assert "at random timing" in why and "Strike within 4 s" in play
    random = float(why.split("at random timing")[1].split(")")[1].split()[0].rstrip(")"))
    assert random < 1.0


def test_a_builder_slower_than_the_decay_holds_one_combo_a_use():
    main = _main()
    skills = [main, Skill("Slow Strike", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Slow Strike", "speed": 0.125, "cooldown": 8.0, "HitChance": 100, "_press": 1.0}}
    combo, why, _ = models.vruun_combo_share(main, {"Speed": 0.04, "_groups": groups, "_skills": skills}, main)
    # 3.1 hits a use without the decay; a Strike every 8 s can't chain within 4 s: 1
    assert combo == pytest.approx(1.0)
    assert "at most 1 Combo a use" in why and "without the decay: 3.1" in why


def test_vruuns_six_second_decay_lets_the_same_builder_chain():
    main = _main({"skill_maximum_number_of_combo_stacks": 10, "base_combo_stacks_decay_delay_ms": 6000})
    skills = [main, Skill("Slow Strike", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Slow Strike", "speed": 0.2, "cooldown": 5.0, "HitChance": 100, "_press": 1.0}}
    combo, why, _ = models.vruun_combo_share(main, {"Speed": 0.05, "_groups": groups, "_skills": skills}, main)
    assert combo == pytest.approx(4.0) and "at most" not in why


def test_a_set_models_damage_steer_is_carried_in_its_mix_note():
    sets = [{"label": "Normal Strikes", "TotalDPS": 100.0}, {"label": "Final Strike", "TotalDPS": 120.0},
            {"label": "Explosion", "TotalDPS": 300.0}]
    main = Skill("Rapid Assault", 1, attack=True, melee=True, stat_set="Explosion")
    mods, notes = models.detonated_set_model(main, "Explosion", sets, 1.0, 0.0, "no Detonator")
    assert mods and mods[0]["name"] == "Damage"  # (the steer: no Detonator, the Explosion held under the plain set)
    assert notes[0]["set_damage"] == pytest.approx(1 + mods[0]["value"] / 100)
    assert models.set_damage([], "Rapid Assault") == 1.0


def test_the_strongest_ailment_takes_the_mix_models_steer_out_once():
    """MECH107 (POE2T8800-8802): the steer F sits on every set row and on the scored set; the mixed Bleed is the sets'
    own, so the final line is the plain mix over PoB's scored Bleed without F."""
    steer = 0.4
    base = [{"label": "Plain", "BleedDPS": 30.0, "BleedStackPotential": 2.0, "Speed": 1.0, "DpsMultiplier": 1.0},
            {"label": "Explosion", "BleedDPS": 60.0, "BleedStackPotential": 2.0, "Speed": 1.0, "DpsMultiplier": 1.0}]
    rows = [{**s, "BleedDPS": s["BleedDPS"] * steer} for s in base]  # (PoB's rows under the steer)
    main = Skill("Rapid Assault", 1, attack=True, melee=True, stat_set="Explosion")
    mods, notes = models.strongest_ailment_model(main, rows, {"Plain": 1.0, "Explosion": 0.0}, 1.0, steer=steer)
    factor = 1 + mods[0]["value"] / 100
    # PoB's scored Bleed under the steer x this line = the Plain set's own Bleed (30), not 30 x F
    assert 60.0 * steer * factor == pytest.approx(30.0, rel=1e-3)
    assert "taken back out" in notes[0]["basis"]
    old, _ = models.strongest_ailment_model(main, rows, {"Plain": 1.0, "Explosion": 0.0}, 1.0)
    assert 60.0 * steer * (1 + old[0]["value"] / 100) == pytest.approx(30.0 * steer, rel=1e-3)  # (before: F twice)


def test_martial_master_lets_any_attack_build_combo_and_adept_doubles_each_gain():
    """The manager 23:57: the Invoker's Martial Master / Martial Adept weren't read by vruun_combo_share (an UNDERCOUNT)."""
    main = _main()
    bow = Skill("Lightning Arrow", 2, attack=True)  # (an Attack, not a melee Strike)
    skills = [main, bow]
    groups = {2: {"index": 2, "name": "Lightning Arrow", "speed": 2.0, "HitChance": 100, "_press": 0.25}}
    stats = {"Speed": 0.25, "_groups": groups, "_skills": skills}
    assert models.vruun_builder_groups(skills, main) == []
    combo, why, _ = models.vruun_combo_share(main, stats, main)
    assert combo == 0.0 and "no other melee Strike" in why
    master = {**stats, models.COMBO_FACTS: {"any_attack": True, "gain": 1.0}}
    assert models.vruun_builder_groups(skills, main, True) == [2]
    combo, why, _ = models.vruun_combo_share(main, master, main)
    assert combo == pytest.approx(2.0) and "Martial Master" in why  # (0.5 hits/s over 0.25 uses/s)
    adept = {**stats, models.COMBO_FACTS: {"any_attack": True, "gain": 2.0, "groups": {2: 2.2}}}
    combo, why, _ = models.vruun_combo_share(main, adept, main)
    assert combo == pytest.approx(4.4) and "x2.2 Combo" in why  # (Adept + a 20% chance in its group)


def test_the_gain_caps_at_the_skills_maximum_and_counts_in_the_chain_check():
    main = _main()
    skills = [main, Skill("Slow Strike", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Slow Strike", "speed": 0.125, "cooldown": 8.0, "HitChance": 100, "_press": 1.0}}
    stats = {"Speed": 0.04, "_groups": groups, "_skills": skills, models.COMBO_FACTS: {"gain": 2.0}}
    combo, why, _ = models.vruun_combo_share(main, stats, main)
    assert combo == pytest.approx(2.0) and "at most 2 Combo a use" in why  # (one hit a use, 2 Combo each)
    fast = {**stats, "_groups": {2: {**groups[2], "speed": 30.0, "cooldown": 0}}}
    assert models.vruun_combo_share(main, fast, main)[0] == pytest.approx(10.0)


# (GEM42 turn 4: the RESEARCH59 answers)
from tests.test_gem41 import engine  # noqa: E402,F401
from builder.search import assumptions  # noqa: E402

ATTACK_LINE = "Attack Skills have +1 to maximum number of Summoned Totems"


def test_attack_totem_lines_reach_shockwave_totem_with_their_own_row():
    """R59-3: Ancestral Artifice's Melee Attack lines MEDIUM-HIGH; "Attack Skills have +N Totems" probably counts too
    (MEDIUM), its own row."""
    slam = Skill("Shockwave Slam", 1, attack=True, melee=True)
    totem = Skill("Shockwave Totem", 1, totem_limit=3, totem_limit_own=1, melee_totem_added=1, attack_totem_added=1)
    _, notes = models.totem_count_model([slam, totem], slam)
    melee = next(n for n in notes if "Melee Attack totem lines" in n["label"])
    attack = next(n for n in notes if n["label"].endswith(": Attack totem lines"))
    assert melee["confidence"] == "MEDIUM-HIGH" and melee["value"] == "3 with / 1 without"
    assert attack["confidence"] == "MEDIUM" and attack["value"] == "3 with / 2 without" and "R59-3" in attack["basis"]


def test_the_bridge_adds_attack_totem_lines_to_shockwave_totems_limit(engine):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Shockwave Totem", ascendancies=["Warbringer"], level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Shockwave Totem"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build

    def totem():
        return next(s for s in build.call("activeSkills")["skills"] if s["name"] == "Shockwave Totem")

    bare = totem()
    build.call("setConfig", vars={"customMods": ATTACK_LINE}, fields=[])
    try:
        got = totem()
        assert got["attackTotemAdded"] == 1 and not got.get("meleeAttackTotemAdded")
        assert got["totemLimit"] == bare["totemLimit"] + 1
    finally:
        build.call("setConfig", vars={"customMods": "nil"}, fields=[])


def test_the_idol_of_maxarius_bonded_storm_limit_reaches_storm_skills_only():
    """R59-6: PoB doesn't parse "Storm Skills have +1 to Limit" (the Idol of Maxarius's Bonded line, ModRunes.lua);
    the bridge's sourceLines give a Bonded rune line, "Bonded: " stripped, only while PoB applies it (runeLineApplies),
    and LIMIT_LINES reads it by skill type."""
    line = "Storm Skills have +1 to Limit"
    assert assumptions.limit_line_adds([line], {"Storm", "Spell"}) == 1
    assert assumptions.limit_line_adds([line], {"Orb", "Spell"}) == 0


def test_lose_combo_slower_lengthens_the_decay():
    """R59-8: "Skills lose Combo N% slower" lengthens the delay, D / (1 + speed / 100) (MEDIUM-LOW)."""
    assert models.COMBO_SLOWER.match("Skills lose Combo 20% slower").group(1) == "20"
    main = _main()  # (Culmination I: 4 s)
    skills = [main, Skill("Slow Strike", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Slow Strike", "speed": 1 / 4.5, "cooldown": 4.5, "HitChance": 100,
                  "_press": 1.0}}
    stats = {"Speed": 0.05, "_groups": groups, "_skills": skills}
    assert "at most 1 Combo" in models.vruun_combo_share(main, stats, main)[1]  # (a Strike every 4.5 s > 4 s)
    slower = {**stats, models.COMBO_FACTS: {"falloff": -20.0}}
    combo, why, _ = models.vruun_combo_share(main, slower, main)  # (4 s / 0.8 = 5 s: the Strike chains)
    assert "at most" not in why and "Combo kept 5 s" in why and combo == pytest.approx(min(10.0, (1 / 4.5) / 0.05))


def test_devour_leaps_from_one_and_a_half_metres_with_three_a_row():
    """R59-14: Devour is a melee dash with no leap threshold: it leaps past melee reach, ~1.5 m (LOW-MEDIUM)."""
    devour = Skill("Devour", 1, attack=True, melee=True)
    assert models.DEVOUR_FAR_METRES == 1.5 and models.DEVOUR_FAR_ROW_METRES == 3.0
    mods, notes = models.leap_time_model(devour, models.DEVOUR_FAR_METRES, 0.0, 1.0)
    assert mods[0]["value"] == pytest.approx(0.25) and "3 m" in notes[0]["basis"]

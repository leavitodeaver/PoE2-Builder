"""MECH116: the shared Poison pool's limit is the build's, read from the Poisoning sources when the main skill can't
Poison (GEM50's find, probes POE2T9807 / 9808: the Gas Arrow skeleton held to 1 Poison at a limit of 3)."""
import pytest

from builder.search import models


def _value(mods, name):
    return next(m["value"] for m in mods if m["name"] == name) / 100


def test_main_skill_limit_stands_where_it_poisons():
    notes = [{"poison_pool": ("AddedGasArrowCloud", 40.0, 1.0), "poison_limit": 5.0}]
    assert models.build_poison_limit(2.0, notes)[0] == 2.0


def test_no_main_limit_reads_the_sources_limit_and_never_a_minions():
    notes = [{"poison_pool": ("AddedGasArrowCloud", 40.0, 1.68), "poison_limit": 3.0},
             {"poison_pool": ("AddedSniperGasArrow", 10.0, 1.0), "poison_limit": 9.0},
             {"poison_pool": ("AddedPoisonGroup4ChayulasWill", 5.0, 1.0), "poison_limit": 2.0}]
    limit, how = models.build_poison_limit(0.0, notes)
    assert limit == 3.0 and "Gas Arrow's cloud" in how
    assert models.build_poison_limit(0.0, notes[1:2]) == (1.0, models.build_poison_limit(0.0, [])[1])
    assert models.build_poison_limit(0.0, [])[0] == 1.0


def test_gas_arrow_skeleton_cloud_kept_at_the_builds_limit():
    # the Arrow carries no Poison (own 0): at the build's limit 3 the cloud's 1.68 Poisons stand whole (no row);
    # at the old fallback 1 they were cut to one Poison
    mods = [models.added_input("GasArrowCloud", 69.18)]
    notes = [{"poison_pool": ("AddedGasArrowCloud", 69.18 / 1.68, 1.68), "poison_limit": 3.0}]
    limit, how = models.build_poison_limit(0.0, notes)
    out, rows = models.poison_pool_model(mods, notes, (0.0, 0.0, "none"), limit, frozenset(), how)
    assert _value(out, "AddedGasArrowCloud") == pytest.approx(69.18, abs=0.01) and rows == []
    out, rows = models.poison_pool_model(mods, notes, (0.0, 0.0, "none"), 1.0)
    assert _value(out, "AddedGasArrowCloud") == pytest.approx(69.18 / 1.68, abs=0.01) and rows


def _meta(meta):
    gems = {"Despair": {"skillTypes": ["Spell", "AppliesCurse"]}, meta: {"skillTypes": ["Meta"]}}
    return [{"enabled": True, "gems": [{"name": meta, "support": False}, {"name": "Despair", "support": False}]}], gems


def test_cast_on_minion_death_curse_up_at_its_minion_death_rate():
    """A41-31: a curse Cast on Minion Death casts is up at the build's minion-death rate x its duration, its note naming
    the feed; a measured 0.0 is 0 (MECHB71's minion_death_triggers)."""
    groups, gems = _meta("Cast on Minion Death")
    deaths = {"Despair": (0.05, "Cast on Minion Death in group 2, fed by 0.050 minion deaths a second (Skeletons)")}
    (use,) = models.curse_uses(groups, gems, {}, 8.0, [], {"Despair": 0.05}, deaths)
    assert use.share == pytest.approx(0.05 * models.CURSE_DURATION) and "fed by 0.050 minion deaths" in use.how
    assert not use.flat
    (use,) = models.curse_uses(groups, gems, {}, 8.0, [], {"Despair": 0.0}, {"Despair": (0.0, "no minion deaths read")})
    assert use.share == 0.0


def test_a_modelling_gap_zero_never_zeroes_a_meta_curse():
    """The manager's 03:09 ruling: energy_model's 0.0 for a meta whose Energy it doesn't model is a gap, not a reading:
    the curse keeps the flat 100%, LOW and named in the play note; a removed meta's 0 and a modelled 0 stand."""
    groups, gems = _meta("Pounce")
    (use,) = models.curse_uses(groups, gems, {}, 8.0, [], {"Despair": 0.0})
    assert use.share == 1.0 and use.flat and "LOW" in use.how
    _, notes = models.curse_model([use], [], None)
    assert notes[0]["confidence"] == "LOW" and "Despair" in notes[0]["play"]
    (use,) = models.curse_uses(groups, gems, {}, 8.0, [], {"Despair": 0.04})  # another model's positive rate stands
    assert use.share == pytest.approx(0.04 * models.CURSE_DURATION)
    groups, gems = _meta("Cast on Critical")
    (use,) = models.curse_uses(groups, gems, {}, 8.0, [], {"Despair": 0.0})
    assert use.share == 0.0 and not use.flat
    groups, gems = _meta("Curse on Block")
    (use,) = models.curse_uses(groups, gems, {}, 8.0, [], {"Despair": 0.0})
    assert use.share == 0.0


LINE = "Enemies in your Presence Resist Elemental Damage based on their Lowest Resistance"


def test_presence_lowest_resistance_scales_the_ignite_inside_presence_only():
    """POBR1: PoB applies the line to hits only (POE2T6763); the Ignite takes (1 - lowest) / (1 - Fire) in Presence."""
    resists = {"found": True, "Fire": 45, "Cold": 25, "Lightning": 45}
    inside = {"_boss_in_presence": True, "_boss_presence_how": "the boss at 2 m"}
    mods, notes = models.presence_lowest_ignite([LINE], resists, inside, "Flame Wall")
    ratio = 0.75 / 0.55
    assert mods[0]["name"] == "AilmentMagnitude" and mods[0]["keywordFlags"] == ["Ignite"]
    assert mods[0]["value"] == pytest.approx(100 * (ratio - 1), abs=0.01) and notes[0]["value"] == f"x{ratio:.3f}"
    mods, notes = models.presence_lowest_ignite([LINE], resists, {"_boss_in_presence": False}, "Flame Wall")
    assert mods == [] and notes[0]["value"] == "x1.000"
    # equal resistances (the default boss), Fire the lowest, no line, or no reading: nothing
    assert models.presence_lowest_ignite([LINE], {"found": True, "Fire": 45, "Cold": 45, "Lightning": 45}, inside,
                                         "Flame Wall") == ([], [])
    assert models.presence_lowest_ignite([LINE], {"found": True, "Fire": 10, "Cold": 45, "Lightning": 45}, inside,
                                         "Flame Wall") == ([], [])
    assert models.presence_lowest_ignite(["Elemental Damage you deal with Hits is Resisted by lowest Elemental "
                                          "Resistance instead"], resists, inside, "Flame Wall") == ([], [])
    assert models.presence_lowest_ignite([LINE], {"found": False}, inside, "Flame Wall") == ([], [])


def test_eternal_flame_iii_refresh_lengthens_the_strongest_ignite_window():
    """Step 4: 2 hits/s x 50% crit x 20% = 0.2 refreshes/s on a 4 s Ignite: it lives (e^0.8 - 1) / 0.8 = 1.532x, so
    PoB's 1.5 applications a duration become 2.30; the roll average S / (S + 1) goes 0.6 -> 0.697."""
    import math
    stats = {"IgniteStackPotential": 1.5, "IgniteDuration": 4.0, "IgniteDPS": 100.0, "Speed": 2.0, "HitChance": 100.0,
             "CritChance": 50.0}
    mods, notes = models.eternal_flame_refresh(stats, "Crossbow Shot")
    s2 = 1.5 * math.expm1(0.8) / 0.8
    factor = (s2 / (s2 + 1)) / (1.5 / 2.5)
    assert mods[0]["keywordFlags"] == ["Ignite"] and mods[0]["value"] == pytest.approx(100 * (factor - 1), abs=0.01)
    assert notes[0]["value"] == f"x{factor:.3f}" and notes[0]["confidence"] == "MEDIUM"
    # no crits, no Ignite, or the entry forbidden: nothing
    assert models.eternal_flame_refresh({**stats, "CritChance": 0}, "Crossbow Shot") == ([], [])
    assert models.eternal_flame_refresh({**stats, "IgniteDPS": 0}, "Crossbow Shot") == ([], [])
    assert models.eternal_flame_refresh(stats, "Crossbow Shot", None, {models.ETERNAL_FLAME_ENTRY}) == ([], [])
    # a rare Ignite (S under 1): its uptime grows with the window too
    mods, _ = models.eternal_flame_refresh({**stats, "IgniteStackPotential": 0.5}, "Crossbow Shot")
    s2 = 0.5 * math.expm1(0.8) / 0.8
    assert mods[0]["value"] == pytest.approx(100 * ((s2 / (s2 + 1) if s2 > 1 else 0.5) * min(1, s2) / (0.5 * 0.5) - 1),
                                             abs=0.01)


def test_no_physical_bleed_row_only_for_a_chance_on_hits_with_no_physical_damage():
    """Step 5 (R69-5, UNVERIFIED): the Bleeding box's lower reading takes the main skill's chance as 0."""
    from builder.search import conditions
    stats = {"BleedChancePerHit": 5.0, "Speed": 2.0, "HitChance": 100.0, "BleedDuration": 5.0, "PhysicalHitAverage": 0}
    up, text = conditions.no_physical_bleed(stats)
    assert up == 0.0 and "UNVERIFIED" in text and "R69-5" in text
    assert conditions.no_physical_bleed({**stats, "PhysicalHitAverage": 120.0}) == (0.0, "")
    assert conditions.no_physical_bleed({k: v for k, v in stats.items() if k != "PhysicalHitAverage"}) == (0.0, "")
    assert conditions.no_physical_bleed({**stats, "BleedChancePerHit": 0}) == (0.0, "")

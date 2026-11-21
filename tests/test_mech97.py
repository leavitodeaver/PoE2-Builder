"""MECH97 (MECH-A): Way of the Stonefist's "(30-40)% increased Culling Strike Threshold if you've dealt a Culling Strike
Recently". PoB parses it with leftover text (never applied); the ledger counts it at CulledRecently4s: min(1, the boss
preset's add Culls/s x 4 s), 0 against a lone boss (its own Cull ends the fight)."""
from types import SimpleNamespace

import pytest

from builder.ledger import translate
from builder.search import bosses
from builder.search.assumptions import Assumptions


def share(boss):
    a = Assumptions.__new__(Assumptions)
    a.build = SimpleNamespace(ctx=SimpleNamespace(request=SimpleNamespace(boss=boss)))
    return a.culled_recently_share(4.0)


def test_a_lone_boss_gives_nothing():
    value, how = share("Arbiter of Ash")
    assert value == 0.0 and "ends the fight" in how


def test_the_presets_adds_give_their_culls_times_four_seconds():
    culls = bosses.kill_stats("Atziri, the Red Queen")[bosses.CULLS_STAT]
    value, how = share("Atziri, the Red Queen")
    assert culls > 0 and value == pytest.approx(min(1.0, culls * 4.0)) and "evenly spaced" in how


def test_no_request_reads_the_default_boss():
    a = Assumptions.__new__(Assumptions)
    a.build = SimpleNamespace()
    expected = min(1.0, bosses.kill_stats(bosses.DEFAULT)[bosses.CULLS_STAT] * 4.0)
    assert a.culled_recently_share(4.0)[0] == pytest.approx(expected)


def test_the_line_reaches_the_ledger_as_a_share_line():
    found = translate("35% increased Culling Strike Threshold if you've dealt a Culling Strike Recently")
    assert found and found[1] == ["share:CulledRecently4s 35% increased Culling Strike Threshold"]
    assert found[0].on_items


def test_all_attack_damage_electrocute_flags_every_type_on_attacks():
    from builder.search import models

    mods, notes = models.electrocute_attack_mods(["All damage with Attacks Contributes to Electrocution Buildup"])
    assert {m["name"] for m in mods} == {f"{t}CanElectrocute" for t in ("Physical", "Lightning", "Cold", "Fire", "Chaos")}
    assert all(m["type"] == "FLAG" and m["keywordFlags"] == ["Attack"] for m in mods) and notes
    assert models.electrocute_attack_mods(["Lightning damage from Hits Contributes to Electrocution Buildup"]) == ([], [])


def test_lucky_while_surrounded_is_pobs_flag_under_its_own_condition():
    from builder.search import models

    mods, _ = models.lucky_surrounded_mods(["Attack Damage with Hits is Lucky while you are Surrounded"])
    assert mods == [models.raw_mod("LuckyHits", "FLAG", 1, keywords=["Attack"], condition="Surrounded")]


def test_double_damage_on_full_life_counts_the_bosss_energy_shield():
    """x1 / (1 - E/2): Atziri (EnergyShieldFromLife 33) about +4.07%; a boss without Energy Shield nothing."""
    from builder.search import models

    line = ["Deal Double Damage to Enemies that are on Full Life"]
    mods, notes = models.full_life_double_mods(line, bosses.life_share_of_pool("Atziri, the Red Queen"))
    shield = 1 - bosses.life_share_of_pool("Atziri, the Red Queen")
    assert mods[0]["name"] == "Damage" and mods[0]["type"] == "MORE"
    assert mods[0]["value"] == pytest.approx(100 * (1 / (1 - shield / 2) - 1), rel=1e-3)
    assert 4.0 < mods[0]["value"] < 4.2 and notes[0]["confidence"] == "LOW"
    none, notes = models.full_life_double_mods(line, 1.0)
    assert none == [] and "first hit" in notes[0]["basis"]
    assert models.full_life_double_mods(["Deal Double Damage"], 0.5) == ([], [])


def test_the_lowest_resistance_line_reaches_pobs_wording():
    found = translate("Elemental Damage from your Hits is Resisted by the enemy's lowest Elemental Resistance")
    assert found and found[1] == ["Elemental Damage you deal with Hits is Resisted by lowest Elemental Resistance instead"]

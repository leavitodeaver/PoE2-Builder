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


def test_the_lowest_resistance_line_reaches_pobs_wording():
    found = translate("Elemental Damage from your Hits is Resisted by the enemy's lowest Elemental Resistance")
    assert found and found[1] == ["Elemental Damage you deal with Hits is Resisted by lowest Elemental Resistance instead"]


def test_the_boss_is_on_full_life_while_its_shield_is_stripped():
    """E r / (E r + L): Atziri's shield at the same DPS is its share of the pool; a reader doubling the damage halves the
    time the shield takes; no shield, or nothing gained by the box, 0."""
    from builder.search import conditions

    shield = 1 - bosses.life_share_of_pool("Atziri, the Red Queen")
    assert conditions.enemy_full_life_share(shield, 1.0) == pytest.approx(shield)
    assert conditions.enemy_full_life_share(shield, 0.5) == pytest.approx(shield * 0.5 / (shield * 0.5 + 1 - shield))
    assert conditions.enemy_full_life_share(0.0, 0.5) == 0.0


def test_the_probe_reads_the_requests_boss_and_puts_the_box_back():
    from builder.search import conditions

    class Build:
        def __init__(self, boss):
            self.ctx = SimpleNamespace(request=SimpleNamespace(boss=boss))
            self.config, self.calls = {}, []

        def call(self, name, **kw):
            if name == "getConfig":
                return {"input": dict(self.config)}
            if name == "stats":
                return {"AverageHit": 100.0, "Speed": 2.0, "HitChance": 100.0}
            self.calls.append(kw["vars"])
            on = kw["vars"][conditions.ENEMY_FULL_LIFE_VAR]
            return {"stats": {"FullDPS": 200.0 if on else 100.0}}

    me = conditions.Conditions.__new__(conditions.Conditions)
    me.build = Build("Atziri, the Red Queen")
    share, how = me._enemy_full_life()
    shield = 1 - bosses.life_share_of_pool("Atziri, the Red Queen")
    assert share == pytest.approx(conditions.enemy_full_life_share(shield, 0.5)) and "Energy Shield" in how
    assert me.build.calls[-1] == {conditions.ENEMY_FULL_LIFE_VAR: False}
    me.build = Build("Arbiter of Ash")
    assert me._enemy_full_life()[0] == 0.0 and me.build.calls == []


def test_the_full_life_gate_reads_lines_and_supports():
    from builder.search import conditions

    me = conditions.Conditions.__new__(conditions.Conditions)
    me.build = SimpleNamespace(ctx=SimpleNamespace(gems={"Ambush": {"grantedEffectId": "SupportAmbushPlayer"},
                                                         "Spark": {"grantedEffectId": "SparkPlayer"}}))
    assert me._full_life_readers(["30% increased Critical Hit Chance against Enemies on Full Life"], {})
    assert me._full_life_readers(["100% chance to deal Double Damage against Enemies on Full Life"], {})
    assert me._full_life_readers([], {"Ambush": set()})
    assert not me._full_life_readers(["20% increased Movement Speed while on Full Life"], {"Spark": set()})


def test_the_double_damage_line_reaches_the_ledger_under_the_full_life_box():
    found = translate("Deal Double Damage to Enemies that are on Full Life")
    assert found and found[1] == ["100% chance to deal Double Damage against Enemies on Full Life"]


def test_chaos_strips_the_shield_twice_as_fast_and_bleed_or_poison_ends_full_life():
    """RESEARCH39: Chaos removes twice the Energy Shield (a hit all Chaos halves the window's ratio); Bleed / Poison
    bypass the shield, so the window ends at the first one (here 0.5 s into a fight of minutes)."""
    from builder.search import conditions

    shield = 1 - bosses.life_share_of_pool("Atziri, the Red Queen")
    plain, _ = conditions.enemy_full_life_window(shield, 1.0)
    chaos, how = conditions.enemy_full_life_window(shield, 1.0, chaos=1.0)
    assert plain == pytest.approx(shield)
    assert chaos == pytest.approx(conditions.enemy_full_life_share(shield, 0.5)) and "Chaos" in how
    life = bosses.PRESETS["Atziri, the Red Queen"].life
    cut, how = conditions.enemy_full_life_window(shield, 1.0, ailments_per_second=2.0, life=life, on=10_000.0)
    total = life * shield / (1 - shield) / 10_000.0 + life / 10_000.0
    assert cut == pytest.approx(0.5 / total) and cut < 0.001 and "Bleed / Poison" in how
    # a first ailment after the shield breaks doesn't cut the window
    late, _ = conditions.enemy_full_life_window(shield, 1.0, ailments_per_second=1e-9, life=life, on=10_000.0)
    assert late == pytest.approx(plain)


def test_every_primed_freeze_skill_feeds_the_primed_freeze_rate():
    """GEM29's row: Wave of Frost and Shattering Concoction carry has_freeze_cull as Arctic Howl does; their uses are
    summed (the main skill at its Speed, others at their group's rate)."""
    from builder.search import conditions

    class Build:
        def call(self, name, **kw):
            assert name == "groups"
            return {"groups": [{"index": 2, "gems": [{"name": "Arctic Howl"}]}]}

    me = conditions.Conditions.__new__(conditions.Conditions)
    me.build, me.main_skill = Build(), "Wave of Frost"
    me._group_uses = lambda groups, names, rows=None: {"Arctic Howl": 0.25} if "Arctic Howl" in names else {}
    me._chosen_use = lambda name: 0.0
    rate, how = me._primed_freeze({"Speed": 1.5})
    assert rate == pytest.approx(1.75) and "Wave of Frost" in how and "Arctic Howl" in how
    me.main_skill = "Spark"
    me.build = SimpleNamespace(call=lambda name, **kw: {"groups": []})
    assert me._primed_freeze({"Speed": 1.5}) is None


def test_the_main_skills_own_break_quality_adds_to_the_break_increase():
    """GEM29's row: Armour Breaker's armour_break_amount_+% (quality 20: +20%) adds to the lines' increase; a side
    skill's scale (no stats) doesn't take it."""
    from builder.search import conditions

    scale, how = conditions.break_amount_scale(["Break 30% increased Armour"], {"_break_inc_gem": 20.0})
    assert scale == pytest.approx(1.5) and "quality" in how
    assert conditions.break_amount_scale(["Break 30% increased Armour"], {})[0] == pytest.approx(1.3)


def test_brink_flags_its_groups_skills_and_needs_another_source_to_heavy_stun():
    """Brink I / II (PLANS6's open OVERCOUNT): the supported skills carry MechBrink; heavy_stun_rate gives them no
    Heavy Stun of their own; another source (other skills' buildup) finishes the bar."""
    from builder.search import models

    groups = [{"enabled": True, "gems": [{"name": "Boneshatter", "support": False}, {"name": "Brink II", "support": True}]},
              {"enabled": True, "gems": [{"name": "Leap Slam", "support": False}]}]
    mods, notes = models.brink_mods(groups)
    assert mods == [models.raw_mod(models.MECH_BRINK, "FLAG", 1, skill="Boneshatter")] and notes
    assert models.brink_mods(groups[1:]) == ([], [])
    base = {"HeavyStunBuildupAvg": 20.0, "Speed": 2.0, "HitChance": 100.0, "PhysicalHitAverage": 100.0, "_boss": "Arbiter of Ash"}
    plain = models.heavy_stun_rate(base)[0]
    alone, how = models.heavy_stun_rate({**base, f"Flag:{models.MECH_BRINK}": True})
    helped = models.heavy_stun_rate({**base, f"Flag:{models.MECH_BRINK}": True, "_buildup_others": {"HeavyStun": 0.05}})[0]
    assert plain > 0 and alone == 0.0 and "Brink" in how and helped > 0


def test_sundered_armour_raises_pobs_fully_broken_effect_at_its_share():
    """R39-16: +20% increased (stacking with Fully Broken's 20%), as FullyBrokenArmourEffect INC +100% x the share."""
    from builder.search import models

    mods, notes = models.sundered_mods(1.0, "Sunder is the main skill")
    assert mods == [models.raw_mod("FullyBrokenArmourEffect", "INC", 100.0)] and "increased" in notes[0]["confidence"]
    assert models.sundered_mods(0.5, "x")[0][0]["value"] == pytest.approx(50.0)
    assert models.sundered_mods(0.0, "") == ([], [])


def test_dexterity_gives_the_users_eight_accuracy_a_point():
    """The user's ruling (21 Nov): +8 a point through PoB's DexAccBonusOverride; none where a line sets the value."""
    from builder.search import models

    mods, notes = models.dex_accuracy_mods([], 100.0)
    assert mods == [models.raw_mod("DexAccBonusOverride", "OVERRIDE", 8.0, tag=models.DEX_ACCURACY_TAG)]
    assert "+200" in notes[0]["value"]
    line = ["Dexterity's Accuracy Bonus instead grants +12 to Accuracy Rating per Dexterity"]
    assert models.dex_accuracy_mods(line, 100.0) == ([], [])

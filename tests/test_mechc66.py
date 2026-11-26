"""MECHC66: flasks and charms start a boss fight full (flasks.STOCK's note; RESEARCH63 R63-5); the Arcane Surge support on
a skill other than the main one (conditions.ARCANE_SURGE_SUPPORT_STATS' second note; example build 10's Contagion)."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from builder.search import conditions, flasks

CHARM = {"kind": "Charm", "chargesMax": 40, "chargesUsed": 20, "duration": 4}
LIFE = {"kind": "Flask", "chargesMax": 60, "chargesUsed": 10, "duration": 4, "lifeTotal": 1000}


def test_a_full_charm_brings_its_stock_of_uses_into_the_fight():
    gain = flasks.Charges()
    assert flasks.uptime(CHARM, gain) == 0.0  # (no charge source: today's "starts empty" reading)
    full = replace(gain, fight=60.0)
    assert flasks.stock_uses(CHARM, full) == 2
    assert flasks.uptime(CHARM, full) == pytest.approx(2 * 4 / 60)
    # a short fight: the two uses cover it all, no more
    assert flasks.uptime(CHARM, replace(gain, fight=6.0)) == pytest.approx(1.0)
    # 30 of 40 a use: one use, not 1.33
    assert flasks.stock_uses({**CHARM, "chargesUsed": 30}, full) == 1
    # "50% increased Charm Charges used": 30 a use, one use
    assert flasks.stock_uses(CHARM, replace(full, charm_used_inc=50)) == 1
    # generated charges add on top of the stock
    assert flasks.uptime(CHARM, replace(full, charm=0.1)) == pytest.approx((2 + 0.1 * 60 / 20) * 4 / 60)


def test_a_full_life_flask_adds_its_stock_to_its_uses_and_says_so():
    full = replace(flasks.Charges(), fight=30.0, life_degen=1.0)  # (its own loss keeps Life below full: no dip cap)
    uses, how = flasks.uses_per_second(LIFE, full, "", "Life")
    assert uses == pytest.approx(6 / 30) and "6 starting uses over the 30 s fight" in how
    assert flasks.uses_per_second(LIFE, replace(full, fight=0.0), "", "Life")[0] == 0.0


def test_the_stock_switch_and_the_fight_length(monkeypatch):
    class Fake:
        commits = 3
        equipped = {}

        def __init__(self):
            self.asked = 0

        def call(self, method, **kw):
            assert method == "noFlaskDps"
            self.asked += 1
            return {"FullDPS": 1_000_000.0, "CombinedDPS": 900_000.0}

    build = Fake()
    from builder.search import bosses

    pool = conditions.boss_pool(bosses.DEFAULT)
    fight, how = flasks.fight_length(build)
    assert fight == pytest.approx(min(pool / 1_000_000.0, flasks.FIGHT_CAP)) and "every flask and charm off" in how
    flasks.fight_length(build)
    assert build.asked == 1  # (kept per commit)
    build.commits = 4
    flasks.fight_length(build)
    assert build.asked == 2
    zero = Fake()
    zero.call = lambda method, **kw: {"FullDPS": 0.0, "CombinedDPS": 0.0}
    assert flasks.fight_length(zero)[0] == 0.0


def test_arcane_surge_support_on_a_side_skill_counts_at_its_own_spend():
    stats = {"Mana": 1000.0, "_arcane_surge_sides": (("Contagion", 15.0, 4.0, 30.0, "0.5 uses/s, 60 Mana each"),)}
    value = conditions.arcane_surge_uptime([], stats)
    # 150 Mana every 5 s, 4 s each: 4 / 9 up (the lower bound), 80% if Mana spent during the buff counts
    assert "Contagion" in value[1] and "44%" in value[1]
    nothing = conditions.arcane_surge_uptime([], {"Mana": 1000.0, "_arcane_surge_sides": (("Contagion", 15.0, 4.0, 0.0,
                                                                                          "0 uses"),)})
    assert "spends none" in nothing[1]


def test_arcane_surge_sides_reads_each_supported_skill_but_the_main_one():
    stat_a, stat_b = conditions.ARCANE_SURGE_SUPPORT_STATS

    class Fake:
        objective = None

        def call(self, method, **kw):
            if method == "activeSkills":
                return {"skills": [
                    {"name": "Despair", "group": 4, "supportStats": {stat_a: 15, stat_b: 4000}},
                    {"name": "Contagion", "group": 9, "supportStats": {stat_a: 15, stat_b: 4000}},
                    {"name": "Essence Drain", "group": 7, "supportStats": {}}]}
            assert method == "useRates" and kw["indices"] == [9]
            return {"rates": [{"index": 9, "name": "Contagion", "speed": 2.0, "cooldown": 2.0, "manaCost": 60}]}

    sides = conditions.arcane_surge_sides(Fake(), "Despair")
    assert len(sides) == 1
    name, pct, seconds, spend, how = sides[0]
    assert (name, pct, seconds) == ("Contagion", 15, 4.0) and spend == pytest.approx(0.5 * 60)


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_real_pob_no_flask_dps_switches_the_slots_off_for_one_calculation(engine):
    """bridge.noFlaskDps: one calculation with every flask and charm slot off (The Black Cat's "7% less Lightning Damage
    taken when Hit" out of it, as test_engine_item_lines reads it; the Spark build's config already holds Onslaught, so an
    Onslaught charm moves nothing), and the slot is active again afterwards."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    search = spark_with(engine, [])
    build = search.build
    cat = next(u for u in search.ctx.data.uniques if u["name"] == "The Black Cat")
    charm = chr(10).join(line for line in search.ctx.corrected_raw(cat["name"], cat["raw"]).split(chr(10))
                         if "Unlucky" not in line)
    belt = chr(10).join(["Rarity: Rare", "Test Belt", "Plate Belt", "Item Level: 82", "Implicits: 2", "+160 to Armour",
                         "Has 3 Charm Slot"])  # (the test build has no charm slot of its own: test_engine_item_lines' belt)
    build.call("equip", slot="Belt", raw=belt, fields=[])
    bare = build.call("stats", fields=["LightningTakenHitMult"])
    build.call("equip", slot="Charm 1", raw=charm, active=True, fields=[])
    on = build.call("stats", fields=["LightningTakenHitMult"])
    assert on["LightningTakenHitMult"] == pytest.approx(bare["LightningTakenHitMult"] * 0.93, rel=1e-3)
    off = build.call("noFlaskDps", fields=["LightningTakenHitMult", "CombinedDPS"])
    print("PROBE", on, off)
    assert off["LightningTakenHitMult"] == pytest.approx(bare["LightningTakenHitMult"], rel=1e-3)
    assert off["CombinedDPS"] > 0
    assert build.call("chargeGain")["active"]["Charm 1"] is True
    assert build.call("stats", fields=["LightningTakenHitMult"]) == on


def test_the_fight_takes_the_builds_own_scored_dps_where_it_is_lower():
    from builder.search import bosses

    class Fake:
        commits = 1
        equipped = {}
        stats = {"FullDPS": 2_000_000.0}
        objective = SimpleNamespace(dps=lambda stats: 1_000_000.0)  # (scored at half PoB's Full DPS)

        def call(self, method, **kw):
            return {"FullDPS": 1_000_000.0}

    fight, how = flasks.fight_length(Fake())
    assert fight == pytest.approx(min(conditions.boss_pool(bosses.DEFAULT) / 500_000.0, flasks.FIGHT_CAP))
    assert "x0.500" in how


def test_the_stock_row_names_each_items_starting_uses_and_its_generated_charges():
    class Fake:
        equipped = {"Charm 1": SimpleNamespace(name="Test Charm", raw="r1"),
                    "Flask 1": SimpleNamespace(name="Test Flask", raw="r2")}

        def call(self, method, **kw):
            assert method == "flaskData"
            return {"results": [LIFE, CHARM]}  # (slot order: the flasks, then the charms)

    gain = replace(flasks.Charges(), fight=20.0, life_flask=0.5)
    rows = flasks.stock_note(Fake(), gain)
    assert len(rows) == 1 and rows[0]["value"] == "a 20.0 s fight"
    assert "Test Flask: 6 uses at the start, + 0.50 charges a second generated (a use every 20 s)" in rows[0]["basis"]
    assert "Test Charm: 2 uses at the start, + 0.00 charges a second generated (none" in rows[0]["basis"]
    assert flasks.stock_note(Fake(), replace(gain, fight=0.0)) == []


def test_dread_banners_flask_charges_count_at_the_banners_share():
    on = {"flask": 0.7, "flaskBanner": 0.5, "bannerOn": True}  # (0.2 of the build's own + the banner's 0.5)
    build = SimpleNamespace(banner_share=0.3)
    assert flasks.banner_flask(build, on)["flask"] == pytest.approx(0.2 + 0.3 * 0.5)
    off = {"flask": 0.2, "flaskBanner": 0.5, "bannerOn": False}  # (the box off: under 7%)
    assert flasks.banner_flask(SimpleNamespace(banner_share=0.05), off)["flask"] == pytest.approx(0.2 + 0.05 * 0.5)
    assert flasks.banner_flask(SimpleNamespace(), on)["flask"] == 0.7  # (no share read yet: PoB's)



# Savage Fury's frenzy at its earned share (models.SAVAGE_FURY's note)
FURY_SKILLS = [{"name": "Savage Fury", "skillId": "SavageFuryPlayer", "group": 3,
                "levelStats": {"wild_beast_maximum_energy": 20, "wild_beast_energy_per_hit": 1}},
               {"name": "Savage Fury", "skillId": "SavageFuryActivePlayer", "group": 3,
                "levelStats": {"wild_beast_damage_+%_final": 29}}]


def test_savage_fury_frenzy_counts_at_the_share_its_attacks_earn():
    from builder.search import models

    stats = {"Speed": 2.0, "HitChance": 100}
    # 20 Fury at 2 Fury/s = 10 s, then a 10 s frenzy and its 0.6 s release: 10 / 20.6
    up = 10 / 20.6
    mods, rows = models.savage_fury_model(FURY_SKILLS, stats, {"Attack", "UsableWhileShapeshifted"}, True, False, None)
    damage = next(m for m in mods if m["name"] == "Damage")
    assert damage["type"] == "MORE" and damage["value"] == pytest.approx(100 * ((1 + up * 0.29) / 1.29 - 1), abs=0.01)
    speed = next(m for m in mods if m["name"] == "Speed")
    assert speed["value"] == pytest.approx(-20 * (1 - up), abs=0.01)
    assert rows[0]["value"] == f"{up:.0%}" and "without it" in rows[0]["play"]
    # another Onslaught source on: only the damage comes off
    mods, _ = models.savage_fury_model(FURY_SKILLS, stats, {"Attack", "UsableWhileShapeshifted"}, True, True, None)
    assert [m["name"] for m in mods] == ["Damage"]
    # a spell main skill builds no Fury: the frenzy never comes, all of it off
    mods, rows = models.savage_fury_model(FURY_SKILLS, stats, {"Spell", "UsableWhileShapeshifted"}, False, False, None)
    assert next(m for m in mods if m["name"] == "Damage")["value"] == pytest.approx(100 * (1 / 1.29 - 1), abs=0.01)
    assert rows[0]["value"] == "0%"
    # not in the build: nothing
    assert models.savage_fury_model(FURY_SKILLS[:1], stats, set(), True, False, None) == ([], [])

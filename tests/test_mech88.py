"""MECH88 (MECH-A): Counterstancing's Projectile half; the Infused factor read in one condition state."""
import pytest

from builder.search import bosses, models


def _counter(main, stats):
    parry = models.Skill("Parry", attack=True, cast_time=1.0)
    return models.parry_model([parry, main], stats, main)


def test_counterstancing_projectile_half_needs_close_range_and_the_bosss_projectiles():
    melee_line = "Successfully Parrying a Projectile Hit grants 40% increased Damage to your next Melee Attack"
    mace = models.Skill("Mace Strike", attack=True, melee=True)
    base = {"enemySkillTime": 0.7, "Speed": 2.0, "HitChance": 100, "_main_types": {"Attack", "Melee"},
            "_texts": [melee_line], "_boss": "Xesht, We That Are One"}
    mods, notes = _counter(mace, {**base, "_distance": 2.0})
    inc = next(m for m in mods if m.get("line") == "1% increased Damage")
    want = 40 * min(1.0, (1 / models.PARRY_EVERY) * 0.50 / 2.0)
    assert inc["scale"] == pytest.approx(want) and inc["skill"] == "Mace Strike"
    note = next(n for n in notes if n["label"].startswith("Counterstancing"))
    assert "1.5 m" in note["basis"] and "without it 0" in note["play"]
    # at a ranged distance the boss fires from where it stands: none
    far = _counter(mace, {**base, "_distance": 5.0})
    assert not any(m.get("line") == "1% increased Damage" for m in far[0])
    assert "none" in next(n for n in far[1] if n["label"].startswith("Counterstancing"))["basis"]
    # a boss with no projectiles (Kulemak), and the worst-case preset (the lowest share, a bonus): none
    for boss in ("Vessel of Kulemak", bosses.WORST):
        got = _counter(mace, {**base, "_distance": 2.0, "_boss": boss})
        assert not any(m.get("line") == "1% increased Damage" for m in got[0])
    assert models.projectile_share({"_boss": "The King in the Mists"})[0] == pytest.approx(0.43 * 0.17 + 0.57 * 0.62)


def test_counterstancing_ranged_half_takes_the_lowest_melee_share_on_the_worst_case_preset():
    ranged_line = "Successfully Parrying a Melee Hit grants 40% increased Damage to your next Ranged Attack"
    bow = models.Skill("Lightning Arrow", attack=True)
    base = {"enemySkillTime": 0.7, "Speed": 2.0, "HitChance": 100, "_main_types": {"Attack", "RangedAttack"},
            "_texts": [ranged_line]}
    worst = _counter(bow, {**base, "_boss": bosses.WORST})
    low = min(v[0] for v in bosses.MELEE_SHARE.values())
    got = [m for m in worst[0] if m.get("line") == "1% increased Damage"]
    assert (got[0]["scale"] if got else 0.0) == pytest.approx(40 * min(1.0, (1 / models.PARRY_EVERY) * low / 2.0))
    ash = _counter(bow, {**base, "_boss": "Arbiter of Ash"})
    inc = next(m for m in ash[0] if m.get("line") == "1% increased Damage")
    assert inc["scale"] == pytest.approx(40 * (1 / models.PARRY_EVERY) * 0.12 / 2.0)


def test_queens_procession_stacks_follow_your_stun_threshold_and_it_counts_only_when_it_beats_the_main_skill():
    assert models.queens_procession_level(90) == 20 and models.queens_procession_level(84) == 19
    assert models.queens_procession_level(1) == 1
    # level 20: self-hit 42.5; no Armour, threshold 1105: 0.65 x 42.5 x 1.5 x 2 / 1105 = 7.5% a tick -> stun at tick 14
    rows = models.queens_procession_model(92, {"StunThreshold": 1105.0}, 50_000.0)
    row = rows[0]
    assert row["value"] == 0 and "13 ticks" in row["basis"] and "up to 13 stacks" in row["basis"]
    mult = sum(1 + 0.02 * k for k in range(13)) / 13
    sprint = (171 + 256) / 2 * 1.06 * mult / 0.5
    assert f"{sprint:,.0f} DPS while sprinting UNSCALED" in row["basis"] and "counts 0" in row["basis"]
    assert "Without it" in row["play"]
    # a huge threshold: the cap of 50 stacks
    big = models.queens_procession_model(92, {"StunThreshold": 1e9}, 50_000.0)[0]
    assert "up to 50 stacks" in big["basis"]
    # Armour cuts the self-hit and so the buildup: more ticks
    armoured = models.queens_procession_model(92, {"StunThreshold": 1105.0, "Armour": 2000.0}, 50_000.0)[0]
    assert "13 ticks" not in armoured["basis"]
    # a tiny threshold: the first self-hit stuns you
    assert "first self-hit" in models.queens_procession_model(92, {"StunThreshold": 10.0}, 50_000.0)[0]["basis"]
    # beating the main skill even unscaled: still 0, named an UNDERCOUNT
    assert "UNDERCOUNT" in models.queens_procession_model(92, {"StunThreshold": 1e9}, 10.0)[0]["basis"]


def test_not_dodge_rolled_recently_is_the_rest_of_the_roll_cadence():
    from builder.search import conditions

    line = "20% increased Armour if you haven't Dodge Rolled Recently"
    assert conditions.NOT_DODGE_RECENTLY_LINE.search(line)
    assert not conditions.DODGE_RECENTLY_LINE.search(line)
    share, how = conditions.not_dodge_rolled_recently([line])
    assert share == pytest.approx(1 - 4 / models.PARRY_EVERY) and "never roll" in how
    assert conditions.not_dodge_rolled_recently([line], {"Cast on Dodge"})[0] == 0.0  # (a roll every 2 s: always rolled)
    from builder.search.assumptions import ROLL_RELIANT

    assert not ROLL_RELIANT.search(line) and ROLL_RELIANT.search("10% increased Evasion if you've Dodge Rolled Recently")


def test_a_line_wanting_the_boss_out_of_the_presence_tries_just_beyond_its_edge():
    from builder.search import conditions

    lion = "Repeatable Attacks with this Bow Repeat +2 times if no enemies are in your Presence"
    types = {"Attack", "RangedAttack", "Projectile"}
    got = conditions.distance_candidates([lion], types, presence_radius=6.04)
    assert got[0] == 5.0 and 6.6 in got  # (ceil to 6.1, +0.5: beyond the widened Presence)
    assert 6.6 not in conditions.distance_candidates([lion], types)  # (no radius known: no such trial)


def test_dark_and_abyssal_pact_rows_read_the_minions_life_and_count_only_if_a_cast_beats_the_main_skill():
    row = models.pact_model("Dark Pact", 20, {"Skeletal Brute": 4000.0, "Skeletal Archer": 1000.0}, 3000.0, 20_000.0)[0]
    hit = ((52 + 97) / 2 + 0.13 * 4000) * 1.07
    assert row["value"] == 0 and f"{hit:,.0f} a cast UNSCALED" in row["basis"] and "Skeletal Brute" in row["basis"]
    assert "doesn't beat it" in row["basis"]  # (14,000 in the main skill's 0.7 s)
    alone = models.pact_model("Dark Pact", 20, {}, 3000.0, 20_000.0)[0]
    assert f"{((52 + 97) / 2 + 0.13 * 3000 * 2) * 1.07:,.0f} a cast" in alone["basis"]  # (your own Life, 100% more)
    abyss = models.pact_model("Abyssal Pact", 15, {"Zombie": 2000.0}, 3000.0, 20_000.0)[0]
    assert f"{((21 + 40) / 2 + 0.15 * 2000) * 1.07:,.0f} a cast" in abyss["basis"]
    assert "UNDERCOUNT" in models.pact_model("Dark Pact", 20, {"Brute": 4000.0}, 0.0, 100.0)[0]["basis"]
    assert models.PACT_LEVEL.search("Grants Skill: Level 17 Dark Pact").group(1) == "17"

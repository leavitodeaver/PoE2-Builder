"""MECHB58: a side Orb of Storms' bolts as its hits (models.side_orb_hits); Spell Echo's Echoes and Mystic
Avalanche (models.spell_echo_model); Rallying Form (models.rallying_form_model)."""
import pytest

from builder.search import conditions as c, models

GROUPS = [{"index": 1, "gems": [{"name": "Fireball"}]}, {"index": 2, "gems": [{"name": "Orb of Storms"}]}]


def orb_row(**kw):
    row = {"index": 2, "name": "Orb of Storms", "speed": 1.2, "duration": 12.0, "HitChance": 100.0,
           "ShockChancePerHit": 10.0, "ShockDuration": 8.0, "FreezeBuildupAvg": 5.0, "PoisonDPS": 12.0}
    row.update(kw)
    return row


def test_a_side_orb_reads_its_bolts_as_hits():
    rows = {2: orb_row()}
    before = c.shock_others(dict((k, dict(v)) for k, v in rows.items()), GROUPS, {"Orb of Storms"}, 1)[0]
    assert before[0][1] == pytest.approx(1 / 12)  # (one cast an Orb life: one hit per 12 s)
    said = models.side_orb_hits(rows, {2: 0.6}, 1)
    assert said and "0.60 bolts a second" in said[0]
    assert rows[2]["speed"] == 0.6 and rows[2]["triggered"] is True
    assert rows[2]["PoisonDPS"] == pytest.approx(12.0 * 0.6 / 1.2)  # (PoB's at its own Speed, cut to the bolts)
    out, _, how = c.shock_others(rows, GROUPS, {"Orb of Storms"}, 1)
    assert out == [("Orb of Storms", 0.6, pytest.approx(0.1), 8.0)]
    assert c.buildup_others(rows, GROUPS, {"Orb of Storms"}, 1)["Freeze"] == pytest.approx(0.6 * 0.05)


def test_no_cycle_read_leaves_the_row_and_the_main_orb_is_left_alone():
    rows = {2: orb_row()}
    assert models.side_orb_hits(rows, None, 1) == [] and rows[2] == orb_row()
    assert models.side_orb_hits(rows, {2: 0.0}, 1) == [] and rows[2] == orb_row()
    assert models.side_orb_hits(rows, {2: 0.6}, 2) == [] and rows[2] == orb_row()  # (the main group: PoB's Speed)
    fire = {1: {"name": "Fireball", "speed": 1.0}}
    assert models.side_orb_hits(fire, {1: 0.6}, 2) == [] and fire[1] == {"name": "Fireball", "speed": 1.0}
    # (the charge readers skip an Orb by name and count its bolts in _side_orb_gains: unchanged)
    models.side_orb_hits(rows, {2: 0.6}, 1)
    assert c.side_hit_rows(rows, GROUPS, {"Orb of Storms"}, 1) == []


MA = "Final Echo of Cascadable Spells also Cascades to either side of the targeted Area along a random axis"
CASC = {"Spell", "Cascadable", "Area"}


def spark(**support):
    return models.Skill(name="Spark", group=1, spell=True, support_stats=dict(support))


def test_spell_echo_adds_its_echo_hit():
    stats = {"Speed": 1.4, "CastRate": 1.4}
    mods, notes = models.spell_echo_model(spark(**{models.ECHO_COUNT_STAT: 1}), CASC, stats, [])
    assert mods[0]["value"] == pytest.approx(100.0) and mods[0][models.HIT_CUT_PER_USE] == pytest.approx(2.0)
    assert "x2.000" in notes[0]["value"]
    # not Cascadable, not a spell, no Echo: nothing
    assert models.spell_echo_model(spark(**{models.ECHO_COUNT_STAT: 1}), {"Spell"}, stats, []) == ([], [])
    assert models.spell_echo_model(spark(), CASC, stats, []) == ([], [])


def test_zarokhs_refrain_is_right_in_pob_while_its_cast_rate_beats_the_cooldown():
    skill = spark(**{models.ECHO_COUNT_STAT: 3})
    fast = {"Speed": 0.8, "CastRate": 1.5, "Cooldown": 5.0}  # (PoB: min(1.5, 4 / 5) = 0.8 = 4 hits a cooldown)
    assert models.spell_echo_model(skill, CASC, fast, [])[0] == []
    slow = {"Speed": 0.15, "CastRate": 0.15, "Cooldown": 5.0}  # (0.15 casts x 4 hits = 0.6 hits/s against PoB's 0.15)
    mods, _ = models.spell_echo_model(skill, CASC, slow, [])
    assert mods[0][models.HIT_CUT_PER_USE] == pytest.approx(4.0)


def test_mystic_avalanche_adds_two_side_hits_on_the_final_echo():
    stats = {"Speed": 1.0, "CastRate": 1.0, "AreaOfEffectRadius": 20}
    mods, notes = models.spell_echo_model(spark(**{models.ECHO_COUNT_STAT: 1}), CASC, stats, [MA], boss=(1.0, "boss"))
    assert mods[0][models.HIT_CUT_PER_USE] == pytest.approx(4.0)  # (1 + 1 Echo + 2 side areas)
    assert any("x2.000" == n["value"] for n in notes) and "miss" in notes[-1]["basis"]
    # a 20% chance to Echo only (Now and Again): +2 x 0.95 x 0.2 over 1 + 0.95 x 0.2
    mods, notes = models.spell_echo_model(spark(), CASC, stats, [MA], chance=(0.2, "Now and Again 20%"))
    assert mods[0][models.HIT_CUT_PER_USE] == pytest.approx(1 + 0.38 / 1.19)
    # Spell Cascade on the same spell: not counted (UNVERIFIED stacking), the Echo hit still is
    mods, notes = models.spell_echo_model(spark(**{models.ECHO_COUNT_STAT: 1, models.CASCADES_PER_SIDE: 1}), CASC,
                                          stats, [MA])
    assert mods[0][models.HIT_CUT_PER_USE] == pytest.approx(2.0) and notes[-1]["value"] == "not counted"
    # nothing Echoes: not counted
    assert models.spell_echo_model(spark(), CASC, stats, [MA])[0] == []


RALLY = "Companions in your Presence have Onslaught while you are Shapeshifted"


def test_rallying_form_gives_companions_onslaught_at_the_shifted_share():
    mods, notes = models.rallying_form_model([RALLY], 0.75, "test")
    assert mods[0]["line"].startswith("Companions have") and mods[0]["scale"] == pytest.approx(15.0)
    assert notes[0]["value"] == 0.75
    mods, notes = models.rallying_form_model([RALLY], 0.0, "no form")
    assert mods == [] and notes[0]["value"] == 0.0
    assert models.rallying_form_model(["Minions have Onslaught"], 1.0, "") == ([], [])

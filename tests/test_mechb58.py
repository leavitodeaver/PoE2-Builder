"""MECHB58: a side Orb of Storms' bolts as its hits for the other groups' hit readers (models.side_orb_hits)."""
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


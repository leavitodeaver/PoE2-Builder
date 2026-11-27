"""GEM51: Catalysing Discharge (Catalysing Elements' triggered spell): its rate from the supported skill's uses on your
own Elemental Ground (MECH-A's ground shares) under a 1 s Cooldown, its element mix, and always a rate (PoB would
otherwise count the bridge-given damage at its own speed)."""
import pytest

from builder.search import models

NAME = models.CATALYSING_DISCHARGE


def rate_of(mods):
    return next(m["value"] for m in mods if m.get("key") == "triggerRate" and m.get("skill") == NAME)


def test_no_own_ground_gives_rate_zero_with_a_note():
    mods, notes = models.catalysing_discharge_model(2.0, "Tornado 2.00 uses/s", {}, group=3)
    assert rate_of(mods) == 0.0
    assert {"kind": "Raw", "name": "Damage", "type": "MORE", "value": -100.0, "skill": NAME, "group": 3} in mods
    assert all(m.get("group") == 3 for m in mods if m.get("skill") == NAME)
    assert "never a flat rate" in notes[0]["basis"] and notes[0]["confidence"] == "LOW"


def test_one_ground_rate_through_the_cooldown_and_its_element_only():
    mods, notes = models.catalysing_discharge_model(2.0, "x", {"Cold": (0.5, "Frost Wall")}, 1.0, 2)
    # 2 uses a second, half boosted, at most one per 1 s: 2 / (ceil(1 x 2) + 1 / 0.5 - 1) = 2 / 3
    assert rate_of(mods) == pytest.approx(2 / 3, abs=1e-3)
    more = {m["name"]: m["value"] for m in mods if m.get("kind") == "Raw"}
    assert more == {"FireDamage": -100.0, "LightningDamage": -100.0}  # Cold only
    crit = next(m["value"] for m in mods if m.get("key") == "CritChance")
    assert crit == pytest.approx(11.0)
    assert "Chilled Ground 50%" in notes[0]["basis"]


def test_two_grounds_mix_and_the_per_element_row():
    grounds = {"Fire": (0.3, "a"), "Lightning": (0.3, "b")}
    mods, notes = models.catalysing_discharge_model(1.5, "x", grounds, 1.0)
    chance = 1 - 0.7 * 0.7
    assert rate_of(mods) == pytest.approx(models.cooldown_trigger_rate(1.5, chance, 1.0), abs=1e-3)
    more = {m["name"]: m["value"] for m in mods if m.get("kind") == "Raw"}
    assert more == {"FireDamage": pytest.approx(-50.0), "LightningDamage": pytest.approx(-50.0),
                    "ColdDamage": -100.0}
    assert next(m["value"] for m in mods if m.get("key") == "CritChance") == pytest.approx(7.5)
    row = 2 * models.cooldown_trigger_rate(1.5, 0.3, 1.0)
    assert f"{row:.3f} a second" in notes[0]["basis"]


def test_cooldown_caps_at_one_a_second_and_forbidden_is_zero():
    mods, _ = models.catalysing_discharge_model(10.0, "x", {"Fire": (1.0, "a")}, 1.0)
    assert rate_of(mods) == pytest.approx(1.0)
    mods, notes = models.catalysing_discharge_model(10.0, "x", {"Fire": (1.0, "a")},
                                                    forbidden={models.CATALYSING_ENTRY})
    assert rate_of(mods) == 0.0 and notes == []

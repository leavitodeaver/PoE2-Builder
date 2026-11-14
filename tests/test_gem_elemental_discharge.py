"""GEM5: Elemental Discharge's payout (models.elemental_discharge_model; an UNDERCOUNT, PoB scores it 0: its damage sits
under three config boxes the optimizer never ticks and the triggered skill has no rate).

The model reads the boss's ailment uptimes after the consume cut from build.ailment_up (MECH-A's resolver; MECH69's
change, not on main yet): these tests use a stub of that interface. The PoB test checks the bridge's useRates `config`
option gives the triggered skill's per-element hits, and puts the config back."""

from __future__ import annotations

import faulthandler
from types import SimpleNamespace

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import models

HITS = {"Fire": 1000.0, "Cold": 2000.0, "Lightning": 1500.0}


def test_no_ailment_on_the_boss_means_no_discharge():
    dps, notes = models.elemental_discharge_model(HITS, 3.0, {"Ignite": 0.0, "Shock": 0.0, "Freeze": 0.0}, spell="Spark")
    assert dps == 0.0 and notes[0]["value"] == "0" and "never inflicts" in notes[0]["basis"]
    # (nothing published yet: the same 0, with the reason)
    up, why = models.ailment_up_of(SimpleNamespace())
    assert up == {} and "not published" in why
    assert models.elemental_discharge_model(HITS, 3.0, up)[0] == 0.0


def test_one_ailment_discharges_at_the_capped_rate_with_its_element_only():
    # Shock up 50%: 4 hits/s find it 2 times a second; 1 s cooldown -> 1 / (1 + 0.5) = 0.667 discharges/s, Lightning only
    dps, notes = models.elemental_discharge_model(HITS, 4.0, {"Shock": 0.5}, spell="Spark")
    assert dps == pytest.approx(1500.0 / 1.5)
    assert "without it: 0" in notes[0]["play"] and notes[0]["confidence"] == "MEDIUM"


def test_each_element_counts_at_its_share_of_the_discharges():
    up = {"Ignite": 0.5, "Shock": 0.5, "Freeze": 0.0}
    any_up = 1 - 0.5 * 0.5
    rate = 1 / (1.0 + 1 / (10.0 * any_up))
    per = (0.5 / any_up) * 1000.0 + (0.5 / any_up) * 1500.0
    dps, _ = models.elemental_discharge_model(HITS, 10.0, up)
    assert dps == pytest.approx(rate * per)
    # (the cooldown caps it: many more hits never pass 1 discharge a second x the hit of the elements up)
    assert models.elemental_discharge_model(HITS, 1e6, up)[0] < per * 1.0 + 1e-6


def test_published_uptimes_are_read_as_shares_or_share_and_basis():
    build = SimpleNamespace(ailment_up={"Ignite": (0.4, "how"), "Shock": 1.3, "Chill": 0.9})
    up, why = models.ailment_up_of(build)
    assert up == {"Ignite": 0.4, "Shock": 1.0, "Freeze": 0.0} and "consume cut" in why


def test_element_hits_are_crit_weighted():
    row = {"CritChance": 25.0, "FireHitAverage": 100.0, "FireCritAverage": 300.0, "LightningHitAverage": 50.0}
    hits = models.discharge_element_hits(row)
    assert hits == {"Fire": pytest.approx(150.0), "Lightning": pytest.approx(50.0), "Cold": 0.0}


@pytest.fixture(scope="module")
def engine():
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


def test_use_rates_config_gives_the_discharges_hits_and_puts_the_config_back(engine):
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", level=90, budget={"gear_tier": "endgame"},
                                        objective="boss"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 1)
    search.setup()
    build = search.build
    group = build.call("setGroup", gems=[{"name": "Spark"}, {"name": "Elemental Discharge"}], main=True, fullDPS=True,
                       fields=["AverageDamage"])["group"]
    names = [s["name"] for s in group["skills"]]
    positions = [i for i, n in enumerate(names, 1) if n == models.DISCHARGE_SKILL]
    assert positions, names
    boxes = {box: True for box, _ in models.DISCHARGE_BOXES.values()}

    def row(**config):
        rows = build.call("useRates", indices=[group["index"]], actives=[positions[-1]], **config)["rates"]
        assert rows, names
        return rows[0]

    off = models.discharge_element_hits(row())
    on_row = row(config=boxes)
    on = models.discharge_element_hits(on_row)
    assert sum(off.values()) == 0.0, off  # (PoB: every element under its Consumed box)
    assert all(on[e] > 0 for e in ("Fire", "Cold", "Lightning")), on_row
    # Cold 80-120% of Intelligence against Fire 40-60%: about twice the Fire hit before modifiers
    assert on["Cold"] > on["Fire"]
    config = build.call("getConfig")["input"]
    assert not any(config.get(box) for box in boxes), config  # (put back)
    dps, notes = models.elemental_discharge_model(on, 3.0, {"Shock": 0.6}, cooldown=on_row.get("cooldown") or 1.0,
                                                  spell="Spark")
    assert dps > 0, notes


def test_bone_shrapnel_is_0_until_the_adds_life_pin_and_distance_are_known():
    dps, notes = models.bone_shrapnel_model(0.02, None, None, None, 1.0)
    assert dps == 0.0 and "add Life" in notes[0]["basis"] and "Pin share" in notes[0]["basis"]
    assert models.bone_shrapnel_model(0.0, 1e5, 1.0, 1.0, 1.0)[0] == 0.0  # (no adds: the boss dies once)
    # 0.02 kills/s, half Pinned, all near, 25% of 100,000 Life x0.8 -> 0.01 x 20,000 = 200 DPS
    dps, notes = models.bone_shrapnel_model(0.02, 100_000.0, 0.5, 1.0, 0.8)
    assert dps == pytest.approx(200.0) and notes[0]["confidence"] == "LOW" and "without it: 0" in notes[0]["play"]

"""MECHC21 (FIX_QUEUE MECH67's Scarred Faith note; an UNDERCOUNT now the recovery floor counts every source): Scarred
Faith's "5% of Physical Damage prevented Recouped as Energy Shield per enemy Power" and the passives' "N% of Physical
Damage prevented Recouped as Life": PoB parses both with leftover text and never applies them; recovery reads them at the
Physical damage the build's Armour prevents from the boss's landed hits, the ES one per the boss's Power."""
from __future__ import annotations

import pytest

from builder.search import recovery

FAITH = "5% of Physical Damage prevented Recouped as Energy Shield per enemy Power"
LIFE = "10% of Physical Damage prevented Recouped as Life"


def test_prevented_damage_recoups_per_power():
    income = recovery.Income()
    recovery.hit_income(income, {"Life": 1000}, [FAITH, LIFE], prevented=200.0, prevented_how="test", power=20.0,
                        power_how="a Unique boss")
    assert income.rates["EnergyShieldPreventedRecoup"] == pytest.approx(200.0)  # 5% x 20 Power = 100% of 200
    assert income.rates["LifePreventedRecoup"] == pytest.approx(20.0)  # 10% of 200
    none = recovery.Income()
    recovery.hit_income(none, {"Life": 1000}, [FAITH], prevented=0.0, power=20.0)
    assert none.pool("EnergyShield") == 0 and "0, no Physical damage prevented" in none.parts[0]
    # the Tribute form isn't read (Tribute isn't: its own ledger entry)
    tribute = recovery.Income()
    recovery.hit_income(tribute, {"Life": 1000}, [LIFE + " if you have at least 100 Tribute"], prevented=200.0)
    assert tribute.pool("Life") == 0


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
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_hit_rates_read_the_prevented_damage_and_the_bosss_power(engine):
    from builder.request.schema import BuildRequest, Choices
    from builder.search.assumptions import Assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"],
                                        require=Choices(skills=["Mana Tempest"])))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    hook = Assumptions(build)
    ring = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                         "+2000 to Armour", LIFE])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    rates = hook.hit_rates("Spark", {})
    more = build.call("stats", fields=["PhysicalEnemyDamage", "PhysicalDamageReduction"])
    assert more["PhysicalDamageReduction"] > 0 and rates["landed"] > 0
    assert 0 < rates["prevented"] <= more["PhysicalEnemyDamage"] * more["PhysicalDamageReduction"] / 100 * rates["landed"]
    assert rates["power"] == 20  # a Unique boss, no Power lines
    hook.life_mana_income(build.call("activeSkills")["skills"], "Spark")
    assert build.recovery_sources["LifePreventedRecoup"] == pytest.approx(0.1 * rates["prevented"], rel=1e-3)

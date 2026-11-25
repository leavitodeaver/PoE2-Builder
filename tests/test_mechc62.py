"""MECHC62: Life / Mana recovered on Culling (recovery.CULL_STATS' note)."""
from __future__ import annotations

import pytest

from builder.search import recovery


def test_cull_income_counts_the_adds_culls_once_at_the_best_carrier():
    stats = {"Life": 2000.0, "Mana": 1000.0}
    carriers = {"Killing Palm": {"recover_%_maximum_mana_on_cull": 4.0},
                "Quarterstaff Strike": {"recover_%_maximum_mana_on_cull": 12.0, "active_skill_life_gained_on_cull": 50.0}}
    income = recovery.Income()
    rows = recovery.cull_income(income, stats, carriers, 0.5, "the adds", True, "Killing Palm Culls")
    # each add Culled once, by the carrier that recovers most: 12% of 1,000 Mana, 50 Life
    assert income.rates["ManaOnEventRecovery"] == pytest.approx(0.12 * 1000 * 0.5)
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(50 * 0.5)
    assert {r["label"] for r in rows} == {"Mana recovered on Culling (Quarterstaff Strike)*",
                                          "Life recovered on Culling (Quarterstaff Strike)*"}
    # nothing in the build Culls: no income, the row says why
    income = recovery.Income()
    rows = recovery.cull_income(income, stats, carriers, 0.5, "the adds", False, "nothing in the build Culls")
    assert income.rates["ManaOnEventRecovery"] == 0 and "0 Culls: nothing in the build Culls" in rows[0]["basis"]


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


def test_real_pob_killing_palm_quality_and_soul_drain_reach_the_cull_income(engine):
    """Real PoB: the bridge's levelStats carry Killing Palm's quality stat (0.2% of maximum Mana a point) and its
    supportStats Soul Drain's 12%; Assumptions.cull_income counts the best at the preset's add Culls, and nothing (no
    bridge call) on a lone boss."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    from builder.search import bosses

    search = spark_with(engine, [])
    build = search.build
    staff = chr(10).join(["Rarity: Rare", "Test Staff", "Wrapped Quarterstaff", "Item Level: 82", "Implicits: 0"])
    build.call("equip", slot="Weapon 1", raw=staff, fields=[])  # (Killing Palm needs a Quarterstaff: POE2T7161)
    build.call("addSkillGroup", text="Killing Palm 20/20 1\nSoul Drain 1/0 1")
    search.settle()
    skills = build.call("activeSkills", levelStats=["Killing Palm"], supportStats=list(recovery.CULL_STATS))["skills"]
    palms = [s for s in skills if s["name"] == "Killing Palm"]
    assert palms, [s["name"] for s in skills]
    palm = next((s for s in palms if not s.get("disabled")), palms[0])
    assert not palm.get("disabled"), palm
    assert palm["levelStats"].get("recover_%_maximum_mana_on_cull") == pytest.approx(4.0)
    assert palm["supportStats"].get("recover_%_maximum_mana_on_cull") == pytest.approx(12.0)
    mana = float(build.call("stats", fields=["Mana"])["Mana"])
    present = {s["name"]: s for s in skills if not s.get("disabled")}
    income = recovery.Income()
    rows = search.assumptions.cull_income(income, {"Mana": mana, bosses.CULLS_STAT: 0.25, "_boss": bosses.DEFAULT},
                                          present)
    assert income.rates["ManaOnEventRecovery"] == pytest.approx(0.16 * mana * 0.25, rel=1e-6)
    assert rows and "Killing Palm" in rows[0]["label"]
    income = recovery.Income()
    assert search.assumptions.cull_income(income, {"Mana": mana, bosses.CULLS_STAT: 0.0}, present) == []


def test_real_pob_one_wildwoods_gifts_copy_is_read(engine):
    """Real PoB (RESEARCH60 R60-2: at most one Wildwood's Gifts exists): a second copy (a level-1 group beside The Hollow
    Mask's level 20, an impossible build) no longer adds its Blooms; the fastest copy alone counts."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    build = spark_with(engine, []).build
    mask = chr(10).join(["Rarity: Unique", "The Hollow Mask", "Hewn Mask", "Item Level: 82", "Implicits: 1",
                         "Grants Skill: Level 20 Wildwood's Gifts"])
    build.call("equip", slot="Helmet", raw=mask, fields=[])
    one = build.call("chargeGain")["blooms"]["rate"]
    assert 0.5 < one < 2.0
    build.call("addSkillGroup", text="Wildwood's Gifts 1/0 1")
    copies = [s for s in build.call("activeSkills")["skills"] if s["name"] == "Wildwood's Gifts" and not s.get("disabled")]
    if len(copies) < 2:
        pytest.skip(f"PoB made {len(copies)} Wildwood's Gifts copies from the group text: nothing to compare")
    two = build.call("chargeGain")["blooms"]["rate"]
    assert two == pytest.approx(one)


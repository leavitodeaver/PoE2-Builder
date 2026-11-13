"""MECHC12: Life and Mana recovery from kills, Remnants, charms and events (builder/search/recovery.py)."""

from __future__ import annotations

import pytest

from builder.search import recovery
from builder.search.recovery import Income


def test_on_kill_reads_pobs_amount_at_the_bosss_add_kills():
    income = Income()
    stats = {"LifeOnKill": 120, "ManaOnKill": 40, "Life": 3000, "Mana": 1000}
    recovery.on_kill(income, stats, ["Recover 10% of your maximum Life when an Enemy dies in your Presence"], 0.5)
    assert income.rates["LifeOnKillRecovery"] == pytest.approx(120 * 0.5 + 0.10 * 3000 * 0.5)
    assert income.rates["ManaOnKillRecovery"] == pytest.approx(40 * 0.5)
    # a lone boss: no adds, nothing (its own death ends the fight)
    lone = Income()
    recovery.on_kill(lone, stats, [], 0.0)
    assert lone.pool("Life") == lone.pool("Mana") == 0.0


def test_life_remnants_one_per_three_seconds_of_hitting_plus_kills():
    income = Income()
    skills = {recovery.LIFE_REMNANTS: {"gain": 1408, "levelStats": {recovery.LIFE_EVERY: 3000, recovery.LIFE_ON_KILL: 25}}}
    recovery.remnant_income(income, skills, {"Life": 4000}, ["Recover 3% of Maximum Life when you collect a Remnant"],
                            hits=4.0, crits=1.0, ailment_share=0.0, ailment_how="", enemies=0.2)
    rate = 1 / (3 + 1 / 4.0) + 0.25 * 0.2
    assert income.rates["LifeRemnantRecovery"] == pytest.approx(rate * 1408 + 0.03 * 4000 * rate)
    assert income.rates["ManaRemnantRecovery"] == 0.0


def test_mana_remnants_need_crits_on_an_ailing_boss():
    skills = {recovery.MANA_REMNANTS: {"gain": 738, "levelStats": {}}}
    none = Income()
    recovery.remnant_income(none, skills, {}, [], hits=5.0, crits=2.0, ailment_share=0.0, ailment_how="", enemies=0.0)
    assert none.rates["ManaRemnantRecovery"] == 0.0  # no Elemental Ailment on the boss: no Remnant
    some = Income()
    recovery.remnant_income(some, skills, {}, [], hits=5.0, crits=2.0, ailment_share=0.5, ailment_how="", enemies=0.0)
    assert some.rates["ManaRemnantRecovery"] == pytest.approx(738 / (2 + 1 / 1.0))


def test_charms_recover_at_their_own_uses():
    income = Income()
    raw = "Rarity: Magic\nThawing Charm\nRecover 113 Life when Used\n"
    recovery.charm_income(income, [("Thawing Charm", raw, 0.1, "on Freeze"), ("Idle", "Recover 50 Mana when Used", 0.0, "")],
                          {"Mana": 1000}, ["Recover 10% of maximum Mana when a Charm is used"])
    assert income.rates["CharmLifeRecoveryRate"] == pytest.approx(11.3)
    assert income.rates["CharmManaRecoveryRate"] == pytest.approx(0.10 * 1000 * 0.1)  # the idle charm's 50 Mana: never used


def test_events_at_the_builds_rates_and_vaal_pact():
    texts = ["Recover 2% of maximum Life and Mana when you use a Warcry",
             "Recover 50% of maximum Life when you Heavy Stun a Rare or Unique Enemy",
             "Recover 25 Life when you Ignite an Enemy", "Recover 3% of maximum Mana when you Shock an Enemy"]
    stats = {"Life": 2000, "Mana": 1000, "IgniteChancePerHit": 50, "ShockChancePerHit": 20}
    income = Income()
    recovery.event_income(income, stats, texts, warcries=0.1, heavy_stuns=0.05, hits=2.0)
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(0.02 * 2000 * 0.1 + 0.5 * 2000 * 0.05 + 25 * 1.0)
    assert income.rates["ManaOnEventRecovery"] == pytest.approx(0.02 * 1000 * 0.1 + 0.03 * 1000 * 0.4)
    recovery.restrict(income, ["Cannot Recover Life other than from Leech"])
    assert income.pool("Life") == 0.0 and income.pool("Mana") > 0


def test_note_names_unread_lines_and_stays_quiet_without_sources():
    assert recovery.note(Income()) == []
    income = Income()
    income.unread = recovery.unread(["Recover 3% of maximum Life for each Endurance Charge consumed"])
    (row,) = recovery.note(income)
    assert "Endurance Charge consumed" in row["basis"] and "undercount" in row["basis"]


# Engine: an item's "Grants Skill: Level 20 Life Remnants" reaches the bridge's per-Remnant amount and the hook's field ---
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


def test_life_remnants_from_an_item_reach_the_recovery_field(engine):
    """MECHC12: activeSkills' remnantGain (PoB's LifeGainPerRemnant formula) and Assumptions.life_mana_income's
    LifeRemnantRecovery for a Spark build given Life Remnants by an item; none without it."""
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
    skills = build.call("activeSkills")["skills"]
    assert not any(s["name"] == recovery.LIFE_REMNANTS for s in skills)
    hook.life_mana_income(skills, "Spark")
    assert build.recovery_sources["LifeRemnantRecovery"] == 0.0
    helmet = chr(10).join(["Rarity: Rare", "Test Crown", "Hewn Mask", "Item Level: 82", "Implicits: 1",
                           "Grants Skill: Level 20 Life Remnants"])
    build.call("equip", slot="Helmet", raw=helmet, fields=[])
    skills = build.call("activeSkills")["skills"]
    remnants = next(s for s in skills if s["name"] == recovery.LIFE_REMNANTS)
    assert (remnants.get("remnantGain") or {}).get("Life", 0) > 0, remnants
    notes = hook.life_mana_income(skills, "Spark")
    hits = build.call("stats", fields=["Speed", "HitChance"])
    rate = 1 / (3 + 1 / (hits["Speed"] * hits["HitChance"] / 100))
    assert build.recovery_sources["LifeRemnantRecovery"] == pytest.approx(rate * remnants["remnantGain"]["Life"], rel=1e-3)
    assert notes and "Life Remnants" in notes[0]["basis"]

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
    income.unread = recovery.unread(["Recover 3% of maximum Life when you lose a Spirit Charge"])
    (row,) = recovery.note(income)
    assert "Spirit Charge" in row["basis"] and "undercount" in row["basis"]
    # (MECHC13: the spent / consumed lines have rates now, so they're no longer named unread)
    assert recovery.unread(["Recover 3% of maximum Life for each Endurance Charge consumed",
                            "Recover 2% of maximum Life per Glory consumed"]) == []


def test_spent_and_consumed_recover_at_the_builds_own_rates():
    """MECHC13: charges consumed, Glory spent, Combo expended, corpses consumed and Offerings created, each at the rate
    the build gives (no source: 0, said)."""
    stats = {"Life": 4000, "Mana": 1000}
    texts = ["Recover 3% of maximum Life for each Endurance Charge consumed",
             "Recover 5% of maximum Mana when you consume a Power Charge",
             "Recover 1% of maximum Life per Glory consumed",
             "Recover 4% of Maximum Life when you expend at least 10 Combo",
             "Recover 6% of Maximum Mana when you expend at least 10 Combo",
             "Recover 2% of maximum Life when you Consume a corpse",
             "Regenerate 3% of maximum Mana over 2 seconds when you Consume a corpse",
             "Recover 10% of maximum Life when you create an Offering"]
    income = Income()
    recovery.spent_income(income, stats, texts, endurance=(0.5, "Boneshatter"), power=(0.0, "no consumer"),
                          glory=(10.0, "a Banner"), combo=(0.2, "Tempest Bell"), corpses=(0.1, "adds"),
                          offerings=(0.25, "Pain Offering on its cooldown"))
    life = 0.03 * 4000 * 0.5 + 0.01 * 4000 * 10.0 + 0.04 * 4000 * 0.2 + 0.02 * 4000 * 0.1 + 0.10 * 4000 * 0.25
    assert income.rates["LifeOnConsumeRecovery"] == pytest.approx(life)
    assert income.rates["ManaOnConsumeRecovery"] == pytest.approx(0.06 * 1000 * 0.2 + 0.03 * 1000 * 0.1)
    assert any("Power Charge consumed: 0, no consumer" in p for p in income.parts)
    assert "LifeOnConsumeRecovery" in recovery.FIELDS["Life"] and "ManaOnConsumeRecovery" in recovery.FIELDS["Mana"]
    # nothing in the build's lines: nothing added, nothing said
    quiet = Income()
    recovery.spent_income(quiet, stats, [], endurance=(1.0, "x"))
    assert quiet.pool("Life") == 0.0 and quiet.parts == []


def test_charge_model_records_the_charges_its_consumers_spend():
    """MECHC13: _charges_held puts the charges consumed a second in stats["_charge_flow"] (the lower of those made and
    the consumers' uses), for build.charge_flow; no consumer, none recorded."""
    from builder.search.conditions import _charges_held

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "EnduranceChargesMax": 3, "EnduranceChargesDuration": 15,
             "_consumers": {"Endurance": {"Boneshatter": 0.4}}}
    _charges_held("Endurance")(["Gain an Endurance Charge on Critical Hit"], stats)  # 1 crit/s made
    rate, how = stats["_charge_flow"]["Endurance"]
    assert rate == pytest.approx(0.4) and "Boneshatter" in how
    base = {k: v for k, v in stats.items() if k not in ("_consumers", "_charge_flow")}
    few = dict(base, CritChance=10, _consumers={"Endurance": {"Boneshatter": 2.0}})
    _charges_held("Endurance")(["Gain an Endurance Charge on Critical Hit"], few)  # 0.2/s made, 2/s wanted
    assert few["_charge_flow"]["Endurance"][0] == pytest.approx(0.2)
    alone = dict(base)
    _charges_held("Endurance")(["Gain an Endurance Charge on Critical Hit"], alone)
    assert "_charge_flow" not in alone


def test_glory_spent_is_uses_times_cost():
    from builder.search import models

    stats = {"Speed": 3.0, "HitChance": 100, "IgniteChanceOnHit": 100}  # (3 Ignites a second build Ancestral Cry's)
    names = ["War Banner", "Ancestral Cry"]
    plan, _ = models.glory_plan(names, stats, None)
    uses, _ = models.glory_uses(names, stats, None)
    spent, _ = models.glory_spent(names, stats, None)
    assert uses == pytest.approx(sum(u for u, _ in plan.values()))
    assert spent == pytest.approx(sum(u * c for u, c in plan.values()))
    assert set(plan) == {"Ancestral Cry"} and plan["Ancestral Cry"][1] == 100  # (no Attack hits: no Banner Glory)
    assert spent == pytest.approx(plan["Ancestral Cry"][0] * 100) and spent > 0


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


def test_spent_and_consumed_lines_reach_the_recovery_field(engine):
    """MECHC13: Assumptions.spent_rates on a real build: the Endurance Charges its consumers spend (build.charge_flow,
    set here as Conditions would) give LifeOnConsumeRecovery; a Spark build has no Glory, Combo, corpse or Offering skill,
    so those lines give 0 and say why."""
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
                         "Recover 3% of maximum Life for each Endurance Charge consumed",
                         "Recover 1% of maximum Life per Glory consumed",
                         "Recover 4% of Maximum Life when you expend at least 10 Combo",
                         "Recover 2% of maximum Life when you Consume a corpse",
                         "Recover 10% of maximum Life when you create an Offering"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    build.charge_flow = {"Endurance": (0.5, "a test consumer")}
    skills = build.call("activeSkills")["skills"]
    notes = hook.life_mana_income(skills, "Spark")
    life = build.call("stats", fields=["Life"])["Life"]
    assert build.recovery_sources["LifeOnConsumeRecovery"] == pytest.approx(0.03 * life * 0.5, rel=1e-3)
    basis = notes[0]["basis"]
    assert "Endurance Charge consumed x 0.500/s" in basis
    for said in ("no Glory skill", "no Combo skill", "corpse skills none", "no Offering skill"):
        assert said in basis, said


def test_glory_reads_its_cooldown_and_the_builds_glory_lines():
    """MECHC13 (the flat-rate rule): a Glory skill is used at most once per its own cooldown; "% increased Glory
    generation" (Banner-only for "for Banner Skills"; Tribute's not counted), "generate N Glory every 2 seconds", the
    Presence line, and the chances not to consume / to retain 40% change what a use needs."""
    from builder.search import models

    stats = {"Speed": 3.0, "HitChance": 100, "IgniteChanceOnHit": 100}
    base, _ = models.glory_plan(["Ancestral Cry"], stats, None, texts=[])
    uses = base["Ancestral Cry"][0]
    slow = models.Skill("Ancestral Cry", cooldown=1 / uses * 4)
    capped, how = models.glory_plan(["Ancestral Cry"], stats, None, [slow], [])
    assert capped["Ancestral Cry"][0] == pytest.approx(uses / 4) and "cooldown" in how
    more, how = models.glory_plan(["Ancestral Cry"], stats, None, texts=["50% increased Glory generation"])
    held = models.GLORY_INSTANCE["Ancestral Cry"]
    glory = 100 / (1 / uses - held)  # (Glory a second before the line)
    assert more["Ancestral Cry"][0] == pytest.approx(1 / (held + 100 / (glory * 1.5))) and "+50% Glory" in how
    banner_only, _ = models.glory_plan(["Ancestral Cry"], stats, None, texts=["50% increased Glory generation for Banner Skills"])
    assert banner_only["Ancestral Cry"][0] == pytest.approx(uses)
    _, how = models.glory_plan(["Ancestral Cry"], stats, None,
                               texts=["50% increased Glory generation if you have at least 100 Tribute"])
    assert "Tribute isn't modelled" in how
    flat, _ = models.glory_plan(["Ancestral Cry"], stats, None,
                                texts=["Skills which require Glory generate 4 Glory every 2 seconds"])
    assert flat["Ancestral Cry"][0] == pytest.approx(1 / (held + 100 / (glory + 2)))
    cheap, _ = models.glory_plan(["Ancestral Cry"], stats, None, texts=["50% chance to not consume Glory"])
    assert cheap["Ancestral Cry"] == pytest.approx((1 / (held + 50 / glory), 50.0))
    lines = models.glory_lines(["Skills gain 1 Glory every 2 seconds for each Rare or Unique monster in your Presence",
                                "25% chance to retain 40% of Glory on use"])
    assert lines["presence"] == pytest.approx(0.5) and lines["retain"] == pytest.approx(0.25)
    # (PoB's ModCache wordings: "Skills have a N% chance ...", "Banners gain N Glory per second" on Banners only)
    lines = models.glory_lines(["Skills have a 15% chance to not consume Glory", "Banners gain 5 Glory per second",
                                "Allies in your Presence have 30% increased Glory generation"])
    assert lines["not_consume"] == pytest.approx(0.15) and lines["banner_flat"] == 5.0 and lines["inc"] == 0.0
    plain, _ = models.glory_plan(["Ancestral Cry"], stats, None, texts=["Banners gain 5 Glory per second"])
    assert plain["Ancestral Cry"][0] == pytest.approx(uses)


def test_indigon_stops_mana_flask_recovery():
    """MECHC14 (COV17, an OVERCOUNT): "Mana Recovery other than Regeneration cannot Recover Mana" (Indigon) leaves a Mana
    Flask nothing; a Life Flask still recovers."""
    from dataclasses import replace

    from builder.search.flasks import Charges, recovery_per_second

    gain = Charges(mana_flask=2.0, life_flask=2.0)
    mana = {"kind": "Flask", "manaTotal": 300, "chargesUsed": 10, "duration": 3}
    life = {"kind": "Flask", "lifeTotal": 300, "chargesUsed": 10, "duration": 3}
    assert recovery_per_second(mana, gain, "Mana") > 0
    blocked = replace(gain, mana_regen_only=True)
    assert recovery_per_second(mana, blocked, "Mana") == 0.0
    assert recovery_per_second(life, blocked, "Life") == recovery_per_second(life, gain, "Life") > 0
    assert Charges.from_bridge({"manaRegenOnly": True}).mana_regen_only is True
    assert Charges.from_bridge({}).mana_regen_only is False


def test_lose_all_charges_on_reaching_maximum_counts_the_average_held():
    """MECHC14 (COV18, an OVERCOUNT): Redflare Conduit's "Lose all Power Charges on reaching maximum Power Charges": the
    count climbs 0 -> maximum and drops, so a build gaining 1 a second with a maximum of 3 holds about 1 (0, 1, 2 for a
    gain each), not the 3 it kept before; a slow gainer whose charges expire holds less still."""
    from builder.search.conditions import _charges_held, charges_with_reset

    assert charges_with_reset(1.0, 1e9, 3) == pytest.approx(1.0)  # (N - 1) / 2, nothing expires
    assert charges_with_reset(1.0, 1e9, 5) == pytest.approx(2.0)
    assert charges_with_reset(1.0, 1e9, 4, extra=1.0) == pytest.approx(1.0)  # two gains a cycle: 0, then 2
    assert charges_with_reset(0.05, 15, 3) < charges_with_reset(1.0, 15, 3)
    assert charges_with_reset(1.0, 1e9, 3, fill=0.5) < 1.0
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "PowerChargesDuration": 15}
    line = "Gain a Power Charge on Critical Hit"
    assert _charges_held("Power")([line], stats)[0] == 3
    count, basis = _charges_held("Power")([line, "Lose all Power Charges on reaching maximum Power Charges"], stats)
    assert count == pytest.approx(1.0) and "drops to 0" in basis
    # another kind's line leaves Power alone
    assert _charges_held("Power")([line, "Lose all Frenzy Charges on reaching maximum Frenzy Charges"], stats)[0] == 3


def test_hit_block_and_cast_lines_recover_at_the_builds_rates():
    """MECHC14 (AUDIT27 A27-32, silent UNDERCOUNTS): "Recover N% of Missing Life before being Hit by an Enemy" at the
    boss's landing hits (the last hit's Life alone missing: LOW, a lower bound), "Regenerate N% of your Armour as Life over
    1 second when you Block" at the boss's Blocked hits, "Regenerate N Life over 1 second when you Cast a Spell" and
    "Leeches N% of maximum Life when you Cast a Spell" at the casts; "when Stunned" is named, not counted."""
    texts = ["Recover 20% of Missing Life before being Hit by an Enemy",
             "Regenerate 2% of your Armour as Life over 1 second when you Block",
             "Regenerate 7 Life over 1 second when you Cast a Spell", "Leeches 1% of maximum Life when you Cast a Spell",
             "Regenerate 3% of maximum Life over 1 second when Stunned"]
    stats = {"Life": 2000, "Armour": 10000}
    income = Income()
    recovery.hit_income(income, stats, texts, landed=0.5, life_per_hit=800, blocked=0.25, casts=2.0, cast_how="test")
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(0.2 * 800 * 0.5 + 0.02 * 10000 * 0.25 + (7 + 20) * 2.0)
    assert recovery.unread(texts) == ["Life / Energy Shield when Stunned"]
    capped = Income()
    recovery.hit_income(capped, stats, texts[:1], landed=1.0, life_per_hit=5000)  # (never more than all of Life missing)
    assert capped.rates["LifeOnEventRecovery"] == pytest.approx(0.2 * 2000)
    none = Income()
    recovery.hit_income(none, stats, texts[:4], cast_how="the main skill isn't a Spell")
    assert none.pool("Life") == 0 and len(none.parts) == 3 and "isn't a Spell" in none.parts[2]


def test_hit_and_cast_lines_reach_the_recovery_field(engine):
    """MECHC14: Assumptions.hit_rates on a real Spark build: its casts give the cast lines' Life; the boss's landing hits
    give the missing-Life line's."""
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
                         "Regenerate 7 Life over 1 second when you Cast a Spell",
                         "Recover 20% of Missing Life before being Hit by an Enemy"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    skills = build.call("activeSkills")["skills"]
    stats = build.call("stats", fields=["Speed", "Life"])
    rates = hook.hit_rates("Spark", {})
    assert rates["casts"] == pytest.approx(stats["Speed"]) and rates["casts"] > 0
    assert rates["landed"] > 0 and 0 < rates["life_per_hit"]
    notes = hook.life_mana_income(skills, "Spark")
    expected = 7 * rates["casts"] + 0.2 * min(rates["life_per_hit"], stats["Life"]) * rates["landed"]
    assert build.recovery_sources["LifeOnEventRecovery"] == pytest.approx(expected, rel=1e-3)
    assert "Spell cast" in notes[0]["basis"] and "Missing Life before a hit" in notes[0]["basis"]


def test_remnant_pickup_lines_count_every_remnant_and_the_double_pickup_lines():
    """MECHC15 (AUDIT26 A26-3; COV17): "Recover N% of maximum Life when you collect a Remnant" fires on the build's
    other Remnants too (Infusion, Ward, Breach ones: `others`), and the additional-Remnant / reappear / double-pickup
    lines multiply every Remnant collected, the Life Remnants' own gain included."""
    from builder.search.assumptions import remnant_multiplier

    line = ["Recover 3% of Maximum Life when you collect a Remnant"]
    alone = Income()  # no Life / Mana Remnants: the pickup line still reads the other Remnants
    recovery.remnant_income(alone, {}, {"Life": 4000}, line, hits=4.0, crits=1.0, ailment_share=0.0, ailment_how="",
                            enemies=0.0, others=(0.25, "Orb of Storms 0.08/s; Remnants of Kalguur 0.17/s"))
    assert alone.rates["LifeRemnantRecovery"] == pytest.approx(0.03 * 4000 * 0.25)
    assert "Remnants of Kalguur" in " ".join(alone.parts)
    none = Income()  # no Remnant at all: nothing
    recovery.remnant_income(none, {}, {"Life": 4000}, line, hits=4.0, crits=1.0, ailment_share=0.0, ailment_how="",
                            enemies=0.0)
    assert none.rates["LifeRemnantRecovery"] == 0.0
    texts = ["10% chance to create an additional Remnant", "Remnants you create reappear once, 3 seconds after being collected",
             "15% chance for Remnants you create to grant their effects twice"]
    times, how = remnant_multiplier(texts)
    assert times == pytest.approx(1.10 * 2 * 1.15) and "reappearing" in how
    assert remnant_multiplier([]) == (1.0, "")
    skills = {recovery.LIFE_REMNANTS: {"gain": 1000, "levelStats": {}}}
    both = Income()
    recovery.remnant_income(both, skills, {"Life": 4000}, line, hits=4.0, crits=1.0, ailment_share=0.0,
                            ailment_how="", enemies=0.0, others=(0.25, "Orb of Storms"), multiplier=times,
                            multiplier_how=how)
    own = 1 / (3 + 1 / 4.0) * times
    assert both.rates["LifeRemnantRecovery"] == pytest.approx(own * 1000 + 0.03 * 4000 * (own + 0.25 * times))


def test_remnant_pickup_line_reads_an_orb_of_storms_builds_infusion_remnants(engine):
    """MECHC15 (A26-3) on a real build: a Spark build with Orb of Storms (a Lightning Infusion Remnant as each Orb
    expires) and a pickup line on a ring gets LifeRemnantRecovery from those Remnants; nothing without the line."""
    from builder.request.schema import BuildRequest, Choices
    from builder.search.assumptions import Assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"],
                                        require=Choices(skills=["Orb of Storms"])))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    hook = Assumptions(build)
    skills = build.call("activeSkills")["skills"]
    assert any(s["name"] == "Orb of Storms" and not s.get("disabled") for s in skills)
    hook.life_mana_income(skills, "Spark")
    assert build.recovery_sources["LifeRemnantRecovery"] == 0.0
    ring = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                         "Recover 3% of Maximum Life when you collect a Remnant"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    skills = build.call("activeSkills")["skills"]
    notes = hook.life_mana_income(skills, "Spark")
    life = build.call("stats", fields=["Life"])["Life"]
    supply, used = hook.remnant_supply(skills, skip=recovery.REMNANT_SKILLS)
    assert supply > 0 and any("Orb of Storms" in u for u in used)
    assert build.recovery_sources["LifeRemnantRecovery"] == pytest.approx(0.03 * life * supply, rel=1e-3)
    assert "Orb of Storms" in notes[0]["basis"]

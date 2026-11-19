"""MECHC12: Life and Mana recovery from kills, Remnants, charms and events (builder/search/recovery.py)."""

from __future__ import annotations

import math

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
    "Leeches N% of maximum Life when you Cast a Spell" at the casts; "when Stunned" at the Stuns given (none here: 0, said; MECHC15)."""
    texts = ["Recover 20% of Missing Life before being Hit by an Enemy",
             "Regenerate 2% of your Armour as Life over 1 second when you Block",
             "Regenerate 7 Life over 1 second when you Cast a Spell", "Leeches 1% of maximum Life when you Cast a Spell",
             "Regenerate 3% of maximum Life over 1 second when Stunned"]
    stats = {"Life": 2000, "Armour": 10000}
    income = Income()
    recovery.hit_income(income, stats, texts, landed=0.5, life_per_hit=800, blocked=0.25, casts=2.0, cast_how="test")
    # (MECHC16, A28-1: the Block and cast lines' 50 + 54 Life/s refill 208 Life in the 2 s between landed hits)
    others = 0.02 * 10000 * 0.25 + (7 + 20) * 2.0
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(0.2 * (800 - others / 0.5) * 0.5 + others)
    assert recovery.unread(texts) == []  # ("when Stunned" counts since MECHC15: no Stun given here, so 0 and said)
    assert "when Stunned: 0" in " ".join(income.parts)
    capped = Income()
    recovery.hit_income(capped, stats, texts[:1], landed=1.0, life_per_hit=5000)  # (never more than all of Life missing)
    assert capped.rates["LifeOnEventRecovery"] == pytest.approx(0.2 * 2000)
    none = Income()
    recovery.hit_income(none, stats, texts[:4], cast_how="the main skill isn't a Spell")
    assert none.pool("Life") == 0 and len(none.parts) == 3 and "isn't a Spell" in none.parts[1]  # (the missing-Life
    # line is read last since MECHC16: the cast / Block / Stun lines refill Life before it)


def test_missing_life_before_a_hit_is_the_hit_less_what_refills_life_between_hits():
    """MECHC16 (AUDIT28 A28-1, an OVERCOUNT): "Recover N% of Missing Life before being Hit" counted the last hit's Life as
    missing whatever refilled it. Now the Life missing before a hit is the hit less what the build refills in the time
    between landed hits (regeneration, leech, flasks, recoup, other sources, net of Life costs), at least 0."""
    line = ["Recover 20% of Missing Life before being Hit by an Enemy"]
    stats = {"Life": 2000}
    bare = Income()  # nothing refills: the whole hit is missing
    recovery.hit_income(bare, stats, line, landed=0.5, life_per_hit=800)
    assert bare.rates["LifeOnEventRecovery"] == pytest.approx(0.2 * 800 * 0.5)
    part = Income()  # 100 Life/s over the 2 s between hits: 600 of the 800 still missing
    recovery.hit_income(part, stats, line, landed=0.5, life_per_hit=800, refill=100, refill_how="regeneration 100")
    assert part.rates["LifeOnEventRecovery"] == pytest.approx(0.2 * 600 * 0.5)
    assert "regeneration 100" in " ".join(part.parts)
    full = Income()  # 500 Life/s: Life is full again before each hit, so the line gives nothing
    recovery.hit_income(full, stats, line, landed=0.5, life_per_hit=800, refill=500)
    assert full.pool("Life") == 0 and "full again before each hit" in full.parts[0]


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
    refill = rates["refill"] + 7 * rates["casts"]  # (MECHC16, A28-1: Life refilled between the boss's landed hits)
    expected = 7 * rates["casts"] + 0.2 * max(min(rates["life_per_hit"], stats["Life"]) - refill / rates["landed"], 0.0)         * rates["landed"]
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


def test_ward_damaged_recently_share_needs_runic_reprieve_and_ward_to_pay():
    """MECHC15 (AUDIT26 A26-1, COV18): "...if your Runic Ward has been damaged Recently" is up the share of the fight
    within 4 s of a boss hit Runic Reprieve takes into Ward: 0 without Reprieve; with it, the hits its Ward
    regeneration pays for, at 1 - exp(-4 x hits a second); more regeneration, more of the fight."""
    import math

    from builder.search import objective
    from builder.search.assumptions import RUNIC_REPRIEVE, Assumptions

    class Stand:
        def __init__(self, regen):
            self.regen = regen

        def call(self, what, **kw):
            assert what == "stats"
            return {"enemySkillTime": 1.0, "PhysicalTakenHit": 1000.0, "WardRegenRecovery": self.regen, "Ward": 2000.0,
                    "Life": 3000.0}

    hook = Assumptions.__new__(Assumptions)
    hook.build = Stand(5.0)
    none, how = hook.ward_damaged_share([{"name": "Spark"}])
    assert none == 0.0 and "no Runic Reprieve" in how
    skills = [{"name": RUNIC_REPRIEVE, "castTime": 0.5}]
    low, how = hook.ward_damaged_share(skills)
    stats = hook.build.call("stats")
    every = objective.benefit_every(stats)
    taken, landed, _ = objective.boss_taken(stats, every)
    per_hit = sum(taken.values()) / landed
    paid = min(1 / every, 5.0 / per_hit)
    assert low == pytest.approx(1 - math.exp(-4 * paid)) and 0 < low < 1
    hook.build = Stand(1e6)
    high, _ = hook.ward_damaged_share(skills)
    assert high == pytest.approx(1 - math.exp(-4 / every)) and high > low
    hook.build = Stand(0.0)
    assert hook.ward_damaged_share(skills)[0] == 0.0  # no Ward regenerated: no blocked hit is paid for


def test_life_when_stunned_counts_the_bosss_hits_that_light_stun_you(monkeypatch):
    """MECHC15 (AUDIT27 A27-32): "Regenerate N% of maximum Life over 1 second when Stunned" at the boss's landed hits x
    the game's Light Stun chance per damage type (hit / Stun Threshold, a monster's Physical 100% more since MECHC26 (R4:
    KeywordPopups Stun and 0.3.0; was 50%), under 15% none), less the
    build's Stun avoidance; nothing for a build that can't be Stunned."""
    from builder.search import bosses

    monkeypatch.setattr(bosses, "melee_share", lambda stats=None: (0.0, "none (test)"), raising=False)
    stats = {"StunThreshold": 2000.0, "StunAvoidChance": 0.0, "Life": 4000.0}
    weights = {"Physical": 0.5, "Fire": 0.5}
    sizes = {"Physical": 600.0, "Fire": 200.0}  # Physical 600 x 2 / 2000 = 60%; Fire 10%: under 15%, none
    rate, how = recovery.stuns_taken(stats, sizes, weights, 2.0)
    # (MECHC27, L1-1: 50% more threshold per Stun in the past 4 s) s = 2 x 0.5 x 0.6 / (1 + 0.5 x 4 s), so 2s^2 + s - 0.6 = 0
    assert rate == pytest.approx((-1 + math.sqrt(1 + 8 * 0.6)) / 4, rel=1e-6) and "Physical 100% more" in how
    assert "past 4 s" in how and "36.0 a minute without it" in how
    half, _ = recovery.stuns_taken({**stats, "StunAvoidChance": 50.0}, sizes, weights, 2.0)
    assert half == pytest.approx((-1 + math.sqrt(1 + 8 * 0.3)) / 4, rel=1e-6)
    assert recovery.stuns_taken({**stats, "StunAvoidChance": 100.0}, sizes, weights, 2.0)[0] == 0.0
    income = Income()
    recovery.hit_income(income, stats, ["Regenerate 3% of maximum Life over 1 second when Stunned"], stunned=rate,
                        stunned_how=how)
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(0.03 * 4000 * rate)
    none = Income()
    recovery.hit_income(none, stats, ["Regenerate 3% of maximum Life over 1 second when Stunned"])
    assert none.rates["LifeOnEventRecovery"] == 0.0 and "when Stunned: 0" in " ".join(none.parts)
    assert not recovery.unread(["Regenerate 3% of maximum Life over 1 second when Stunned"])
    # (MECHC17) the Energy Shield form, at the same Stuns, in its own field (no carrier found yet: read all the same)
    es_line = ["Regenerate 4% of maximum Energy Shield over 1 second when Stunned"]
    shield = Income()
    recovery.hit_income(shield, {**stats, "EnergyShield": 2000}, es_line, stunned=rate, stunned_how=how)
    assert shield.rates["EnergyShieldOnEventRecovery"] == pytest.approx(0.04 * 2000 * rate)
    assert shield.rates["LifeOnEventRecovery"] == 0.0 and not recovery.unread(es_line)
    bare = Income()
    recovery.hit_income(bare, stats, es_line, stunned=rate, stunned_how=how)
    assert bare.rates["EnergyShieldOnEventRecovery"] == 0.0 and "no Energy Shield" in " ".join(bare.parts)


def test_sacrifice_corpses_reach_the_corpse_recovery_line(engine):
    """MECHC15 (MECHB19's Sacrifice corpses, AUDIT27 A27-4): "Recover N% of maximum Life when you Consume a corpse" reads
    Sacrifice's corpses (the build's own undead Reviving minion deaths) through models.corpse_supply; before, the
    recovery path's stats lacked models.SACRIFICE_CORPSES_STAT (set only in Assumptions.models), so a lone boss gave 0."""
    from builder.request.schema import BuildRequest, Choices
    from builder.search import models
    from builder.search.assumptions import Assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Detonate Dead",
                                        require=Choices(skills=["Sacrifice", "Skeletal Warrior Minion"])))
    skeleton = enumerate_skeletons(Context(payload), ["Detonate Dead"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    hook = Assumptions(build)
    ring = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                         "Recover 2% of maximum Life when you Consume a corpse"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    skills = build.call("activeSkills")["skills"]
    present = {s["name"] for s in skills if not s.get("disabled")}
    assert {"Sacrifice", "Detonate Dead"} <= present and any("Skeletal Warrior" in n for n in present), present
    stats = build.call("stats", fields=["Life"])
    rates = hook.spent_rates(skills, "Detonate Dead", stats, ["recover 2% of maximum life when you consume a corpse"])
    corpses, how = rates["corpses"]
    found = [models.Skill.of(s) for s in skills if not s.get("disabled")]
    main = next(s for s in found if s.name == "Detonate Dead")
    own, _ = models.sacrifice_corpses(found, main, {**build.call("stats", fields=models.MODEL_FIELDS),
                                                    "_minion_lives": getattr(build, "minion_lives", {})})
    # (the wiring this checks: Sacrifice's own figure reaches the line and its note. On this skeleton MECHB19's
    # sacrifice_corpses itself gives 0, "no undead Reviving minions" (POE2T1656): routed to MECH-B)
    assert "Sacrifice" in how and f"Sacrifice {own:.3f} corpses/s" in how, how
    assert corpses <= own + 1e-9


def test_charms_used_when_stunned_fire_at_the_bosss_light_stuns():
    """MECHC16 (CLAUDE.md's no-flat-rates rule): charms "Used when you become Stunned" assumed one Stun every 10 s. Now
    they fire at the boss's hits that Light Stun you (recovery.boss_stuns: its landed hits against your Stun Threshold,
    from chargeGain's stunThreshold / stunAvoid / deflect), the same rate the "when Stunned" recovery line reads; a build
    the boss can't Stun gets 0, said."""
    from builder.search.flasks import Charges, trigger_rate

    data = {"enemySkillTime": 1.0, "takenByType": {"Physical": 3000, "Fire": 0, "Cold": 0, "Lightning": 0, "Chaos": 0},
            "stunThreshold": 4000, "bossDamage": {"Physical": 1}}
    boss = "Olroth, Origin of the Fall"  # (MECHC19: a named boss; the worst case reads the lowest real boss's Stuns)
    gain = Charges.from_bridge(data, 0, boss)
    hit = {"enemySkillTime": 1.0, "EvadeChance": 0.0, "EffectiveBlockChance": 0.0, "PhysicalTakenHit": 3000,
           "StunThreshold": 4000, "_boss": boss}
    expected, _ = recovery.boss_stuns(hit)
    assert gain.stuns == pytest.approx(expected) and 0 < expected < 1
    rate, how = trigger_rate("Used when you become Stunned", gain)
    assert rate == pytest.approx(expected) and "Light Stun" in how
    immune = Charges.from_bridge({**data, "stunAvoid": 100}, 0, boss)
    rate, how = trigger_rate("Used when you become Stunned", immune)
    assert rate == 0.0 and "can't be Stunned" in how
    # (MECHC17) the other ailments read the boss's own rate (Charges.ailments); Slow keeps its labelled rate
    assert trigger_rate("Used when you become Frozen", Charges(boss_hits=1.0, boss_types=("Cold",)))[0] == 0.0
    assert trigger_rate("Used when you are affected by a Slow", Charges(boss_hits=1.0))[0] == 0.1


def test_ailment_charms_fire_at_the_bosss_own_ailment_rate():
    """MECHC17 (CLAUDE.md's no-flat-rates rule): charms "Used when you become Ignited / Shocked / Frozen / Poisoned" and
    "start Bleeding" assumed one per 10 s on any boss dealing the type. Now the boss's own rate (boss_hits.ailment_rate):
    its landed hits of the type x the game's chance against your Ailment Threshold x your avoidance; 0 where no boss skill
    inflicts it (Poison on every pinnacle, Bleeding but on Olroth)."""
    from builder.search import boss_hits
    from builder.search.flasks import Charges, trigger_rate

    data = {"enemySkillTime": 0.7, "takenByType": {"Physical": 3000, "Fire": 2000, "Cold": 1500, "Lightning": 2500,
                                                   "Chaos": 500},
            "bossDamage": {t: 1 for t in ("Physical", "Fire", "Cold", "Lightning", "Chaos")}, "ailmentThreshold": 3000,
            "ailmentAvoid": {"Ignite": 0, "Shock": 0, "Freeze": 0, "Bleed": 0, "Poison": 0}}

    def rate(boss, trigger, **avoid):
        return trigger_rate(trigger, Charges.from_bridge({**data, "ailmentAvoid": {**data["ailmentAvoid"], **avoid}},
                                                         0, boss))

    ash, divinity, olroth = "Arbiter of Ash", "Arbiter of Divinity", "Olroth, Origin of the Fall"
    assert rate(ash, "Used when you become Ignited")[0] > 0
    for trigger in ("Used when you become Shocked", "Used when you become Frozen", "Used when you start Bleeding"):
        assert rate(ash, trigger)[0] == 0.0 and "no source, 0" in rate(ash, trigger)[1]
    # Shock: 1% per 4% of the threshold dealt, x the Arbiter of Divinity's own Shock chance stats; avoidance halves it
    shock, how = rate(divinity, "Used when you become Shocked")
    assert shock > 0 and "Ailment Threshold" in how and boss_hits._factor(divinity, boss_hits.SHOCK_FACTORS, "Lightning") > 1
    assert rate(divinity, "Used when you become Shocked", Shock=50)[0] == pytest.approx(shock / 2)
    assert rate(divinity, "Used when you become Shocked", Shock=100)[0] == 0.0
    # Freeze: a 1,500 Cold hit builds 2 x 1,500 / 3,000 = 100%: every Olroth Cold hit that lands Freezes
    frozen, how = rate(olroth, "Used when you become Frozen")
    assert frozen > 0 and "UNVERIFIED" in how
    # (MECHC18) the counted reading is the lower one: a single hit must fill the buildup. A 1,000 Cold hit builds 2 x
    # 1,000 / 3,000 x Olroth's mean buildup factor (1.14) = 76%: none counted; "with buildup kept" gives the upper row
    low, how = trigger_rate("Used when you become Frozen", Charges.from_bridge(
        {**data, "takenByType": {**data["takenByType"], "Cold": 1000}}, 0, olroth))
    assert low == 0.0 and "with buildup kept: " in how and "with buildup kept: 0.0" not in how
    assert rate(olroth, "Used when you start Bleeding")[0] > 0
    for boss in (ash, divinity, olroth, None):
        assert rate(boss, "Used when you become Poisoned")[0] == 0.0
    # (MECHC19, AUDIT29 A29-1) the worst-case pinnacle reads the LOWEST real boss's rate of a gain: 0 Bleeds (11 of 12
    # never Bleed you), 0 Shocks (the Arbiter of Ash deals no Lightning)
    for trigger in ("Used when you start Bleeding", "Used when you become Shocked"):
        low, how = rate(None, trigger)
        assert low == 0.0 and "lowest real boss" in how, how



def test_slow_charms_read_the_bosss_chills_and_slow_skills():
    """MECHC18 (CLAUDE.md's no-flat-rates rule): "Used when you are affected by a Slow" fired once per 10 s on any boss.
    Now Chill (KeywordPopups: an Ailment that Slows; Cold hits of at least 30% of your Ailment Threshold Chill, 2 s on
    players) and the boss's Slow skills at their cooldowns (Zarokh's Temporal Ball, Olroth's / Atziri's Maims); only a boss
    with neither keeps the labelled flat rate."""
    from builder.search import boss_hits
    from builder.search.flasks import Charges, trigger_rate

    data = {"enemySkillTime": 0.7, "takenByType": {"Physical": 3000, "Cold": 1500},
            "bossDamage": {"Physical": 1, "Cold": 1}, "ailmentThreshold": 3000, "ailmentAvoid": {"Chill": 0}}
    slow = "Used when you are affected by a Slow"

    def rate(boss, **over):
        return trigger_rate(slow, Charges.from_bridge({**data, **over}, 0, boss))

    # Olroth: Cold hits of 50% of the threshold Chill; its Maims add theirs
    olroth, how = rate("Olroth, Origin of the Fall")
    assert olroth > 0 and "Chill" in how and "Maim" in how
    # under the 30% floor: only the Maims; immune to Chill: the same
    small = rate("Olroth, Origin of the Fall", takenByType={"Physical": 3000, "Cold": 600})[0]
    immune = rate("Olroth, Origin of the Fall", ailmentAvoid={"Chill": 100})[0]
    assert 0 < small < olroth and immune == pytest.approx(small)
    # a hit while still Chilled refreshes the 2 s Chill: new Chills = c x e^(-2c)
    stats = {"_boss": "The Raven Trickster", "ColdTakenHit": 1500, "AilmentThreshold": 3000, "enemySkillTime": 1.0}
    c = 2.0 * boss_hits.type_shares("The Raven Trickster")["Cold"]
    assert boss_hits.slow_rate(stats, 2.0)[0] == pytest.approx(c * math.exp(-2 * c))
    # Zarokh deals no Cold hits: its Temporal Ball, once per 16 s
    assert rate("Zarokh, the Temporal")[0] == pytest.approx(1 / 16)
    # a Cold boss whose hits are too small to Chill and that has no Slow skill: 0, no source
    zero, how = rate("The Aberration", takenByType={"Physical": 3000, "Cold": 300})
    assert zero == 0.0 and "no source, 0" in how
    # neither a Cold hit nor a Slow skill (the Arbiter of Ash): the labelled flat rate stays
    ash, how = rate("Arbiter of Ash")
    assert ash == pytest.approx(0.1) and "LOW" in how
    # (MECHC19, AUDIT29 A29-1) the worst-case pinnacle reads the lowest real boss's Slows, naming it
    from builder.search import bosses
    worst, how = rate(bosses.WORST)
    assert 0 <= worst <= olroth and "lowest real boss" in how


def test_life_when_a_minion_is_revived_reads_the_builds_minion_deaths():
    """MECHC18: "Recover 2% of maximum Life when one of your Minions is Revived" (minion_defence9, two Soul Core rows) was
    in EVENT_UNREAD. It now reads the build's Reviving minions Killed a second (models.minion_deaths, from
    Assumptions.spent_rates): each one Killed is Revived; none Killed, 0 and said."""
    line = "Recover 2% of maximum Life when one of your Minions is Revived"
    assert not any(p.search(line) for p in recovery.EVENT_UNREAD.values())
    stats = {"Life": 3000, "Mana": 500}
    income = Income()
    recovery.spent_income(income, stats, [line], revived=(0.25, "Sacrifice"))
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(0.02 * 3000 * 0.25)
    assert "a Minion Revived" in income.parts[-1]
    none = Income()
    recovery.spent_income(none, stats, [line], revived=(0.0, "nothing in this build kills its minions"))
    assert not none.rates.get("LifeOnEventRecovery") and "nothing in this build kills" in none.parts[-1]

def test_indigon_keeps_the_corpse_lines_mana_regeneration():
    """MECHC16 (AUDIT28 A28-4, a small UNDERCOUNT): Indigon's "Mana Recovery other than Regeneration cannot Recover Mana"
    zeroed "Regenerate N% of maximum Mana over 2 seconds when you Consume a corpse", which is Regeneration: it stays
    (labelled UNVERIFIED in game); the other Mana sources still go."""
    texts = ["Regenerate 4% of maximum Mana over 2 seconds when you Consume a corpse",
             "Recover 3% of maximum Mana when you consume a Power Charge"]
    stats = {"Life": 2000, "Mana": 1000}
    income = Income()
    recovery.spent_income(income, stats, texts, corpses=(0.5, "test"), power=(1.0, "test"))
    assert income.rates["ManaOnConsumeRecovery"] == pytest.approx(0.04 * 1000 * 0.5 + 0.03 * 1000 * 1.0)
    recovery.restrict(income, ["Mana Recovery other than Regeneration cannot Recover Mana"])
    assert income.pool("Mana") == pytest.approx(0.04 * 1000 * 0.5)
    assert "Regeneration, which the line allows" in income.parts[-1]


def test_worst_case_boss_counts_the_lowest_real_bosss_rate_of_a_gain():
    """MECHC19 (AUDIT29 A29-1, an OVERCOUNT on the default boss): the worst-case pinnacle's tables take the HIGHEST real
    preset's factors and shares, so charms that fire when the boss Bleeds, Shocks, Freezes, Stuns or Slows you, or hits
    you with a type, read Olroth's Bleeding though 11 of 12 bosses never Bleed you, the Arbiter of Divinity's x6 Shock
    and Olroth's x3 Freeze. They are gains: like the "when hit" benefits (MECHD4), the worst case reads the LOWEST real
    boss's rate (boss_hits.gain_floor over real_readings: each preset's own hits, types, cadence and red-flash share)."""
    from builder.search import boss_hits, bosses
    from builder.search.flasks import Charges, trigger_rate

    sizes = boss_hits.hit_sizes(bosses.WORST)
    data = {"enemySkillTime": 0.51, "takenByType": {t: v * 0.6 for t, v in sizes.items()},
            "bossDamage": {t: 1 for t in sizes}, "ailmentThreshold": 3000, "stunThreshold": 3000, "life": 6000,
            "ailmentAvoid": {"Ignite": 0, "Shock": 0, "Freeze": 0, "Bleed": 0, "Poison": 0, "Chill": 0}}
    worst = Charges.from_bridge(data)  # no boss named: the worst case
    olroth = Charges.from_bridge(data, 0, "Olroth, Origin of the Fall")
    for trigger in ("Used when you start Bleeding", "Used when you become Frozen", "Used when you become Shocked",
                    "Used when you become Ignited", "Used when you take Cold damage from a Hit"):
        low, how = trigger_rate(trigger, worst)
        assert low == 0.0 and "lowest real boss" in how, (trigger, how)
    assert trigger_rate("Used when you start Bleeding", olroth)[0] > 0  # a named boss keeps its own rate
    assert trigger_rate("Used when you become Frozen", olroth)[0] > 0
    # every boss deals Physical: the worst case reads the slowest real boss's landed hits, never more than any named one
    phys = "Used when you take Physical damage from a Hit"
    low, how = trigger_rate(phys, worst)
    named = [trigger_rate(phys, Charges.from_bridge(data, 0, n))[0] for n in bosses.REAL]
    assert 0 < low <= min(named) + 1e-9 and "lowest real boss" in how
    # and at the "when hit" benefit pace, below the defence pace's landed hits (boss_hits)
    assert trigger_rate(phys, olroth)[0] < olroth.boss_hits
    # Stuns ("Used when you become Stunned", Life / Energy Shield when Stunned): the lowest real boss's too
    rows = [recovery.boss_stuns(s)[0] for _, s in boss_hits.real_readings(
        {"enemySkillTime": 0.51, "EvadeChance": 0.0, "EffectiveBlockChance": 0.0, "StunThreshold": 3000,
         **{f"{t}TakenHit": v * 0.6 for t, v in sizes.items()}})]
    assert worst.stuns == pytest.approx(min(rows)) and "lowest real boss" in worst.stuns_how
    # Slow: no higher than the lowest real boss (four keep the labelled once per 10 s, the user's choice 13 Nov)
    slow, how = trigger_rate("Used when you are affected by a Slow", worst)
    assert slow <= 0.1 + 1e-9 and "lowest real boss" in how


def test_rune_of_reach_s_reduced_remnant_effect_lowers_remnant_recovery(engine):
    """COV25 (an OVERCOUNT): Rune of Reach in a wand gives "Remnants you create have 25% reduced effect", which PoB drops
    (ModParser reads only "increased"). The ledger's raw modifier (mod:RemnantEffect INC -25, on the item as
    "LedgerMod:...") makes it RemnantEffect INC -25: a Life Remnant gives 25% less, and so does LifeRemnantRecovery."""
    from builder.ledger import translate
    from builder.ledger.items import corrected_item
    from builder.request.schema import BuildRequest, Choices
    from builder.search.assumptions import Assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    entry, lines = translate("Remnants you create have 25% reduced effect")
    assert entry.id == "items-rune-of-reach-reduced-remnant-effect" and lines == ["mod:RemnantEffect INC -25"]
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"],
                                        require=Choices(skills=["Mana Tempest"])))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    hook = Assumptions(build)
    helmet = chr(10).join(["Rarity: Rare", "Test Crown", "Hewn Mask", "Item Level: 82", "Implicits: 1",
                           "Grants Skill: Level 20 Life Remnants"])
    build.call("equip", slot="Helmet", raw=helmet, fields=[])

    def gain(wand_lines):
        build.call("equip", slot="Weapon 1", raw=chr(10).join(wand_lines), fields=[])
        skills = build.call("activeSkills")["skills"]
        remnants = next(s for s in skills if s["name"] == recovery.LIFE_REMNANTS)
        hook.life_mana_income(skills, "Spark")
        return remnants["remnantGain"]["Life"], build.recovery_sources["LifeRemnantRecovery"]

    head = ["Rarity: Rare", "Test Wand", "Attuned Wand", "Item Level: 82"]
    plain, plain_rate = gain(head + ["Implicits: 0"])
    raw = chr(10).join(head + ["Sockets: S", "Rune: Rune of Reach", "Implicits: 0"])
    assert gain(raw.split(chr(10)))[0] == pytest.approx(plain)  # PoB alone drops the line
    corrected = corrected_item(raw)
    assert "{custom}LedgerMod:RemnantEffect INC -25" in corrected.split(chr(10))
    reduced, reduced_rate = gain(corrected.split(chr(10)))
    assert plain > 0 and plain_rate > 0
    assert reduced == pytest.approx(0.75 * plain, abs=1)  # (PoB floors the gain: 410 -> 307)
    assert reduced_rate == pytest.approx(plain_rate * reduced / plain, rel=1e-3)


def test_life_flask_recovery_applies_to_energy_shield():
    """MECHC23 (AUDIT33 A33-1, an UNDERCOUNT for Energy Shield builds): "Life Recovery from Flasks also applies to
    Energy Shield" (Shavronne's Satchel) gives Energy Shield the Life flask's recovery at the flask's own use rate, times
    PoB's EnergyShieldRecoveryRateMod; "... instead" takes it off Life; none without the line."""
    from dataclasses import replace

    from builder.search.flasks import Charges, es_recovery_per_second, recovery_per_second

    life = {"kind": "Flask", "lifeTotal": 300, "chargesUsed": 10, "duration": 3}
    gain = Charges(flask=1)
    assert recovery_per_second(life, gain, "Life") == pytest.approx(30.0)
    assert es_recovery_per_second(life, gain) == 0.0  # no line
    shield = replace(gain, life_flask_to_es=True, es_recovery_mod=1.5)
    assert es_recovery_per_second(life, shield) == pytest.approx(45.0)
    assert recovery_per_second(life, shield, "Life") == pytest.approx(30.0)  # "also": Life keeps it
    instead = replace(shield, life_flask_es_instead=True)
    assert recovery_per_second(life, instead, "Life") == 0.0 and es_recovery_per_second(life, instead) == pytest.approx(45.0)
    assert es_recovery_per_second(None, shield) == 0.0
    # (MECHC24, COV27) Enduring Elixirs' global form: PoB counts its recovery itself, so 0 here. (MECHC25, AUDIT34 A34-20)
    # the flask's OWN kept line (Blood of the Warrior, Opportunity) parses to nothing in PoB: ours counts it
    kept = "Blood of the Warrior\nGargantuan Life Flask\nEffect is not removed when Unreserved Life is Filled"
    assert es_recovery_per_second(life, shield, kept) == pytest.approx(45.0)
    # Enduring Elixirs: 0 only with the flask up the whole fight (its slot on in the floors' stats: PoB has it); part-time
    # (here 1 charge a second, 0.1 uses x 3 s = 30%), the floors see the slot off and PoB adds nothing: ours counts
    assert es_recovery_per_second(life, replace(shield, life_effect_kept=True, flask=4)) == 0.0
    assert es_recovery_per_second(life, replace(shield, life_effect_kept=True)) == pytest.approx(45.0)
    assert es_recovery_per_second(life, shield, "Recovers 300 Life over 3 Seconds") == pytest.approx(45.0)
    assert es_recovery_per_second(life, replace(shield, life_flask_unusable=True)) == 0.0  # Reverie: no use
    data = Charges.from_bridge({"lifeFlaskToEs": True, "lifeFlaskEsInstead": True, "esRecoveryMod": 1.2})
    assert data.life_flask_to_es and data.life_flask_es_instead and data.es_recovery_mod == pytest.approx(1.2)
    assert Charges.from_bridge({}).es_recovery_mod == 1.0 and not Charges.from_bridge({}).life_flask_to_es


def test_energy_shield_recharge_starts_on_a_use_held_for_a_reset():
    """MECHC23: Bitterbloom's "Energy Shield Recharge starts when you use a Mana Flask" and Nascent Hope's own "Energy
    Shield Recharge starts on use" start recharge at the flask's / charm's uses a second; each use held until a boss hit
    resets the recharge starts it at once in that gap (recharge_start_wait), up to one a gap."""
    import math

    from builder.search import recovery
    from builder.search.flasks import ES_STARTS_FIELD, es_recharge_starts, recharge_start_wait

    texts = ["Energy Shield Recharge starts when you use a Mana Flask"]
    charm = ("Nascent Hope", "Used when you become Frozen" + chr(10) + "Energy Shield Recharge starts on use", 0.05)
    other = ("Plain Charm", "Used when you become Frozen", 0.5)
    assert es_recharge_starts([], [other], 0.4)[0] == 0.0
    starts, how = es_recharge_starts(texts, [charm, other], 0.2)
    assert starts == pytest.approx(0.25) and "Mana Flask" in how and "Nascent Hope" in how and "Plain" not in how
    assert recharge_start_wait(0.5, 2.0, 0.0) == pytest.approx(math.exp(-1.0))
    assert recharge_start_wait(0.5, 2.0, 0.25) == pytest.approx(0.5 + 0.5 * math.exp(-1.0))
    assert recharge_start_wait(0.5, 2.0, 5.0) == pytest.approx(1.0)  # one start a gap at most
    assert recharge_start_wait(0.0, 2.0, 1.0) == 1.0
    assert "EnergyShieldFlaskRecovery" in recovery.FIELDS["EnergyShield"] and ES_STARTS_FIELD not in recovery.ALL_FIELDS
    income = recovery.Income()
    income.add("EnergyShieldFlaskRecovery", 40.0, "Life flask recovery applied to Energy Shield")
    note = recovery.note(income)
    assert note and "40.0 Energy Shield/s" in note[0]["value"]


def test_nascent_hope_starts_recharge_only_as_often_as_the_boss_freezes_you():
    """MECHC25 (AUDIT34 A34-8, an OVERCOUNT): Nascent Hope is a Thawing Charm ("Used when you become Frozen"); its
    "Energy Shield Recharge starts on use" was counted at the uses its charges allow (uses_per_second), even against a
    boss that never Freezes you. uses_per_second now caps a charm by its trigger's rate, as uptime does."""
    from dataclasses import replace

    from builder.search.flasks import Charges, es_recharge_starts, trigger_rate, uses_per_second

    raw = "Nascent Hope\nThawing Charm\nUsed when you become Frozen\nEnergy Shield Recharge starts on use"
    info = {"kind": "Charm", "chargesUsed": 20, "duration": 1.0}
    data = {"enemySkillTime": 0.7, "takenByType": {"Physical": 3000, "Fire": 2000, "Cold": 1500, "Lightning": 2500,
                                                   "Chaos": 500},
            "bossDamage": {t: 1 for t in ("Physical", "Fire", "Cold", "Lightning", "Chaos")}, "ailmentThreshold": 3000,
            "ailmentAvoid": {"Ignite": 0, "Shock": 0, "Freeze": 0, "Bleed": 0, "Poison": 0}}
    rich = 100.0  # charges a second: alone they'd pay for 5 uses/s, capped at one per 1 s effect
    olroth = replace(Charges.from_bridge(data, 0, "Olroth, Origin of the Fall"), charm=rich)
    ash = replace(Charges.from_bridge(data, 0, "Arbiter of Ash"), charm=rich)
    freezes = trigger_rate(raw, olroth)[0]
    assert 0 < freezes < 1.0
    uses, how = uses_per_second(info, olroth, raw, None)
    assert uses == pytest.approx(freezes) and "its trigger" in how
    assert es_recharge_starts([], [("Nascent Hope", raw, uses)], 0.0)[0] == pytest.approx(freezes)
    # the Arbiter of Ash never Freezes you: no uses, no starts
    assert uses_per_second(info, ash, raw, None)[0] == 0.0
    # a charm with no trigger this reads keeps its charge-paced rate (one per 1 s effect here)
    plain = "Some Charm\nUsed when you use a Life Flask"
    assert uses_per_second(info, ash, plain, None)[0] == pytest.approx(1.0)


def test_energy_barrier_is_named_among_the_unread_recharge_starts():
    """MECHC25 (AUDIT34 A34-2): the Energy Barrier support's recharge start on Stun is named as unread, not silent."""
    from builder.search import flasks

    text = ("energy shield recharge begins immediately and is not interrupted by damage for 2 seconds when stunned "
            "while using supported skills")
    assert [n for n, p in flasks.ES_STARTS_UNREAD.items() if p.search(text)] == [
        "Energy Barrier (recharge begins at once when Stunned)"]


def test_a_kept_at_full_flask_pob_counts_whole_adds_no_flask_recovery_of_ours():
    """MECHC25 (MECHC24's lead, an OVERCOUNT; probe POE2T1700): PoB adds a kept-at-full flask's whole recovery to its
    LifeRegenRecovery / ManaRegenRecovery (calcFlaskRecovery) under Enduring Elixirs' flag or a Mana flask's own line,
    which objective summed with our FlaskLifeRecoveryRate / FlaskManaRecoveryRate. With the flask up the whole fight
    (its slot on in the floors' stats) ours is 0; part-time (the floors see the slot off, PoB adds nothing) ours stays."""
    from builder.search.flasks import Charges, pob_counts_whole, recovery_per_second

    life = {"kind": "Flask", "chargesUsed": 10, "duration": 3, "lifeTotal": 920}
    mana = {"kind": "Flask", "chargesUsed": 10, "duration": 3, "manaTotal": 310}
    kept_mana = "Melting Maelstrom\nUltimate Mana Flask\nEffect is not removed when Unreserved Mana is Filled"
    whole, part = Charges(flask=4.0), Charges(flask=1.0)  # 0.4 uses/s x 3 s: up whole; 0.1 x 3 s: 30%
    elixirs, elixirs_part = Charges(flask=4.0, life_effect_kept=True), Charges(flask=1.0, life_effect_kept=True)
    assert recovery_per_second(life, whole, "Life") == pytest.approx(920 / 3)  # no kept line: ours alone
    assert pob_counts_whole(life, elixirs, "Life") and recovery_per_second(life, elixirs, "Life") == 0.0
    assert recovery_per_second(life, elixirs, "Life", own=True) == pytest.approx(920 / 3)
    assert recovery_per_second(life, elixirs_part, "Life") == pytest.approx(92.0)  # part-time: PoB's slot off
    # the Life flask's own kept line: PoB parses it to nothing, so ours counts it
    own_life = "Blood of the Warrior\nGargantuan Life Flask\nEffect is not removed when Unreserved Life is Filled"
    assert recovery_per_second(life, whole, "Life", own_life) == pytest.approx(920 / 3)
    assert recovery_per_second(mana, whole, "Mana", kept_mana) == 0.0
    assert recovery_per_second(mana, part, "Mana", kept_mana) == pytest.approx(31.0)
    assert recovery_per_second(mana, whole, "Mana", "Ultimate Mana Flask") == pytest.approx(310 / 3)


def test_charms_use_no_charges_fire_at_their_trigger():
    """MECHC25 (COV27, AUDIT33 A33-2: PoB parses Pragmatism's "Charms use no Charges" and reads it nowhere): each charm
    is used at its trigger's rate, at most once per its effect, whatever charges the build makes; none are spent."""
    from dataclasses import replace

    from builder.search.flasks import CHARMS_FREE, Charges, charm_charges_used, uptime, uses_per_second

    assert CHARMS_FREE.search("Pragmatism\nExplorer Armour\nCharms use no Charges")
    info = {"kind": "Charm", "chargesUsed": 40, "duration": 2.0}
    hit = "Some Charm\nUsed when you take Fire damage from a Hit"
    gain = Charges(boss_hits=0.25, boss_types=("Fire",), charm=0.5)  # 0.5 charges/s: one use per 80 s
    free = replace(gain, charms_free=True)
    assert uses_per_second(info, gain, hit, None)[0] == pytest.approx(0.5 / 40)
    assert uses_per_second(info, free, hit, None)[0] == pytest.approx(0.25)  # the boss's hits, under 1 per 2 s
    assert uptime(info, free, None, hit) == pytest.approx(0.5)
    fast = replace(free, boss_hits=2.0)
    assert uses_per_second(info, fast, hit, None)[0] == pytest.approx(0.5)  # once per its 2 s effect
    assert uptime(info, fast, None, hit) == 1.0
    no_fire = replace(free, boss_types=("Cold",))
    assert uses_per_second(info, no_fire, hit, None)[0] == 0.0 and uptime(info, no_fire, None, hit) == 0.0
    assert uptime(info, free, None, "Gold Charm\nUsed when you kill a Rare or Unique enemy") == 0.0
    assert charm_charges_used([(info, hit)], free)[0] == 0.0 and charm_charges_used([(info, hit)], gain)[0] > 0


def test_staunch_deflection_takes_the_deflected_share_off_the_bosss_bleeds_and_maims_on_you():
    """MECHC27 (COV31's H4-5, an OVERCOUNT of charm uses for a build holding the lines): "Deflected Hits cannot inflict
    Bleeding on you" / "Deflected Hits cannot inflict Maim on you" (Staunch Deflection; PoB leaves both unparsed). A
    Deflected Hit still lands, so the boss's Bleeds and Maims on you lose PoB's DeflectChance share; its Chills don't."""
    from builder.search.flasks import Charges, deflect_stops, trigger_rate

    lines = ["Deflected Hits cannot inflict Maim on you", "Deflected Hits cannot inflict Bleeding on you"]
    assert deflect_stops(lines) == ("Bleed", "Maim") and deflect_stops(lines[:1]) == ("Maim",) and deflect_stops([]) == ()
    data = {"enemySkillTime": 0.7, "takenByType": {"Physical": 3000, "Cold": 1500}, "bossDamage": {"Physical": 1, "Cold": 1},
            "ailmentThreshold": 3000, "ailmentAvoid": {"Bleed": 0, "Chill": 0}, "deflect": 40, "deflectEffect": 40}
    olroth = "Olroth, Origin of the Fall"

    def rate(trigger, stops=(), **over):
        return trigger_rate(trigger, Charges.from_bridge({**data, **over}, 0, olroth, deflect_stops=stops))

    bleed = "Used when you start Bleeding"
    plain, kept = rate(bleed)[0], rate(bleed, ("Bleed", "Maim"))
    assert plain > 0 and kept[0] == pytest.approx(plain * 0.6) and "not Deflected" in kept[1]
    assert rate(bleed, ("Maim",))[0] == pytest.approx(plain)  # the Maim line alone leaves Bleeds alone
    # Slows: only the Maim part loses the share; with no Chill (a Cold hit under 30% of the threshold) all of it does
    small = {"takenByType": {"Physical": 3000, "Cold": 600}}
    slow = "Used when you are affected by a Slow"
    maims, kept = rate(slow, **small)[0], rate(slow, ("Maim",), **small)
    assert maims > 0 and kept[0] == pytest.approx(maims * 0.6) and "cannot inflict Maim" in kept[1]
    full, cut = rate(slow)[0], rate(slow, ("Maim",))[0]
    assert cut == pytest.approx(full - maims * 0.4)


def test_full_life_share_is_one_less_the_dips_over_the_refill():
    """MECHC27 (PATCH_CHECK MC-4; MECH75's Full-Life penalties): PoB's Full Life box is never set, so "on Full Life"
    lines counted never and "not on Full Life" ones all fight. recovery.full_life_share: below full for (Life a second
    the boss's landed hits and the Life costs take) / (the recovery left after a steady loss) of the fight."""
    from builder.search import objective
    from builder.search.recovery import full_life_share

    stats = {"enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 0, "PhysicalTakenHit": 200,
             "Life": 2000, "LifeRegenRecovery": 300.0}
    taken = sum(objective.boss_taken(stats)[0].values())
    share, how = full_life_share(stats)
    assert taken > 0 and share == pytest.approx(1 - taken / 300.0) and "MEDIUM" in how
    # a Life cost adds its dips; a steady loss below the regeneration comes off the refill instead
    assert full_life_share({**stats, "LifePerSecondCost": 10.0})[0] == pytest.approx(1 - (taken + 10) / 300.0)
    assert full_life_share({**stats, "PhysicalBuildDegen": 20.0})[0] == pytest.approx(1 - taken / 280.0)
    assert full_life_share({**stats, "PhysicalBuildDegen": 350.0})[0] == 0.0
    # no recovery: never back on full; nothing taking Life: on Full Life all fight; Chaos Inoculation: always
    assert full_life_share({**stats, "LifeRegenRecovery": 0.0})[0] == 0.0
    assert full_life_share({**stats, "PhysicalTakenHit": 0})[0] == 1.0
    assert full_life_share({**stats, "LifeRegenRecovery": 0.0, "ChaosInoculation": True})[0] == 1.0
    # Energy Shield takes the hits first; what its own recovery doesn't refill spills onto Life; Mind over Matter's share
    # of Life's part goes to Mana
    shielded = {**stats, "EnergyShield": 1000, "EnergyShieldRegenRecovery": taken}
    assert full_life_share(shielded)[0] == 1.0
    assert full_life_share({**shielded, "EnergyShieldRegenRecovery": taken / 2})[0] == pytest.approx(1 - taken / 2 / 300)
    assert full_life_share({**stats, "sharedMindOverMatter": 50})[0] == pytest.approx(1 - taken / 2 / 300)


def test_the_full_life_box_follows_the_builds_full_life_lines():
    """MECHC27: conditionFullLife is set only for a build with a "while / when (not) on Full Life" line (or Zealot's
    Oath's excess-regeneration line), at full_life_share's share; never on Full Life leaves it off with the reason."""
    from builder.search.conditions import FULL_LIFE_LINE, full_life_uptime

    assert FULL_LIFE_LINE.search("30% less Critical Damage Bonus when on Full Life")
    assert FULL_LIFE_LINE.search("20% faster start of Energy Shield Recharge when not on Full Life")
    assert FULL_LIFE_LINE.search("Excess Life Recovery from Regeneration is applied to Energy Shield")
    assert not FULL_LIFE_LINE.search("20% increased Damage with Hits against Enemies that are on Full Life")
    assert full_life_uptime([], {}) == (0.0, "")
    value, how, share = full_life_uptime([], {"_full_life": (0.4, "x")})
    assert value is True and share == 0.4 and "40%" in how
    assert full_life_uptime([], {"_full_life": (0.0, "y")})[0] == 0.0


def test_dodge_rolled_recently_reads_the_builds_roll_cadence():
    """MECHC27 (PATCH_CHECK MC-7): PoB's Dodge Rolled Recently box at the share of the fight a roll was in the past 4 s:
    a roll every 2 s for a build that leans on rolling (MECH75's ROLL_RELIANT, which the line itself meets: all of it);
    unset without a "Dodge Rolled Recently" line."""
    from builder.search.conditions import dodge_rolled_recently

    line = "50% increased Evasion Rating if you've Dodge Rolled Recently"
    assert dodge_rolled_recently(["+2 metres to Dodge Roll distance if you haven't Dodge Rolled Recently"], {}) == (0.0, "")
    value, how, share = dodge_rolled_recently([line], {})
    # (MECH75's ROLL_RELIANT: a "Dodge Rolled Recently" line makes the build roll every 2 s, as Stoicism reads it)
    assert value is True and share == 1.0 and "2 s" in how
    assert dodge_rolled_recently([line], {"_gem_names": {"Cast on Dodge"}})[2] == 1.0
    assert dodge_rolled_recently([line, "Gain Guard equal to 15% of missing Energy Shield for 4 seconds when you "
                                        "Dodge Roll"], {})[2] == 1.0


def test_refill_rate_sums_every_recovery_source_of_the_pool():
    """MECHC27 (for COV-A's Force of Will: it read Mana regeneration alone): recovery.refill_rate sums the pool's
    regeneration, leech (less the boss's Leech resistance), on-hit, flasks and the added sources, recoup and Block gains."""
    from builder.search.recovery import refill_rate

    stats = {"enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 0, "PhysicalTakenHit": 0,
             "ManaRegenRecovery": 50.0, "ManaLeechRate": 20.0, "ManaLeechResisted": -18.0, "FlaskManaRecoveryRate": 10.0}
    rate, how = refill_rate(stats)
    assert rate == pytest.approx(62.0) and "ManaRegenRecovery 50.0" in how
    assert refill_rate({}, "Life")[0] == 0.0



def test_kurgals_leash_grants_unholy_might_at_its_commands_rate():
    """MECHC27 (PATCH_CHECK MD-1): Kurgal's Leash's 15 s of Unholy Might a Command (PoB reads neither stat: 0 before),
    up min(1, 15 s x Commands a second); no Command rate, no Unholy Might (no flat rate)."""
    from builder.search.conditions import unholy_might_uptime

    assert unholy_might_uptime([], {}) == (0.0, "")
    value, how, share = unholy_might_uptime([], {"_kurgal": (0.04, "Skeletal Arsonist Commanded 0.040/s")})
    assert value is True and share == pytest.approx(0.6) and "Kurgal" in how
    assert unholy_might_uptime([], {"_kurgal": (0.2, "x")})[2] == 1.0
    off = unholy_might_uptime([], {"_kurgal": (0.0, "no rate")})
    assert off[0] == 0.0 and "no source, 0" in off[1]



def test_full_energy_shield_share_reads_es_dips_against_its_refill():
    """MECHC27 (PATCH_CHECK MC-5): Mysticism and "while on Full Energy Shield" lines counted never (PoB's box unset).
    recovery.full_es_share: below full for (the boss's landed hits ES takes) / (ES recovery) of the fight."""
    from builder.search import objective
    from builder.search.recovery import full_es_share

    stats = {"enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 0, "PhysicalTakenHit": 200,
             "EnergyShield": 1000, "EnergyShieldRegenRecovery": 300.0}
    taken = sum(objective.boss_taken(stats)[0].values())
    assert full_es_share(stats)[0] == pytest.approx(1 - taken / 300.0)
    assert full_es_share({**stats, "PhysicalEnergyShieldBypass": 100})[0] == 1.0  # (it all goes to Life)
    assert full_es_share({**stats, "EnergyShieldRegenRecovery": 0.0})[0] == 0.0
    assert full_es_share({**stats, "EnergyShield": 0})[0] == 0.0



def test_ghost_dance_regenerates_es_while_a_shroud_was_lost_recently():
    """MECHC27 (PATCH_CHECK H1-9): Ghost Dance's 2% of Evasion as ES a second while a Shroud was lost in the past 4 s
    (PoB: none). Shrouds every 10.1 s; hits faster than that lose each new one at once (4 / 10.1 of the fight), slower
    hits each find one waiting (1 - e^(-4h))."""
    from builder.search.recovery import Income, ghost_dance_income, ghost_dance_share

    assert ghost_dance_share(10.1, 1.5) == pytest.approx(4 / 10.1)
    assert ghost_dance_share(10.1, 0.05) == pytest.approx(1 - math.exp(-0.2))
    assert ghost_dance_share(10.1, 0.0) == 0.0
    income = Income()
    ghost_dance_income(income, {"base_cooldown_modifiable_repeat_interval_ms": 10100}, 10000, 1.5, "1.50/s")
    assert income.rates["EnergyShieldGhostDanceRegen"] == pytest.approx(0.02 * 10000 * 4 / 10.1)
    assert income.pool("EnergyShield") == pytest.approx(income.rates["EnergyShieldGhostDanceRegen"])



@pytest.mark.usefixtures("arbiter_default")
def test_charges_from_an_event_named_before_the_gain_read_that_event_not_one_per_4_s():
    """MECHC27 (PATCH_CHECK K2-1 / K2-2 / K2-3 / K2-5 / K2-6, OVERCOUNTS): Killing Palm, Sniper's Mark, Profane Ritual
    and Magma Barrier name their event before the gain, so they fell to the flat one-per-4-s fallback; Combat Frenzy's
    "no more than once every N seconds" wasn't read."""
    from builder.search.conditions import _charge_rate

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "enemySkillTime": 1.0, "EffectiveBlockChance": 30,
             "_kills": (0.0, 0.0, "a lone boss")}
    palm = ("Dash to an enemy and Strike them, Culling enemies if their life is low enough. Each enemy killed by this "
            "strike grants a Power Charge, with higher Rarity monsters granting additional Charges.")
    assert _charge_rate(palm, "Power", stats)[0] == 0.0
    assert _charge_rate(palm, "Power", {**stats, "_kills": (0.1, 0.1, "adds")})[0] == pytest.approx(0.1)
    sniper = ("Mark a target. The next Critical Hit the target receives Activates the Mark, Consuming it to deal extra "
              "damage and grant you a Frenzy Charge.")
    assert _charge_rate(sniper, "Frenzy", stats)[0] == 0.0  # (no Mark use rate: no re-applied Mark)
    assert _charge_rate(sniper, "Frenzy", {**stats, "_mark_uses": 0.1})[0] == pytest.approx(0.1)
    ritual = ("Mark a Corpse with a profane rune, dealing Chaos damage over time to nearby enemies. When the ritual is "
              "complete the Corpse is consumed and you gain a Power Charge.")
    assert _charge_rate(ritual, "Power", stats)[0] == 0.0
    magma = ("While active, increases your Block Chance passively and imbues your Shield with lava over time. When fully "
             "imbued, your next Block with your Shield raised will expend the lava to Trigger the Magma Spray skill and "
             "grant you an Endurance Charge.")
    # (MECHC36, K2-6: a Block with the shield RAISED after the lava's 8 s charge-up; no raised shield, none)
    rate, how = _charge_rate(magma, "Endurance", stats)
    assert rate == 0.0 and "never raises" in how
    from builder.search import bosses

    boss = 1 / bosses.benefit_skill_time(stats)  # (the "when hit" pace the charge readers use)
    rate, how = _charge_rate(magma, "Endurance", {**stats, "_raised_share": 1.0})
    assert rate == pytest.approx(1 / (8.0 + 1 / boss)) and "8 s charge-up" in how
    rate, how = _charge_rate(magma, "Endurance", {**stats, "_raised_share": 0.25})
    assert rate == pytest.approx(1 / (8.0 + 1 / (boss * 0.25)))
    frenzy = ("Gain a Frenzy Charge when you Freeze, Electrocute or Pin an enemy, no more than once every 6.1 seconds")
    fast = {**stats, "Speed": 40.0, "FreezeBuildupAvg": 50.0, "ColdHitAverage": 100.0}
    rate, how = _charge_rate(frenzy, "Frenzy", fast)
    assert rate == pytest.approx(1 / 6.1) and "the line's cap" in how



def test_banner_limit_reads_the_builds_lines():
    """MECHC27 (PATCH_CHECK K2-4): 0.3.0's limit of 1 placed Banner, +N from "+N to maximum number of placed Banners",
    none with Whoever Pays Best."""
    import math as _m

    from builder.search.conditions import banner_limit

    assert banner_limit([])[0] == 1
    assert banner_limit(["+1 to maximum number of placed Banners"])[0] == 2
    assert banner_limit(["There is no Limit on the number of Banners you can place"])[0] == _m.inf



def test_eldritch_empowerments_sacrifice_comes_off_energy_shields_recovery():
    """MECHC27 (MECHB30's note: an OVERCOUNT of defence): "Sacrifice 5% of maximum Energy Shield when you Cast a Spell"
    is a negative ES recovery field (casts x 5% x maximum ES a second) the recovery floor nets off; Income.pool leaves
    it out."""
    from builder.search.recovery import Income, sacrifice_income

    income = Income()
    sacrifice_income(income, ["Sacrifice 5% of maximum Energy Shield when you Cast a Spell"], 2.0, 1000.0, "2 casts/s")
    assert income.rates["EnergyShieldSacrificed"] == pytest.approx(-100.0)
    assert income.pool("EnergyShield") == 0.0
    none = Income()
    sacrifice_income(none, ["Sacrifice 5% of maximum Energy Shield when you Cast a Spell"], 0.0, 1000.0, "no Spell")
    assert none.rates["EnergyShieldSacrificed"] == 0.0



def test_temporal_rift_clears_phased_forms_delayed_damage_at_its_use_rate():
    """MECHC27 (COV31 H1-1; 0.5.0: Temporal Rift "now also removes delayed damage from Phased Form"): each use clears the
    past 4 s of delayed damage, so on its 5 s cooldown 80% of it; without Temporal Rift all of it lands."""
    from builder.search.assumptions import phased_form_kept

    class Stand:
        def __init__(self, gems, rate):
            self.gems, self.rate = gems, rate

        def call(self, name, **kw):
            if name == "groups":
                return {"groups": [{"index": 1, "gems": [{"name": g} for g in self.gems]}]}
            return {"rates": [self.rate]}

    assert phased_form_kept(Stand(["Spark"], {}))[0] == 1.0
    kept, how = phased_form_kept(Stand(["Temporal Rift"], {"name": "Temporal Rift", "cooldown": 5.0, "speed": 10.0}))
    assert kept == pytest.approx(0.2) and "80%" in how



def test_the_lethal_draws_charges_leave_the_life_flasks_drinking():
    """MECHC27 (COV31 G4-1's note, a double credit): The Lethal Draw consumes 10% of the Life flask's maximum charges on
    every Bow Attack "if possible", so the charges it takes aren't also drunk (flasks.drink_charges)."""
    from dataclasses import replace

    from builder.search.flasks import Charges, charges_per_second, drink_charges

    info = {"kind": "Flask", "chargesMax": 60, "gainBase": 3.0}
    gain = Charges()
    made = charges_per_second(info, gain, "Life")
    assert made > 0 and drink_charges(info, gain, "Life") == made
    drawn = replace(gain, lethal_pct=10.0, bow_attacks=0.25)  # (0.25 x 6 = 1.5 charges/s taken)
    assert drink_charges(info, drawn, "Life") == pytest.approx(max(made - 1.5, 0.0))
    assert drink_charges(info, replace(gain, lethal_pct=10.0, bow_attacks=5.0), "Life") == 0.0
    assert drink_charges(info, drawn, "Mana") == charges_per_second(info, drawn, "Mana")



def test_the_bosss_melee_hits_stun_you_a_third_more_at_its_melee_share(monkeypatch):
    """MECHC27 (the manager, 15 Nov 06:59; KeywordPopups Stun and 0.3.0: monster Melee damage has 33% more Light Stun
    chance): x(1 + 0.33 x the boss's Melee share, MECH76's bosses.melee_share) where neither the 100% cap nor the 15% floor
    binds; a Melee hit and a non-Melee hit each meet them on their own."""
    from builder.search import bosses

    stats = {"StunThreshold": 1e9, "StunAvoidChance": 0.0}  # (a threshold too high to matter for the 4 s rule)
    weights, sizes = {"Physical": 1.0}, {"Physical": 0.15e9}  # (x2 Physical = 30% a hit)
    monkeypatch.setattr(bosses, "melee_share", lambda stats=None: (0.0, "none"), raising=False)
    plain, _ = recovery.stuns_taken(stats, sizes, weights, 0.001)
    monkeypatch.setattr(bosses, "melee_share", lambda stats=None: (0.4, "40% (test)"), raising=False)
    melee, how = recovery.stuns_taken(stats, sizes, weights, 0.001)
    assert melee == pytest.approx(plain * (1 + 0.33 * 0.4), rel=1e-3) and "Melee 33% more on 40% of hits" in how
    # near the cap: a Melee hit can't pass 100%
    big = {"Physical": 0.45e9}  # (90% plain, 100% not 120% as Melee)
    capped, _ = recovery.stuns_taken(stats, big, weights, 0.001)
    monkeypatch.setattr(bosses, "melee_share", lambda stats=None: (0.0, "none"), raising=False)
    assert capped == pytest.approx(recovery.stuns_taken(stats, big, weights, 0.001)[0] * (0.6 + 0.4 / 0.9), rel=1e-3)


def test_demon_form_cycle_keeps_the_form_only_where_recovery_outpaces_the_demonflame():
    """MECHC28 (PATCH_CHECK S2-4 / K1-o4): Demonflame one a second, at most 10; a stint ends at 1 Life, then the cooldown
    runs out of the form while Life refills; "Demonflame has no maximum" grows until Life runs out; a non-Spell press
    caps a stint."""
    per = 1112 / 60  # (gem level 20: 1112 Life a minute per Demonflame)
    share, stacks, stint, how = recovery.demon_form_cycle(4000, 200, per, 10, 15)
    assert (share, stacks, stint) == (1.0, 10.0, math.inf) and "all fight" in how
    share, stacks, stint, how = recovery.demon_form_cycle(4000, 120, per, 10, 15)
    # (entered at 1 + 120 x 15 Life; Life falls once 10 x 18.5 > 120, about 1,800 Life later)
    assert 0.6 < share < 0.8 and 8 < stacks < 10 and 30 < stint < 60 and "1 Life" in how
    share_none, stacks_none, _, _ = recovery.demon_form_cycle(4000, 200, per, None, 15)
    assert stacks_none > 10 and share_none < 1.0
    share_cut, _, stint_cut, how_cut = recovery.demon_form_cycle(4000, 200, per, 10, 15, limit=20)
    assert stint_cut == pytest.approx(20, abs=0.1) and share_cut == pytest.approx(20 / 35, abs=0.01)
    assert "non-Spell" in how_cut
    assert recovery.demon_form_cycle(4000, -50, per, 10, 15)[0] == 0.0  # (Life never refills between stints)
    assert recovery.demon_form_cycle(1, 200, per, 10, 15)[0] == 0.0  # (Chaos Inoculation: under 2 Life)


def test_temporal_rift_cancels_its_share_of_each_pools_net_drain(monkeypatch):
    """MECHC29 (the manager, 15 Nov 13:14): Temporal Rift resets Life, Mana and Energy Shield to 4 s ago. A pool that
    keeps up sits full (nothing back); one losing a net d a second gets d x 4 s back a use: min(1, 4 x uses) of d."""
    from builder.search import objective

    monkeypatch.setattr(recovery, "life_flows", lambda stats: (50.0, 80.0, 10.0, 0.0, []))  # (90 out, 50 back: net 40)
    monkeypatch.setattr(objective, "boss_taken", lambda stats: ({"Physical": 100.0}, 0.5, 0.0))
    monkeypatch.setattr(recovery, "refill_rate", lambda stats, pool="Mana": (30.0, "") if pool == "Mana" else (200.0, ""))
    monkeypatch.setattr(objective, "es_recharge", lambda stats, hits: (0.0, 0.0))
    income = recovery.Income()
    recovery.rift_income(income, {"ManaPerSecondCost": 20.0, "EnergyShield": 0}, 0.2, "its 5 s cooldown")
    assert income.rates["LifeRiftRecovery"] == pytest.approx(0.8 * 40.0)  # (4 s x 0.2/s)
    assert income.rates["ManaRiftRecovery"] == 0.0  # (Mana refills faster than it's spent: full, nothing back)
    assert income.rates["EnergyShieldRiftRecovery"] == 0.0  # (no Energy Shield)
    income = recovery.Income()
    recovery.rift_income(income, {"ManaPerSecondCost": 50.0, "EnergyShield": 0}, 1.0, "pressed")
    assert income.rates["ManaRiftRecovery"] == pytest.approx(20.0)  # (at most the whole net drain)
    income = recovery.Income()
    recovery.rift_income(income, {"ManaPerSecondCost": 50.0}, 0.0, "not used")
    assert sum(income.rates.values()) == 0


def test_soul_eater_souls_from_hits_with_the_games_decay():
    """MECHC29 (PATCH_CHECK H5-5): KeywordPopups SoulEater: 1% increased Skill Speed a Soul, at most 50, one lost every
    0.5 s after 4 s without eating one. Eating 2 a second all fight holds 50 after the 25 s ramp; eating only inside 45%
    of the fight in segments holds fewer; nothing eaten, none."""
    from builder.search.assumptions import even_segments, soul_eater_average

    always = soul_eater_average(300.0, None, 2.0, 2.0)
    assert always == pytest.approx((300 - 12.5) / 300 * 50, rel=0.02)  # (the ramp's average is half of 50 over 25 s)
    segs = even_segments(300.0, 0.45, 3)
    assert len(segs) == 3 and sum(b - a for a, b in segs) == pytest.approx(135.0)
    weak = soul_eater_average(300.0, segs, 3.0, 0.0)
    assert 0 < weak < always
    assert soul_eater_average(300.0, None, 0.0, 0.0) == 0
    assert soul_eater_average(300.0, None, 2.0, 2.0, cap=10) == pytest.approx(10 * (300 - 2.5) / 300, rel=0.02)


def test_heavy_stun_threshold_multipliers_for_each_action():
    """MECHC30: Parry's own -40% final, "increased Stun Threshold while Parrying", Dionadair's double while the shield is
    raised, Unbreakable's +30% final and Steadfast's +50% final only while channelling (none mapped by PoB)."""
    scale, what = recovery.heavy_stun_scale(["20% increased Stun Threshold while Parrying"], {}, parry=True)
    assert scale == pytest.approx(0.6 * 1.2) and len(what) == 2
    double = ["Double Stun Threshold while Shield is Raised"]
    support = {recovery.UNBREAKABLE_HEAVY: 30, recovery.STEADFAST_CHANNEL: 50}
    assert recovery.heavy_stun_scale(double, support, raised=True)[0] == pytest.approx(2 * 1.3)
    assert recovery.heavy_stun_scale(double, support, raised=True, channel=True)[0] == pytest.approx(2 * 1.3 * 1.5)
    assert recovery.heavy_stun_scale(double, {}, parry=True)[0] == pytest.approx(0.6)  # (not raised: no double)


def test_heavy_stun_buildup_per_blocked_hit_is_065_of_the_hit_over_the_threshold():
    """0.65 (HeavyStunDamageScalePlayer) x hit / Stun Threshold, a monster's Physical x2, its Melee x1.33."""
    from builder.search import bosses, boss_hits

    stats = {"PhysicalTakenHit": 1000, "StunThreshold": 4000, "_boss": bosses.DEFAULT}
    buildups, how = recovery.heavy_buildups(stats)
    w = boss_hits.type_shares(bosses.DEFAULT)["Physical"]
    melee = bosses.melee_share(stats)[0]
    base = 0.65 * 1000 * 2 / 4000
    assert sorted(buildups) == sorted([(w * melee, base * 1.33), (w * (1 - melee), base)])
    assert "HeavyStunDamageScalePlayer" in how
    assert recovery.heavy_buildups({**stats, "StunThreshold": 0})[0] == []


def test_channelling_a_raised_shield_heavy_stuns_you_when_the_bar_fills():
    """Resonating Shield: the bar never decays while channelling, so every blocked hit adds; a hit worth a full bar
    stuns at each blocked hit, 3 s each; Evasion avoids the buildup; tiny hits take many hits to fill."""
    from builder.search import bosses, boss_hits

    big = {f"{t}TakenHit": 50000 for t in boss_hits.TYPES} | {"StunThreshold": 1000, "enemySkillTime": 0.7,
                                                             "_boss": bosses.DEFAULT}
    unavoidable, evadable = boss_hits.shares(big)
    blocked = (1 - unavoidable - evadable) / 0.7
    share, rate, how = recovery.channel_heavy_stun(big)
    assert share == pytest.approx(3 / (1 / blocked + 3)) and rate == pytest.approx(1 / (1 / blocked + 3))
    assert "every 1 blocked hits" in how
    small = {**big, **{f"{t}TakenHit": 1 for t in boss_hits.TYPES}, "StunThreshold": 100000}
    assert 0 < recovery.channel_heavy_stun(small)[0] < 0.01
    # (the manager, 20:55: no Evasion while the shield is raised, so it avoids no buildup)
    assert recovery.channel_heavy_stun({**big, "EvadeChance": 100})[0] == pytest.approx(share)
    assert recovery.channel_heavy_stun({**big, "StunThreshold": 1000 * 2})[0] == pytest.approx(share)  # (still >= 1 bar)


def test_a_parry_heavy_stuns_only_when_its_hit_fills_the_bar_alone():
    """Parries 5 s apart: the bar empties between them, so a parry stuns only on a hit worth a full bar at 0.6 x the
    threshold; Evasion avoids the buildup."""
    from builder.search import bosses, boss_hits

    weights = boss_hits.type_shares(bosses.DEFAULT)
    stats = {"PhysicalTakenHit": 3000, "FireTakenHit": 100, "StunThreshold": 4000, "_boss": bosses.DEFAULT}
    # Physical: 0.65 x 3000 x 2 / 2400 = 1.63 bars, stuns; Fire: 0.03 bars, doesn't
    chance, how = recovery.parry_heavy_stun(stats)
    assert chance == pytest.approx(weights["Physical"]) and "x0.60" in how
    assert recovery.parry_heavy_stun({**stats, "EvadeChance": 50})[0] == pytest.approx(weights["Physical"] / 2)
    assert recovery.parry_heavy_stun({**stats, "StunThreshold": 40000})[0] == 0.0


def test_parry_plan_counts_the_heavy_stuns_of_parrying():
    """MECHC30: a stunned parry loses 3 s more and its Parried window; at 10% of parries the Parry plan no longer pays."""
    from builder.search import models

    parry = models.Skill("Parry", attack=True, cast_time=1.0)
    stats = {"enemySkillTime": 0.7}
    plain = models.parry_plan([parry], stats, True)
    assert plain.box > 0
    calm = models.parry_plan([parry], stats, True, stun=(0.0, "no hit fills the bar"))
    assert calm.box == plain.box and "Heavy Stun from parrying: none (no hit fills the bar)" in calm.how
    stunned = models.parry_plan([parry], stats, True, stun=(0.1, "big slams"))
    assert stunned.box == 0 and "Heavy Stun from parrying: 10%" in stunned.how and "not worth" in stunned.how


def test_heavy_stunned_row_gives_the_dps_without_the_stuns():
    """MECHC30: the play guide's row: the headline with the Heavy Stuns, headline / (1 - share) without; none at 0."""
    from types import SimpleNamespace

    from builder.search import play_guide

    search = SimpleNamespace(build=SimpleNamespace(heavy_stunned_self=(0.25, 0.1, "a Heavy Stun every 2 blocked hits")))
    row = play_guide.heavy_stunned_row(search, 750.0)
    assert row["dps_with"] == 750.0 and row["dps_without"] == pytest.approx(1000.0) and "25%" in row["label"]
    search.build.heavy_stunned_self = (0.0, 0.0, "")
    assert play_guide.heavy_stunned_row(search, 750.0) is None


@pytest.mark.usefixtures("arbiter_default")  # (the parry test's boss: one hit every 0.7 s)
def test_refutations_blocks_heavy_stun_you_within_its_buff():
    """MECHC30: Refutation's blocks build the bar through its 4 s buff (threshold x0.5 x (1 + 5% per 10 Ward)); a full
    bar ends the buff; parry_plan then loses the stun's 3 s and the Parried after it."""
    from builder.search import bosses, boss_hits, models

    big = {f"{t}TakenHit": 50000 for t in boss_hits.TYPES} | {"StunThreshold": 1000, "enemySkillTime": 0.7,
                                                             "_boss": bosses.DEFAULT}
    unavoidable, evadable = boss_hits.shares(big)
    blocked = (1 - unavoidable - evadable) / 0.7
    at, how = recovery.refutation_heavy_stun(big, 0.5)
    assert at == pytest.approx(1 / blocked) and "buff ends" in how
    calm = {**big, **{f"{t}TakenHit": 1 for t in boss_hits.TYPES}}
    assert recovery.refutation_heavy_stun(calm, 0.5)[0] == 0.0
    refutation = models.Skill("Refutation", spell=True, cast_time=0.65, cooldown=9.0)
    stats = {"enemySkillTime": 0.7, "Ward": 100}
    plain = models.parry_plan([refutation], stats, True)
    stunned = models.parry_plan([refutation], stats, True, refute=(1.0, "a stun 1.0 s in"))
    assert plain.box > 0
    # 1.0 s in: Parried only until the stun, the 0.65 s cast + 3 s stun lost: not worth it
    assert stunned.box == 0 and "a stun 1.0 s in" in stunned.how and "not worth" in stunned.how
    calm_plan = models.parry_plan([refutation], stats, True, refute=(0.0, "no stun within the buff"))
    assert calm_plan.box == plain.box and "no stun within the buff" in calm_plan.how
    # a stun at 3.9 s: Parried under 3.9 s of 13, 3.65 s lost: under x(1 + 0.5 x 0.3) x 0.72 = 0.83, not worth it
    assert models.parry_plan([refutation], stats, True, refute=(3.9, "late")).box == 0


def test_consecrated_ground_share_from_the_builds_own_uses(monkeypatch):
    """MECHC30 (MD-3): Consecrate 8 s and Holy Descent 4 s per use, overlapping as independent, x0.75 standing in it;
    "increased Effect of Consecrated Ground" scales both lines; the stationary line is left to PoB when standing still."""
    from types import SimpleNamespace

    from builder.search import assumptions

    texts = ["20% increased Effect of Consecrated Ground you create"]
    stationary = {"on": False}

    def call(name, **_):
        if name == "sourceLines":
            return {"lines": [{"text": t} for t in texts]}
        if name == "getConfig":
            return {"input": {"conditionStationary": stationary["on"]}}
        raise AssertionError(name)

    build = SimpleNamespace(call=call, equipped={}, groups=lambda: [])
    rates = {"Consecrate": (0.0625, "every 16 s"), "Holy Descent": (0.125, "every 8 s")}
    monkeypatch.setattr(assumptions, "group_uses", lambda b, gem: rates.get(gem))
    hook = assumptions.Assumptions.__new__(assumptions.Assumptions)
    hook.build = build
    mods, notes = hook.consecrated_ground()
    laid = 1 - (1 - 0.5) * (1 - 0.5)
    up = laid * 0.75
    assert [m["scale"] for m in mods] == pytest.approx([5 * 1.2 * up, 50 * 1.2 * up], abs=0.01)
    assert f"{up:.0%}" in notes[0]["value"]
    texts.append("You have Consecrated Ground around you while stationary")
    stationary["on"] = True
    mods, notes = hook.consecrated_ground()
    assert mods == [] and "PoB applies itself" in notes[0]["basis"]
    monkeypatch.setattr(assumptions, "group_uses", lambda b, gem: None)
    assert hook.consecrated_ground() == ([], [])


def test_consecrated_row_shows_inside_and_outside():
    """MECHC30 (the manager, 20:55: inside / outside rows): Life regenerated inside, none outside, the same DPS."""
    from types import SimpleNamespace

    from builder.search import play_guide

    search = SimpleNamespace(build=SimpleNamespace(consecrated_ground=(0.375, 0.5, 1.2, "Consecrate every 16 s"),
                                                   stats={"Life": 5000}))
    row = play_guide.consecrated_row(search, 1000.0)
    assert row["dps_with"] == row["dps_without"] == 1000.0 and "Inside it: 6.0%" in row["play"] and "300 Life/s" in row["play"]
    assert "outside it" in row["play"] and ("37%" in row["play"] or "38%" in row["play"])
    search.build.consecrated_ground = None
    assert play_guide.consecrated_row(search, 1000.0) is None


def test_leeching_runs_only_while_its_pool_is_below_full():
    """MECHC31 (RESEARCH26 R26-40 (c), an OVERCOUNT): "all Life Leech is removed when Life is filled" (KeywordPopups), so
    PoB's Leeching box (on all fight for any Leech line before) is set at the share the leeched pool is below full: Life
    1 - full_life_share, Mana its spending over its refill; "Leeching" is any of them. The Life / Mana / Energy Shield
    boxes (never set before) get their own pool's share. Leech that isn't removed at full Life keeps it on all fight."""
    from builder.search.conditions import LEECHING_LINE, leeching_shares, leeching_uptime
    from builder.search.recovery import full_life_share

    assert LEECHING_LINE.search("40% increased Damage while Leeching Life")
    assert LEECHING_LINE.search("You are Unaffected by Bleeding while Leeching")
    assert not LEECHING_LINE.search("10% of Physical Attack Damage Leeched as Life")
    stats = {"enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 0, "PhysicalTakenHit": 200,
             "Life": 2000, "LifeRegenRecovery": 100.0, "LifeLeechRate": 200.0}
    shares = leeching_shares(stats, [], set())
    assert set(shares) == {"Life"}
    assert shares["Life"][0] == pytest.approx(1 - full_life_share(stats)[0]) and 0 < shares["Life"][0] < 1
    value, how, share = leeching_uptime(None)([], {"_leeching": shares})
    assert value is True and share == pytest.approx(shares["Life"][0]) and "MEDIUM" in how
    assert leeching_uptime("Mana")([], {"_leeching": shares})[0] == 0.0
    # Mana Leech: the Mana spent a second over the refill; both pools: either one
    both = leeching_shares({**stats, "ManaLeechRate": 50.0, "ManaRegenRecovery": 50.0, "ManaPerSecondCost": 25.0}, [], set())
    assert both["Mana"][0] == pytest.approx(25.0 / 100.0)
    assert leeching_uptime(None)([], {"_leeching": both})[2] == pytest.approx(1 - (1 - both["Life"][0]) * 0.75)
    # not removed at full Life: all fight; no Leech rate: never; no Leeching line: the box is left alone
    assert leeching_shares(stats, ["Life Leech effects are not removed when Unreserved Life is Filled"], set())["Life"][0] == 1.0
    assert leeching_shares(stats, [], {"Life Leech III"})["Life"][0] == 1.0
    assert leeching_uptime(None)([], {"_leeching": {}})[0] == 0.0
    assert leeching_uptime(None)([], {}) == (0.0, "")


def test_life_and_mana_flasks_are_drunk_only_below_full():
    """MECHC31 (RESEARCH26 R26-25 (a), R26-29 (b), an OVERCOUNT): a Life flask can't be used at full Life without
    Enduring Elixirs (0.5.0's fix wording), a Mana flask not at full Mana (assumed the same). Uses are capped at the dips
    a second below full: the boss's landed hits that reach the pool and the main skill's uses that cost it."""
    from dataclasses import replace

    from builder.search.flasks import Charges, pool_dips, uses_per_second

    flask = {"kind": "Flask", "chargesUsed": 1, "duration": 2.0}
    idle = Charges(flask=1.0)
    rate, how = uses_per_second(flask, idle, "", "Life")
    assert rate == 0.0 and "never below full Life" in how
    hit = replace(idle, boss_hits=0.4, taken_hit=500.0)
    rate, how = uses_per_second(flask, hit, "", "Life")
    assert rate == pytest.approx(0.4) and "dip" in how  # (charges allow 1/s; the boss's 0.4 landed hits/s cap it)
    assert uses_per_second(flask, replace(hit, life_effect_kept=True), "", "Life")[0] == pytest.approx(1.0)
    assert pool_dips(replace(idle, life_degen=5.0), "Life") is None  # (your own damage over time: never full)
    # Mana: the main skill's uses that cost Mana, and Mind over Matter's share of hits
    assert uses_per_second(flask, hit, "", "Mana")[0] == 0.0
    caster = replace(idle, mana_cost=20.0, main_rate=2.0)
    assert uses_per_second(flask, caster, "", "Mana")[0] == pytest.approx(1.0)
    assert pool_dips(replace(hit, mom=40.0), "Mana")[0] == pytest.approx(0.4)


def test_the_biggest_hit_row_rescales_each_hit_and_armour_cuts_less_of_a_bigger_one():
    """MECHC32 (the manager's ruling, 16 Nov 03:28: "show the biggest-hit reading as a row where it moves a defence
    number"): PoB gets each type's average hit; the row takes every hit at its type's biggest ordinary hit. Physical
    also loses Armour's share (PoB's Armour / (Armour + 10 x hit)), so it grows by more than biggest / average."""
    from builder.search import boss_hits
    from builder.search.objective import boss_recovery
    from builder.search.run import recovery_text

    big, mean = boss_hits.hit_sizes("Arbiter of Ash"), boss_hits.mean_hits("Arbiter of Ash")
    stats = {"_boss": "Arbiter of Ash", "PhysicalTakenHit": 500.0, "FireTakenHit": 600.0, "enemySkillTime": 0.7,
             "LifeRegenRecovery": 300.0}
    read = boss_hits.biggest_reading(stats)
    assert read["FireTakenHit"] == pytest.approx(600 * big["Fire"] / mean["Fire"])
    assert read["PhysicalTakenHit"] == pytest.approx(500 * big["Physical"] / mean["Physical"])  # (no Armour read)
    armoured = boss_hits.biggest_reading({**stats, "Armour": 5000.0, "PhysicalDamageReduction": 40.0})

    def dr(hit: float) -> float:
        return 100 * 5000 / (5000 + 10 * hit)

    kept = (1 - (dr(big["Physical"]) + 40 - dr(mean["Physical"])) / 100) / 0.6
    assert kept > 1 and armoured["PhysicalTakenHit"] == pytest.approx(500 * big["Physical"] / mean["Physical"] * kept)
    # the recovery share's row: lower than the headline, and in the report's text
    r = boss_recovery(stats)
    assert r["biggest_share"] < r["share"] and "biggest ordinary hit" in recovery_text(r)
    # no row when the boss's biggest hit of each type is its average (nothing would move)
    assert boss_hits.biggest_reading({**stats, "_boss": "Arbiter of Ash", "PhysicalTakenHit": 0.0}) is not None
    one = {"_boss": "Arbiter of Ash", "ColdTakenHit": 100.0}  # (a type it doesn't deal: average = biggest = 0)
    assert boss_hits.biggest_reading(one) is None
    # Heavy Stun from a channelled raised shield: the row shows the harsher share
    st = {**stats, "StunThreshold": 3000.0}
    row = recovery._biggest_row(st, lambda s: recovery.channel_heavy_stun(s)[0], lambda v: f"{v:.0%} of the fight")
    assert row.startswith("; every hit at its type's biggest") and row.endswith("of the fight")
    assert recovery.channel_heavy_stun(boss_hits.biggest_reading(st))[0] > recovery.channel_heavy_stun(st)[0]


def test_coat_of_reds_bleed_on_you_reads_the_bosss_landed_physical_hits():
    """MECHC32 (the manager, 16 Nov 03:54, from COV36): "25% chance to be inflicted with Bleeding when Hit" at the boss's
    landed hits at the DEFENCE cadence (the faster: the cautious side) x its Physical share; only the strongest Bleed
    deals damage, 5 s each; 15% of the boss's average Physical hit a second (KeywordPopups Bleeding), x2 while moving."""
    from builder.search import boss_hits

    line = "25% chance to be inflicted with Bleeding when Hit"
    stats = {"_boss": "Arbiter of Ash", "enemySkillTime": 0.7, "EvadeChance": 0, "EffectiveBlockChance": 0}
    taken, notes = boss_hits.self_bleed([line], stats)
    physical = boss_hits.type_shares("Arbiter of Ash")["Physical"]
    rate = 0.25 * boss_hits.land_share(stats) / 0.7 * physical
    bleeding = 1 - math.exp(-rate * 5)
    mean = boss_hits.mean_hits("Arbiter of Ash")["Physical"]
    assert taken == pytest.approx(bleeding * 0.15 * mean)
    assert "Without it: 0" in notes[0]["basis"] and "the cautious side" in notes[0]["basis"]
    assert f"{bleeding * 0.15 * boss_hits.hit_sizes('Arbiter of Ash')['Physical']:,.0f}" in notes[0]["basis"]  # (biggest)
    moving = boss_hits.self_bleed([line], {**stats, "_rolling": 0.5})[0]
    assert moving == pytest.approx(taken * 1.5)
    assert "Life only" in notes[0]["basis"]  # (Bleeding bypasses Energy Shield: objective.self_bleed_taken)
    assert boss_hits.self_bleed(["+100 to maximum Life"], stats) == (0.0, [])
    dodged = boss_hits.self_bleed([line], {**stats, "EvadeChance": 50})[0]
    assert 0 < dodged < taken


def test_coat_of_reds_bleed_needs_a_hit_that_reaches_life():
    """MECHC33 (RESEARCH28 R28-3, the manager's 09:36 ruling): a hit Energy Shield takes whole inflicts no Bleed, so on an
    ES build only the share of hits reaching Life counts: on Full ES those bigger than it (or bypassing it), below full
    all of them (the cautious headline), the ES left spread from 0 to full as a row."""
    from builder.search import boss_hits

    line = "25% chance to be inflicted with Bleeding when Hit"
    base = {"_boss": "Arbiter of Ash", "enemySkillTime": 0.7, "EvadeChance": 0, "EffectiveBlockChance": 0}
    no_es = boss_hits.self_bleed([line], base)[0]
    mean = boss_hits.mean_hits("Arbiter of Ash")
    hits = {f"{t}TakenHit": mean[t] for t in boss_hits.TYPES}
    # a huge Energy Shield that regenerates past every hit: on Full ES all fight, no hit bigger than it: no Bleed
    big = {**base, **hits, "EnergyShield": 1e7, "EnergyShieldRegenRecovery": 1e9}
    full, _ = __import__("builder.search.recovery", fromlist=["x"]).full_es_share(big)
    assert full > 0.99
    head, row, how = boss_hits.life_reach(big)
    assert head < 0.02 and row <= head and "reaching Life" in how
    assert boss_hits.self_bleed([line], big)[0] < no_es * 0.05
    # every type bypassing ES: every hit reaches Life, as with no ES
    bypass = {**big, **{f"{t}EnergyShieldBypass": 100 for t in boss_hits.TYPES}}
    assert boss_hits.life_reach(bypass)[0] == pytest.approx(1.0)
    assert boss_hits.self_bleed([line], bypass)[0] == pytest.approx(no_es)
    # a small ES with no recovery: never full again, every hit taken to reach Life (headline); the row is lower
    small = {**base, **hits, "EnergyShield": 1e6}
    head, row, _ = boss_hits.life_reach(small)
    assert head == pytest.approx(1.0) and row < 1.0
    taken, notes = boss_hits.self_bleed([line], {**small, "EnergyShield": 50.0})
    assert taken == pytest.approx(no_es) and "MEDIUM-HIGH" in notes[0]["basis"]
    assert boss_hits.life_reach({**base, **hits})[0] == 1.0  # (no Energy Shield: every hit reaches Life)


def test_redflares_shock_on_you_at_the_rate_its_power_charges_refill():
    """MECHC32 (the manager, 16 Nov 03:54, from COV36): Redflare Conduit's "Shocks you when you reach maximum Power
    Charges" at the build's own refills of the maximum (with its "Lose all Power Charges on reaching maximum": gains /
    maximum), 4 s each on players (KeywordPopups Shock), into PoB's "Are you Shocked?" box; the base 20% over PoB's 15%."""
    from builder.search.conditions import CONDITIONS, self_shock_taken_mods

    texts = ["20% chance to gain a Power Charge on Hit", "Lose all Power Charges on reaching maximum Power Charges",
             "Shocks you when you reach maximum Power Charges"]
    stats = {"Speed": 2.0, "HitChance": 100, "PowerChargesMax": 3, "_texts": texts}
    shocked = next(c for c in CONDITIONS if c.var == "conditionShocked")
    on, basis, share = shocked.value_fn(texts, stats)
    assert share == pytest.approx(min(1.0, 0.2 * 2.0 / 3 * 4)) and "without it: none" in basis
    assert shocked.value_fn(texts, {**stats, "ShockAvoidChance": 100})[0] == 0.0
    mods, how = self_shock_taken_mods(texts, [])
    assert mods[0]["value"] == pytest.approx(5.0) and mods[0]["condition"] == "Shocked"
    assert self_shock_taken_mods(texts, [{"kind": "Raw"}]) == ([], "")  # (Overcharge's own mod is there)
    # (MECHC33, RESEARCH28 R28-5) x your "effect of Shock on you" (PoB's SelfShockEffect): the Cultivated Redflare's 50%
    # reduced makes it 10%, 5% under PoB's box; your own inflicted magnitude is a row only
    mods, how = self_shock_taken_mods(texts, [], effect=50.0, inflict=1.5)
    assert mods[0]["value"] == pytest.approx(-5.0) and "x 50%" in how and "15%" in how and "cautious row" in how
    assert self_shock_taken_mods(texts, [], effect=75.0)[0] == []  # (exactly PoB's 15%)
    assert shocked.value_fn(texts[:2], {**stats, "_texts": texts[:2]}) == (0.0, "")


def test_a_power_charge_every_second_only_while_none_was_lost_recently():
    """MECHC33 (RESEARCH28 R28-5): the Cultivated Redflare's "Gain a Power Charge every Second if you haven't lost Power
    Charges Recently" runs only 4 s past the last loss: with "Lose all on reaching maximum", (T - 4) / T of each refill
    cycle; under a charge-spending skill, the part of each gap past 4 s; with nothing losing them, one a second."""
    from builder.search.conditions import _charge_rate, _reaching_maximum

    line = "Gain a Power Charge every Second if you haven't lost Power Charges Recently"
    hit = "20% chance to gain a Power Charge on Hit"
    lose = "Lose all Power Charges on reaching maximum Power Charges"
    texts = [line, hit, lose]
    stats = {"Speed": 2.0, "HitChance": 100, "PowerChargesMax": 3, "_texts": texts}
    rate, how = _charge_rate(line, "Power", stats)
    cycle = 4 + (3 - 0.4 * 4) / (1 + 0.4)  # (others 0.4/s: 5 s a refill)
    assert rate == pytest.approx((cycle - 4) / cycle) and "without the loss" in how
    assert _reaching_maximum("Power", 1.0, stats)[0] == pytest.approx(1 / cycle)
    # the other gains alone refill it inside 4 s: never on
    fast = {**stats, "Speed": 10.0}
    assert _charge_rate(line, "Power", fast)[0] == 0.0
    # no loss line, a skill spending one every 2 s: never 4 s without a loss; every 8 s: half the time
    keep = {**stats, "_texts": [line, hit]}
    assert _charge_rate(line, "Power", {**keep, "_consumers": {"Power": {"Skill": 0.5}}})[0] == 0.0
    assert _charge_rate(line, "Power", {**keep, "_consumers": {"Power": {"Skill": 0.125}}})[0] == pytest.approx(0.5)
    assert _charge_rate(line, "Power", keep)[0] == pytest.approx(1.0)


def test_widows_reign_and_your_own_bleed_reach_life_only():
    """MECHC32: Widow's Reign (RESEARCH26 R26-40 (a), SETTLED): reserved Life can't be healed, so Life recovery counts only
    past a damage-free window between the boss's landed hits (e^(-hits x window)), 0 under your own steady Life loss.
    The manager's 09:09 ruling: Coat of Red's Bleed on you is Life's own loss (it bypasses Energy Shield), through
    Build.added_stats, not PoB's Physical degen: it stops Life Recharge, never ES Recharge."""
    from builder.search import objective
    from builder.search.run import recovery_text

    stats = {"_boss": "Arbiter of Ash", "PhysicalTakenHit": 300.0, "FireTakenHit": 300.0, "enemySkillTime": 1.0,
             "LifeRegenRecovery": 400.0, "ManaRegenRecovery": 50.0}
    plain = objective.boss_recovery(stats)
    held = objective.boss_recovery({**stats, objective.LIFE_RESERVED_SECONDS: 3.0})
    open_share = math.exp(-plain["hits"] * 3.0)
    assert held["life_reserved_open"] == pytest.approx(open_share)
    assert held["parts"]["Life"] == pytest.approx(plain["parts"]["Life"] * open_share)
    assert "Widow's Reign" in recovery_text(held) and plain["life_reserved_open"] is None
    bled = {**stats, objective.SELF_BLEED_PER_SECOND: 100.0, "PhysicalTakenDotMult": 0.8}
    assert objective.self_bleed_taken(bled) == pytest.approx(80.0)
    assert objective.own_loss({**bled, "EnergyShield": 500})["Life"] == pytest.approx(80.0)
    assert objective.own_loss({**bled, "EnergyShield": 500})["EnergyShield"] == 0.0
    assert objective.boss_recovery(bled)["incoming"] == pytest.approx(plain["incoming"] + 80.0)
    # (your own steady Life loss keeps the reservation up: no Life recovery at all under Widow's Reign)
    assert objective.boss_recovery({**bled, objective.LIFE_RESERVED_SECONDS: 3.0})["life_reserved_open"] == 0.0
    from builder.search.assumptions import LIFE_RESERVED

    for text in ("Life that would be lost by taking Damage is instead Reserved\nuntil you take no Damage to Life for 3 seconds",
                 "Life that would be lost by taking Damage is instead Reserved until you take no Damage to Life for 3 seconds"):
        assert LIFE_RESERVED.search(text).group(1) == "3"  # (the item's two lines, or one)


def test_the_current_energy_shield_box_reads_the_share_on_full_energy_shield():
    """MECHC33 (COV37 via the manager, 16 Nov 09:09): Silks of Veneration's and The Mutable Star's current-ES lines read
    PoB's "Current Energy Shield percentage" box, which the tool now sets from recovery.full_es_share: on full that share
    of the fight, below full counted as empty (the lower reading for a defence), the spread reading in the note; 0% is
    sent as CURRENT_ES_EMPTY (PoB reads 0 as its placeholder 100)."""
    from builder.search.conditions import CONDITIONS, CURRENT_ES_EMPTY, current_es_percent

    star = ["Defend against Hits as though you had 1% more Armour per 1% current Energy Shield"]
    value, how = current_es_percent(star, {"_full_es": (0.6, "refilled at 100/s")})
    assert value == 60 and "80%" in how and "lower reading" in how  # (spread: 60 + 40 / 2)
    assert current_es_percent(star, {"_full_es": (0.0, "never full")})[0] == CURRENT_ES_EMPTY
    assert current_es_percent(star, {})[0] == CURRENT_ES_EMPTY
    assert current_es_percent(["+10 to maximum Life"], {"_full_es": (0.6, "")}) == (0.0, "")
    assert any(c.var == "multiplierCurrentEnergyShield" for c in CONDITIONS)


def test_a_channelled_skill_on_a_cooldown_pays_its_cost_only_while_channelled():
    """MECHC33 (GEM15's find): PoB charges a per-minute cost all fight; Flameblast channels ChannelTime per release on its
    cooldown, so the cost runs channel / max(cooldown, channel) of the fight (the cautious headline), channel /
    (channel + cooldown) the other reading; no channel time from PoB: kept whole."""
    from builder.search import recovery

    # (MECHC34, R28-14: the cooldown starts at release by default, at the channel's start for Flame Breath only)
    share, other, how = recovery.channel_cost_share({"ChannelTime": 2.5, "Cooldown": 10.0, "Speed": 0.1}, "Flameblast")
    assert share == pytest.approx(2.5 / 12.5) and other == pytest.approx(0.25) and "without this: 100%" in how
    breath = recovery.channel_cost_share({"ChannelTime": 2.5, "Cooldown": 10.0}, "Flame Breath")
    assert breath[:2] == (pytest.approx(0.25), pytest.approx(2.5 / 12.5))
    assert recovery.channel_cost_share({"ChannelTime": 12.0, "Cooldown": 10.0}, "Flame Breath")[0] == 1.0
    assert recovery.channel_cost_share({"Cooldown": 10.0})[:2] == (1.0, 1.0)


def test_a_main_skill_that_cant_gain_power_charges_while_used_feeds_none():
    """MECHC33 (GEM15's find): Flicker Strike's "You cannot gain Power Charges while using this Skill": its hits make no
    Power Charges, and other gains land only in the share of the fight away from it (dodge rolls, as read)."""
    from builder.search.conditions import NO_POWER_STAT, _charge_rate

    hit = "20% chance to gain a Power Charge on Hit"
    second = "Gain a Power Charge every 4 seconds"
    stats = {"Speed": 2.0, "HitChance": 100, "PowerChargesMax": 3, "_main_no_power": NO_POWER_STAT}
    assert _charge_rate(hit, "Power", {**stats, "_main_no_power": None})[0] == pytest.approx(0.4)
    assert _charge_rate(hit, "Power", stats)[0] == 0.0
    rate, how = _charge_rate(second, "Power", {**stats, "_rolling": 0.1})
    assert rate == pytest.approx(0.025) and "can't gain Power Charges while used" in how and "without it: 0.25" in how
    assert _charge_rate("20% chance to gain a Frenzy Charge on Hit", "Frenzy", stats)[0] == pytest.approx(0.4)


def test_standing_still_at_range_leaves_the_boss_spot_infusion_remnants():
    """MECHC33 (RESEARCH26 R26-35): Snap's, Living Bomb's, Frost Bomb's and Siphon Elements' Remnants appear at the boss;
    standing still at a ranged distance can't collect them (an OVERCOUNT before); Orb of Storms cast at your feet can."""
    from builder.search.assumptions import infusion_supply, still_far

    present = {"Living Bomb": {}, "Orb of Storms": {}}
    near, _ = infusion_supply(present, [], set(), bomb=(0.5, ""), orb=(0.2, ""))
    far, used = infusion_supply(present, [], set(), bomb=(0.5, ""), orb=(0.2, ""), far=True)
    assert near["Fire"] == pytest.approx(0.5) and far["Fire"] == 0.0
    assert far["Lightning"] == near["Lightning"] == pytest.approx(0.2)
    assert any("out of reach" in u for u in used) and any("at your feet" in u for u in used)
    assert still_far({"input": {"conditionStationary": True, "enemyDistance": 50}})
    assert not still_far({"input": {"conditionStationary": True, "enemyDistance": 20}})
    assert not still_far({"input": {"enemyDistance": 50}})


def test_the_games_elemental_infusions_not_removed_line_is_read():
    """MECHC33: "Skills have N% chance to not remove Elemental Infusions but still count as consuming them" (PoB: no mod)
    feeds KEEP_INFUSION as the older wording did; the Archon-conditional variant isn't read (named)."""
    from builder.search.assumptions import KEEP_INFUSION

    assert KEEP_INFUSION.search("Skills have 10% chance to not remove Elemental Infusions but still count as consuming them")
    assert KEEP_INFUSION.search("50% chance to not remove an Infusion but still count as consuming it")
    assert not KEEP_INFUSION.search("Skills have 45% chance to not remove Elemental Infusions but still count as consuming "
                                    "them if you've lost an Archon Buff in the past 6 seconds")


def test_life_when_you_use_a_mana_flask_at_the_flasks_uses():
    """MECHC33 (COV37 via the manager, 11:48): "Recover N% of maximum Life when you use a Mana Flask" at the Mana flask's
    uses a second (flasks.uses_per_second, capped at the dips below full Mana); no longer listed as unread."""
    from builder.search import recovery

    line = "Recover 9% of maximum Life when you use a Mana Flask"
    income = recovery.Income()
    recovery.mana_flask_income(income, {"Life": 2000.0}, [line], 0.25, "Flask 2: 1.00 charges/s / 4 per use")
    assert income.pool("Life") == pytest.approx(0.09 * 2000 * 0.25)
    assert recovery.unread([line]) == []
    empty = recovery.Income()
    recovery.mana_flask_income(empty, {"Life": 2000.0}, ["+10 to maximum Life"], 0.25, "")
    assert empty.pool("Life") == 0.0


def test_twister_hits_rise_with_the_number_of_twisters_and_never_pass_the_cap():
    """MECHC35 (the user's answer 16 Nov ~21:10, "it depends on the number of twisters"; RESEARCH28 R28-10): the headline
    is 1 + (k - 1)(1 - e^(-N 2 rho v 0.66 / A)), k = floor(D / 0.66) + 1, A = pi 6^2; the open arena's 1 a row.
    RESEARCH28's table: plain 1.30 on the Arbiter (0.4 m) / 1.27 on the worst case (0.3 m); 7 twisters at 3x speed 4.24.
    Hits rise with the twisters, duration, area and speed, and never pass k."""
    from builder.search import recovery

    base = {"ProjectileCount": 1, "ProjectileSpeedMod": 1.0, "Duration": 3.0, "AreaOfEffectRadiusMetres": 0.5}
    head, open_row, how = recovery.twister_hits(base, 0.4)
    assert open_row == 1.0 and head == pytest.approx(1.30, abs=0.01) and "at most 5" in how
    assert "the user's observation" in how and "open arena 1" in how
    assert recovery.twister_hits(base, 0.3)[0] == pytest.approx(1.27, abs=0.01)
    many = recovery.twister_hits({**base, "ProjectileCount": 7, "ProjectileSpeedMod": 3.0}, 0.4)[0]
    assert many == pytest.approx(4.24, abs=0.05)
    assert recovery.twister_hits({}, 0.4)[0] == pytest.approx(head)  # (PoB gives nothing: the base numbers)
    for key, more in (("ProjectileCount", 4), ("Duration", 5.0), ("AreaOfEffectRadiusMetres", 1.0),
                      ("ProjectileSpeedMod", 2.0)):
        assert recovery.twister_hits({**base, key: more}, 0.4)[0] > head, key
    huge = {"ProjectileCount": 60, "ProjectileSpeedMod": 5.0, "Duration": 3.0, "AreaOfEffectRadiusMetres": 2.0}
    capped = recovery.twister_hits(huge, 1.0)[0]
    assert 4.9 < capped <= 5.0  # (k = 5 at 3 s)
    assert recovery.twister_hits({**huge, "Duration": 6.0}, 1.0)[0] <= 10.0  # (k = floor(6 / 0.66) + 1 = 10)


def test_sprinting_counts_0_in_the_headline_and_a_play_row_only_with_sprint_lines():
    """MECHC34 (RESEARCH26 R26-22): no sprint line, no row; Unwavering Stance, 0; otherwise the headline stays 0 and the
    row sprints 6 m after each boss reposition at 3.7 m/s x (MovementSpeedMod + (50 + sprint increases) / 100)."""
    assert recovery.sprint_row(["10% increased maximum Life"], {}, "The Bodach") is None
    guard = ("When you stop Sprinting, gain Guard equal to 4% of maximum Life per second spent Sprinting, up to a maximum "
             "of 20%, for 4 seconds")
    lines = ["25% increased Evasion Rating while Sprinting", guard, "20% increased Guard gained",
             "10% increased Movement Speed while Sprinting", "Lose 5% of maximum Life per second while Sprinting"]
    assert "can't sprint" in recovery.sprint_row(lines + ["Cannot Dodge Roll or Sprint"], {}, "The Bodach")["value"]
    row = recovery.sprint_row(lines, {"MovementSpeedMod": 1.4}, "Xesht, We That Are One")
    each = 6.0 / (3.7 * (1.4 + 0.6))  # 0.81 s a sprint
    assert row["value"].startswith("0 headline") and f"{0.1 * each:.1%}" in row["value"]
    assert f"Guard of {4 * each * 1.2:.1f}% of maximum Life" in row["basis"] and "40% of the fight" in row["basis"]
    assert "25% increased Evasion" in row["play"] and f"{5 * each:.1f}% of maximum Life lost" in row["basis"]
    worst = recovery.sprint_row(lines, {"MovementSpeedMod": 1.4}, "Pinnacle (worst case)")
    assert "Arbiter of Ash's row" in worst["basis"] and f"{0.55 * each:.1%}" in worst["value"]
    # a long sprint hits the 20% cap: the lower reading (cap after the increase) heads, the other is named
    slow = recovery.sprint_row([guard.replace("4%", "8%"), "50% increased Guard gained"], {"MovementSpeedMod": 0.1}, "The Bodach")
    assert "Guard of 20.0%" in slow["basis"] and "before it 30.0%" in slow["basis"]


def test_times_hit_recently_counts_the_bosss_landed_hits_in_4_seconds():
    """MECHC34 (PATCH_CHECK MB-17): Unbending's "for each time you've been Hit by an Enemy Recently" reads PoB's count box,
    never set before (0); now the boss's landed hits a second (gain_hits) x 4 s; no such line, 0."""
    from builder.search import boss_hits, conditions

    line = "10% increased Stun Threshold for each time you've been Hit by an Enemy Recently, up to 100%"
    stats = {"EvadeChance": 20, "EffectiveBlockChance": 0, "enemySkillTime": 0.7, "_boss": "The Bodach"}
    count, how = conditions.hits_recently([line], stats)
    assert count == pytest.approx(boss_hits.gain_hits(stats)[0] * 4, abs=0.01) and count > 0 and "4 s" in how
    assert conditions.hits_recently(["10% increased Stun Threshold"], stats) == (0.0, "")
    assert any(c.var == "multiplierBeenHitRecently" for c in conditions.CONDITIONS)


def test_shapeshifted_recently_lines_read_the_builds_shifts():
    """MECHC34 (PATCH_CHECK MB-8): PoB's two "Shapeshifted to ... recently?" boxes, never set before, now from the build's
    shifts (human_shifts): min(1, 4 s x shifts a second) of the fight; no shift after the start, 0."""
    from builder.search import conditions

    animal = conditions.shifted_recently(conditions.SHIFTED_ANIMAL_RECENTLY, "an Animal")
    human = conditions.shifted_recently(conditions.SHIFTED_HUMAN_RECENTLY, "Human")
    line = "30% increased Attack Damage if you have Shapeshifted to an Animal form Recently"
    on, how, share = animal([line], {"_human_shifts": (0.1, "Wolf presses")})
    assert on is True and share == pytest.approx(0.4) and "Wolf presses" in how
    assert animal([line], {"_human_shifts": (0.5, "")})[2] == 1.0
    assert animal([line], {"_human_shifts": (0.0, "none")})[0] == 0.0
    assert animal(["12% increased Spell Damage if you have Shapeshifted to Human form Recently"], {}) == (0.0, "")
    assert human(["12% increased Spell Damage if you have Shapeshifted to Human form Recently"],
                 {"_human_shifts": (0.2, "")})[2] == pytest.approx(0.8)
    names = {c.var for c in conditions.CONDITIONS}
    assert {"conditionShapeshiftToAnimal", "conditionShapeshiftToHuman"} <= names


def test_fragile_regrowth_climbs_between_landed_hits():
    """MECHC34 (PATCH_CHECK MB-13): Dustbloom's stacks, 1 a second up to 10, all lost on each landed hit (rate h, at
    random): (1 - e^(-10 h)) / h; PoB's count box was never set (0)."""
    from builder.search import boss_hits, conditions

    lines = ["Maximum 10 Fragile Regrowth", "0.5% of maximum Life Regenerated per second per Fragile Regrowth",
             "Lose all Fragile Regrowth when Hit", "Gain 1 Fragile Regrowth each second"]
    stats = {"EvadeChance": 50, "EffectiveBlockChance": 0, "enemySkillTime": 2.0}
    h = boss_hits.landing_hits(stats)
    count, how = conditions.fragile_regrowth(lines, stats)
    assert 0 < count < 10 and count == pytest.approx((1 - math.exp(-10 * h)) / h, abs=0.01) and "MEDIUM" in how
    assert conditions.fragile_regrowth(lines[:2] + lines[3:], stats)[0] == 10.0  # (nothing loses it)
    assert conditions.fragile_regrowth(["Maximum 10 Fragile Regrowth"], stats) == (0.0, "")
    assert any(c.var == "multiplierFragileRegrowth" for c in conditions.CONDITIONS)


def test_wooden_wall_takes_hits_from_the_nearest_totems_life():
    """MECHC34 (PATCH_CHECK MD-6): PoB's Total Totem Life box was never set (0), so Wooden Wall's 20% never reached a
    totem; now PoB's TotemLife for a totem main skill; none without a totem main skill."""
    from builder.search import conditions

    line = "20% of Damage from Hits is taken from your nearest Totem's Life before you"
    assert conditions.totem_life_before_you([line], {"TotemLife": 4321.4})[0] == 4321
    assert conditions.totem_life_before_you([line], {})[0] == 0.0
    assert conditions.totem_life_before_you(["10% increased Totem Life"], {"TotemLife": 100}) == (0.0, "")
    assert any(c.var == "TotalTotemLife" for c in conditions.CONDITIONS) and "TotemLife" in conditions.EVIDENCE_FIELDS


def test_a_mana_flask_drunk_at_low_mana_recovers_its_more():
    """MECHC34 (PATCH_CHECK K5-3): "N% more Recovery if used while on Low Mana", which PoB never applies (leftover text),
    multiplies a Mana flask's recovery; nothing on a Life flask or without the line."""
    from builder.search import flasks

    raw = "Rarity: Magic\nUltimate Mana Flask of the Wise\n76% more Recovery if used while on Low Mana\n"
    assert flasks.low_mana_more(raw, "Mana") == pytest.approx(1.76)
    assert flasks.low_mana_more(raw, "Life") == 1.0 and flasks.low_mana_more("Ultimate Mana Flask", "Mana") == 1.0


def test_royal_acolytes_explosion_is_an_add_hit_kept_out_of_atziris_own():
    """MECHC34 (R26-29 (a)): the acolyte's explosion is in the hit data as an add (Physical, 353.67 raw, one per 45 s at
    least), named in the boss-hit row, and not in Atziri's hit sizes or averages."""
    from builder.search import boss_hits

    name = "Atziri, the Red Queen"
    adds = boss_hits.skill_hits(name, adds=True)
    assert [r["id"] for r in adds] == ["VaalZealotSuicideExplosion"] and adds[0]["hit"] == {"Physical": [353.67, "attack"]}
    assert all(not r.get("add") for r in boss_hits.skill_hits(name))
    assert "VaalZealotSuicideExplosion 354 Physical" in boss_hits.hits_note(name)["value"]
    assert boss_hits.add_hits_text("Arbiter of Ash") == ""


def test_raise_shield_taps_heavy_stun_only_on_a_blocked_hit_that_fills_the_bar():
    """MECHC34 (MECHC30's leftover (c)): each 0.55 s tap blocks a hit 1 - e^(-0.55 x Blockable rate) of the time; it stuns
    when that hit fills the bar alone (the bar empties between taps), less Evasion's avoidance; 3 s a stun."""
    from builder.search import bosses, boss_hits

    weights = boss_hits.type_shares(bosses.DEFAULT)
    stats = {"PhysicalTakenHit": 3000, "FireTakenHit": 100, "StunThreshold": 2000, "enemySkillTime": 0.7,
             "_boss": bosses.DEFAULT}
    unavoidable, evadable = boss_hits.shares(stats)
    caught = 1 - math.exp(-0.55 * max(1 - unavoidable - evadable, 0.0) / 0.7)
    share, how = recovery.raised_tap_heavy_stun(stats, 1.0, 0.25)
    assert share == pytest.approx(0.25 * caught * weights["Physical"] * 3) and "Raise Shield tap" in how
    assert recovery.raised_tap_heavy_stun({**stats, "EvadeChance": 50}, 1.0, 0.25)[0] == pytest.approx(share / 2)
    assert recovery.raised_tap_heavy_stun(stats, 2.0 * 5, 0.25)[0] == 0.0  # (Dionadair-sized threshold: no full bar)
    assert recovery.raised_tap_heavy_stun(stats, 1.0, 0.0) == (0.0, "")



def test_runic_reprieve_channels_heavy_stun_only_on_a_hit_that_fills_the_bar():
    """MECHC35 (MECHC34's next step): each Reprieve channel blocks one boss hit with no Heavy Stun decay; it stuns when
    that hit fills the bar alone (no Evasion clause), 3 s a stun, at the channels' rate."""
    from builder.search import bosses, boss_hits

    weights = boss_hits.type_shares(bosses.DEFAULT)
    stats = {"PhysicalTakenHit": 3000, "FireTakenHit": 100, "StunThreshold": 2000, "enemySkillTime": 0.7,
             "_boss": bosses.DEFAULT}
    share, how = recovery.reprieve_heavy_stun(stats, 1.0, 0.2)
    assert share == pytest.approx(0.2 * weights["Physical"] * 3) and "Runic Reprieve" in how
    assert recovery.reprieve_heavy_stun({**stats, "EvadeChance": 50}, 1.0, 0.2)[0] == pytest.approx(share)
    assert recovery.reprieve_heavy_stun(stats, 10.0, 0.2)[0] == 0.0
    assert recovery.reprieve_heavy_stun(stats, 1.0, 0.0) == (0.0, "")

def test_each_boss_gets_its_own_armour_and_evasion_and_no_penetration():
    """MECHC34 (R28-11 CD-1..3): the level-82 table x (1 + MonsterTypes' +%), no elemental penetration; the King's two
    forms by fight share; the worst case the highest per form (Olroth's +80% Armour, the King's first form's +40%)."""
    from builder.search import bosses

    olroth = bosses.defence_vars("Olroth, Origin of the Fall")
    assert olroth == {"enemyArmour": round(5375 * 1.8), "enemyEvasion": 941, "enemyLightningPen": 0, "enemyColdPen": 0,
                      "enemyFirePen": 0}
    assert bosses.defence_vars("Xesht, We That Are One")["enemyArmour"] == 5375
    king = bosses.defence_vars("The King in the Mists")
    assert king["enemyArmour"] == round(5375 * (1 + (0.43 * 20 + 0.57 * 65) / 100))
    assert king["enemyEvasion"] == round(941 * (1 + 0.43 * 0.4))
    worst = bosses.defence_vars(bosses.WORST)
    assert worst["enemyArmour"] == max(bosses.defence_vars(n)["enemyArmour"] for n in bosses.REAL)
    assert worst["enemyEvasion"] == round(941 * 1.4)
    assert set(bosses.BOSS_DEFENCES) >= set(bosses.REAL)
    assert "3% penetration" in bosses.note()["basis"] and "Armour 9,675" in bosses.note()["basis"]


def test_ab_aeternos_overencumbrance_slows_you_at_your_own_roll_pace():
    """MECHC35 (COV38's sweep): Ab Aeterno's current line "Gain Overencumbrance for 4 seconds when you Dodge Roll"
    (Overencumbered: 10% Slow a stack, stacking and refreshing): a roll every 5 s (one per telegraphed hit) keeps one
    stack 80% of the time, 8%; a build relying on rolling plays at most one roll every 4 s, one stack all the time, 10%;
    Slowing Potency lines scale it; "Your speed is unaffected by Slows" cancels it; no line, nothing."""
    from builder.search import recovery

    line = "Gain Overencumbrance for 4 seconds when you Dodge Roll"
    assert recovery.overencumbrance_slow(["+10 to maximum Life"], False) == (0.0, "")
    slow, how = recovery.overencumbrance_slow([line], False)
    assert slow == pytest.approx(8.0) and "80% of the time" in how
    slow, how = recovery.overencumbrance_slow([line], True)
    assert slow == pytest.approx(10.0) and "no cap found" in how
    assert recovery.overencumbrance_slow([line, "20% reduced Slowing Potency of Debuffs on You"], True)[0] == \
        pytest.approx(8.0)
    assert recovery.overencumbrance_slow([line, "Your speed is unaffected by Slows"], True)[0] == 0.0


def test_chance_to_be_poisoned_ignited_shocked_lines_cost_you_at_the_bosss_hits():
    """MECHC35 (COV38's sweep, Coat of Red's pattern): "+25% chance to be Poisoned" Poisons on the boss's landed hits with
    Physical or Chaos damage (2 s each, 20% of the hit a second, Life only); "+25% chance to be Ignited" and "+20% chance
    to be Shocked" count only the share they add over the boss's own; your avoidance cancels each; no line, nothing."""
    from builder.search import boss_hits, bosses

    base = {"_boss": bosses.DEFAULT, "enemySkillTime": 1.0, "Life": 4000.0, "AilmentThreshold": 4000.0,
            "FireTakenHit": 1000.0, "LightningTakenHit": 1000.0}
    assert boss_hits.self_ailments(["+10 to maximum Life"], base) == (0.0, 0.0, 0.0, [])
    lines = ["+25% chance to be Poisoned", "+25% chance to be Ignited", "+20% chance to be Shocked"]
    poison, burn, shock, notes = boss_hits.self_ailments(lines, base)
    assert poison > 0 and burn > 0 and shock > 0 and len(notes) == 3
    assert "Life only" in notes[0]["basis"] and "without it" in notes[1]["value"]
    immune = {**base, "PoisonAvoidChance": 100, "IgniteAvoidChance": 100, "ShockAvoidChance": 100}
    assert boss_hits.self_ailments(lines, immune)[:3] == (0.0, 0.0, 0.0)
    more = boss_hits.self_ailments(["+50% chance to be Shocked"], base)[2]
    assert more > shock
    # the Poison doubles with twice the chance (all Poisons stack); the Ignite / Shock shares never pass 100%
    assert boss_hits.self_ailments(["+50% chance to be Poisoned"], base)[0] == pytest.approx(2 * poison)
    assert boss_hits.self_ailments(["+100% chance to be Shocked"], base)[2] <= 20.0


def test_the_bosss_own_ignite_and_shock_on_you_count_as_damage_taken():
    """MECHC37 (the manager's 17 Nov ruling (a)): the boss's own Ignites (20% of its average Fire hit a second while
    Ignited) and Shocks (+20% damage taken x your effect of Shock on you, at the share Shocked) count for every build; a
    boss without the type, your avoidance or unaffected-by-Shock cancels each; PoB's Shocked box already on adds none.
    The Ignite reaches the recovery share through Build.added_stats at FireTakenDotMult, never as a steady own loss."""
    from builder.search import boss_hits, objective

    base = {"enemySkillTime": 1.0, "Life": 4000.0, "AilmentThreshold": 2000.0, "FireTakenHit": 2000.0,
            "LightningTakenHit": 2000.0}
    ash = boss_hits.boss_ailments_on_you({**base, "_boss": "Arbiter of Ash"})
    assert ash["ignite"] > 0 and 0 < ash["ignited"] < 1 and ash["shock"] == 0.0 and len(ash["notes"]) == 1
    hit = boss_hits.mean_hits("Arbiter of Ash")["Fire"]
    assert ash["ignite"] == pytest.approx(ash["ignited"] * 0.20 * hit)
    divinity = boss_hits.boss_ailments_on_you({**base, "_boss": "Arbiter of Divinity"})
    assert divinity["ignite"] == 0.0 and divinity["shock"] > 0
    assert divinity["shock"] == pytest.approx(20.0 * divinity["shocked"])
    assert "without it" in divinity["notes"][0]["play"]
    halved = boss_hits.boss_ailments_on_you({**base, "_boss": "Arbiter of Divinity", "SelfShockEffect": 50.0})
    assert halved["shock"] == pytest.approx(divinity["shock"] / 2)
    assert boss_hits.boss_ailments_on_you({**base, "_boss": "Arbiter of Divinity"}, shocked_box=True)["shock"] == 0.0
    immune = boss_hits.boss_ailments_on_you({**base, "_boss": "Atziri, the Red Queen", "IgniteAvoidChance": 100,
                                             "ShockAvoidChance": 100})
    assert immune["ignite"] == 0.0 and immune["shock"] == 0.0
    assert boss_hits.boss_ailments_on_you({**base, "_boss": "Zarokh, the Temporal"})["notes"] == []
    # the recovery share: incoming damage at the Fire DoT multiplier, not a steady loss that stops Recharge
    stats = {"EnergyShield": 3000.0, "EnergyShieldRecharge": 600.0, objective.BOSS_IGNITE_PER_SECOND: 400.0,
             "FireTakenDotMult": 0.25}
    assert objective.boss_ignite_taken(stats) == pytest.approx(100.0)
    assert objective.own_loss(stats) == {"EnergyShield": 0.0, "Life": 0.0}


def test_the_bosss_shock_counts_in_full_in_the_floors_from_seven_percent():
    """MECHC38 (the manager's ruling (i), 18 Nov 02:30): Shocked 7%+ (and under 99.5%) of the fight, the boss's Shock is
    registered for the floors (in full in their recalculation, the average elsewhere); under 7%, at 99.5%+, with none, or
    with PoB's Shocked box on, it is the average alone and nothing is registered."""
    from builder.search import boss_hits

    def on_you(shocked: float, full: float = 20.0) -> dict:
        return {"shocked": shocked, "shock_full": full, "shock": full * shocked}

    assert boss_hits.boss_shock_for_floors(on_you(0.4)) == (pytest.approx(8.0), 0.4)
    assert boss_hits.boss_shock_for_floors(on_you(0.4), average=False) == (pytest.approx(20.0), 0.4)
    assert boss_hits.boss_shock_for_floors(on_you(0.07), average=False) == (pytest.approx(20.0), 0.07)
    assert boss_hits.boss_shock_for_floors(on_you(0.05), average=False) == (pytest.approx(1.0), 0.0)
    assert boss_hits.boss_shock_for_floors(on_you(0.996), average=False) == (pytest.approx(19.92), 0.0)
    assert boss_hits.boss_shock_for_floors({"shocked": 0.5, "shock_full": 20.0, "shock": 0.0}) == (0.0, 0.0)
    base = {"enemySkillTime": 1.0, "Life": 4000.0, "AilmentThreshold": 2000.0, "LightningTakenHit": 2000.0,
            "_boss": "Arbiter of Divinity"}
    out = boss_hits.boss_ailments_on_you(base)
    row = out["notes"][0]
    if 0.07 <= out["shocked"] < 0.995:
        assert "the defence floors read it in full" in row["value"] and "ruling" in row["basis"]


def test_the_bosss_chill_slows_you_and_sierran_inheritance_reverses_it():
    """MECHC38: the boss's Chills on you as action speed (an OVERCOUNT before: none counted), at the share Chilled x the
    mean magnitude (100% x hit / Ailment Threshold, 30% floor, 50% cap); Sierran Inheritance's reversed Chill (the
    manager's ruling (iii), R31-1) turns it positive, and its all-damage line lets every type's hit Chill; Scold's
    Bridle's self-hits Chill too with that line; unaffected by Slows, avoidance and the 6 s immunity lower it."""
    import math

    from builder.search import boss_hits

    reverse = "The Effect of Chill on you is reversed"
    every = "All Damage taken from Hits Contributes to Magnitude of Chill inflicted on you"
    base = {"enemySkillTime": 1.0, "Life": 5000.0, "AilmentThreshold": 2500.0, "ColdTakenHit": 1900.0,
            "PhysicalTakenHit": 1500.0, "FireTakenHit": 2000.0, "LightningTakenHit": 1800.0, "ChaosTakenHit": 1000.0,
            "_boss": "Pinnacle (worst case)"}
    slow, rows = boss_hits.boss_chill_on_you(base, [])
    cold = boss_hits.land_share(base) * boss_hits.type_shares("Pinnacle (worst case)")["Cold"]
    assert slow == pytest.approx(-(1 - math.exp(-2 * cold)) * 50.0)
    assert rows[0]["label"] == "The boss Chills you*" and "without it" in rows[0]["play"]
    fast, rows = boss_hits.boss_chill_on_you(base, [reverse])
    assert fast == pytest.approx(-slow) and rows[0]["label"].startswith("Reversed Chill")
    wide, _ = boss_hits.boss_chill_on_you(base, [reverse, every])
    assert wide > fast
    small = {**base, "ColdTakenHit": 500.0}  # 20% of the threshold: under the 30% floor
    assert boss_hits.boss_chill_on_you(small, [])[0] == 0.0
    assert boss_hits.boss_chill_on_you({**base, "ChillAvoidChance": 100}, [])[0] == 0.0
    assert boss_hits.boss_chill_on_you(base, ["Your speed is unaffected by Slows"])[0] == 0.0
    immune, _ = boss_hits.boss_chill_on_you(base, ["You cannot be Chilled for 6 seconds after being Chilled"])
    assert immune == pytest.approx(-2.0 / (6 + 1 / cold) * 50.0)
    assert boss_hits.boss_chill_on_you(base, [], chilled_box=True) == (0.0, [])
    ash, ash_rows = boss_hits.boss_chill_on_you({**base, "_boss": "Arbiter of Ash"}, [])  # (no Cold hits: a said 0)
    assert ash == 0.0 and ash_rows[0]["value"].startswith("0") and "Ailment Threshold" in ash_rows[0]["basis"]
    assert "avoid every Chill" in boss_hits.boss_chill_on_you({**base, "ChillAvoidChance": 100}, [])[1][0]["basis"]
    scold = {**small, "_boss": "Arbiter of Ash", "ManaPerSecondCost": 2000.0, "Speed": 2.0}  # 1,000 Physical a cast
    lines = [reverse, every, "Take 100% of Mana Costs you pay for Skills as Physical Damage"]
    with_scold, rows = boss_hits.boss_chill_on_you(scold, lines)
    assert with_scold > 0 and "Scold" in rows[0]["basis"]


def test_charges_on_a_consume_count_at_the_consume_rate_and_skill_is_not_kill():
    """MECHC38 (GEM19's routed UNDERCOUNT): Siphoning Strike's Power Charge on a consumed Shock counts at its consumes a
    second (Build.consume_gains, conditions' "_consume_gains"); without that rate, a gain after a consume or a Primed
    Heavy Stun is 0, said; "skill" in a description's last sentence is no kill (before: Siphoning Strike, Disengage,
    Wing Blast, Frenzied Riposte read as on-kill gains); "granted by" (Abyssal Apparition) and Ferocity's "cannot
    generate" are no gains."""
    from builder.search import conditions

    siphon = ("Dash to a target and Strike them with your Quarterstaff. If the target is Shocked, Consume their Shock to "
              "release a Lightning shockwave around the target and grant you a Power Charge. This skill cannot be "
              "Ancestrally Boosted.")
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "_boss": "Arbiter of Ash",
             "_kills": (5.0, 5.0, "five a second")}
    rate, how = conditions._charge_rate(siphon, "Power", stats)
    assert rate == 0.0 and "not modelled" in how
    rate, how = conditions._charge_rate(siphon, "Power", {**stats, "_consume_gains": {siphon: (0.7, "consumes")}})
    assert rate == pytest.approx(0.7)
    wing = ("Shapeshift into a Wyvern and launch yourself backwards with a powerful wingbeat that Knocks Back enemies. "
            "Enemies Primed for Heavy Stun will be stunned, release a shockwave, and have a chance to grant you a Power "
            "Charge. This skill can be used while using other skills to interrupt them.")
    assert conditions._charge_rate(wing, "Power", stats)[0] == 0.0
    apparition = ("Consume a Power Charge when you cast a damaging Spell to trigger an Abyssal Apparition who will also "
                  "cast that same Spell for you. Does not work with Channelling Spells, Spells with a Cooldown, Triggered "
                  "Spells, Spells granted by items, or Spells which already consume a Power Charge. This Skill is not "
                  "directly Supportable.")
    assert conditions._charge_rate(apparition, "Power", stats) is None
    ferocity = ("Supports Skills that you use yourself. Supported Skills will consume a Frenzy Charge on use if possible, "
                "and will gain significant Skill Speed if they do, but cannot generate Frenzy Charges. Cannot Support "
                "Skills which consume Frenzy Charges.")
    assert conditions._charge_rate(ferocity, "Frenzy", stats) is None
    assert conditions._charge_rate("Gain a Power Charge on kill", "Power", stats)[0] == pytest.approx(5.0)


def test_glowswarm_and_encase_in_jade_guard_are_rows_and_jade_heritage_counts_ten_stacks():
    """MECHC37 (the manager's 17 Nov ruling (b): part-time Guard is a row, never in the floors): Glowswarm's Guard is 100%
    of the Mana flask's recovery a use, up uses x 4 s of the fight, capped at 200% of maximum Life; Jade Heritage counts
    its 10 Jade (10% additional Physical Damage Reduction) and shows Encase in Jade's Guard cycle as a row."""
    from builder.search import recovery

    line = ["Using a Mana Flask grants Guard equal to 100% of Flask's recovery amount for 4 seconds"]
    assert recovery.mana_flask_guard_row(["+10 to maximum Life"], 500.0, 0.1, "", 3000.0) is None
    row = recovery.mana_flask_guard_row(line, 500.0, 0.1, "test", 3000.0)
    assert row["value"].startswith("500 Guard for 4 s") and "up 40%" in row["value"] and "without it" in row["play"]
    capped = recovery.mana_flask_guard_row(line + ["50% increased Guard gained"], 5000.0, 1.0, "", 1000.0)
    assert capped["value"].startswith("2,000 Guard") and "up all fight" in capped["value"]  # (MECHC38: ruling (ii))
    assert "in the defences" in capped["label"]
    guard, up = recovery.mana_flask_guard(line + ["50% increased Guard gained"], 5000.0, 1.0, 1000.0)
    assert (guard, up) == (2000.0, 1.0)
    assert [(m["name"], m["value"]) for m in recovery.full_guard_mods(guard, up)] == [
        ("GuardAbsorbRate", 100), ("GuardAbsorbLimit", 2000)]
    assert recovery.full_guard_mods(500.0, 0.4) == [] and recovery.full_guard_mods(0.0, 1.0) == []
    slower = recovery.mana_flask_guard_row(line, 500.0, 0.1, "", 3000.0, (1.25, "Buffs on you expire slower"))
    assert "for 5 s" in slower["value"] and "up 50%" in slower["value"]
    assert recovery.mana_flask_guard_row(line, 500.0, 0.0, "", 3000.0)["value"].startswith("0:")
    assert recovery.jade_heritage(["+10 to maximum Life"], 3000.0) == (0.0, None)
    counted, jade = recovery.jade_heritage(["Gain a stack of Jade every second"], 3000.0)
    assert counted == 10.0 and "1,800 Guard" in jade["play"] and "29% of the fight" in jade["play"]
    assert "3.2% reduction" in jade["play"]


def test_cov40_drawbacks_scolds_bridle_reflected_poison_black_doubt_and_ailment_chance_on_you():
    """MECHC37 (COV40's routed OVERCOUNTS): Scold's Bridle takes N% of the main skill's Mana cost a second as Physical
    damage, one loss per use; reflected Poison lands PoB's poison damage on your Life; The Black Doubt's DoT-bypass puts
    your own damage over time on Life and stops the boss's Ignite holding Energy Shield Recharge; The Infinite Pursuit's
    increased chance to be afflicted scales the boss's Ignite and Shock chances."""
    from builder.search import boss_hits, objective, recovery

    line = ["Take 50% of Mana Costs you pay for Skills as Physical Damage"]
    assert recovery.cost_damage(["+10 to maximum Life"], {"ManaPerSecondCost": 40.0, "Speed": 2.0}) == (0.0, 0.0, [])
    taken, uses, notes = recovery.cost_damage(line, {"ManaPerSecondCost": 40.0, "Speed": 2.0})
    assert taken == pytest.approx(20.0) and uses == 2.0 and "without it: 0" in notes[0]["basis"]
    assert recovery.cost_damage(line, {"ManaPerSecondCost": 0.0, "Speed": 2.0})[:2] == (0.0, 0.0)
    assert recovery.reflected_poison(["Poison you inflict is Reflected to you"], {"PoisonDPS": 900.0})[0] == 900.0
    assert recovery.reflected_poison(["+10 to maximum Life"], {"PoisonDPS": 900.0}) == (0.0, [])
    stats = {"EnergyShield": 3000.0, "FireBuildDegen": 50.0, "FireEnergyShieldBypass": 0.0}
    assert objective.own_loss(stats) == {"EnergyShield": 50.0, "Life": 0.0}
    assert objective.own_loss({**stats, objective.DOT_BYPASSES_ES: 1}) == {"EnergyShield": 0.0, "Life": 50.0}
    ash = {"_boss": "Arbiter of Ash", "enemySkillTime": 1.0, "Life": 4000.0, "AilmentThreshold": 2000.0,
           "FireTakenHit": 500.0, "LightningTakenHit": 500.0}
    assert [d for d in boss_hits.es_dot(ash) if d[2] == "Ignite"]
    assert boss_hits.es_dot({**ash, objective.DOT_BYPASSES_ES: 1}) == []
    pursuit = "90% increased Chance to be afflicted by Ailments when Hit"
    assert boss_hits.afflicted_inc([pursuit, "+10 to maximum Life"]) == 90.0
    plain = boss_hits.ignite_chance(ash, 0.5)[0]
    more = boss_hits.ignite_chance({**ash, boss_hits.SELF_AFFLICTED_FIELD: 90.0}, 0.5)[0]
    assert more == pytest.approx(min(1.0, plain * 1.9))
    assert boss_hits.ignite_chance({**ash, "_texts": [pursuit]}, 0.5)[0] == pytest.approx(more)
    divinity = {**ash, "_boss": "Arbiter of Divinity"}
    assert boss_hits.own_shock_chance({**divinity, boss_hits.SELF_AFFLICTED_FIELD: 90.0}) == pytest.approx(
        min(1.0, boss_hits.own_shock_chance(divinity) * 1.9))


def test_full_mana_box_is_on_at_the_share_the_build_sits_at_full_mana():
    """MECHC37 (COV40's routed UNDERCOUNT: PoB's FullMana condition set by nothing): the box is on only for a line that
    reads "on Full Mana", at 1 - spend / regeneration of the fight; Idle Hands' "count as on Full Mana while at 90%" lowers
    the line; spending more than it regenerates, never."""
    from builder.search import conditions

    stats = {"Mana": 1000.0, "ManaCost": 20.0, "Speed": 2.0, "ManaRegenRecovery": 100.0, "ManaPerSecondCost": 40.0}
    assert conditions._full_mana(["+10 to maximum Life"], stats) == (0.0, "")
    on, how, share = conditions._full_mana(["25% increased Attack Speed while on Full Mana"], stats)
    assert on and share == pytest.approx(0.6) and "without it" in how
    idle = conditions._full_mana(["25% increased Attack Speed while on Full Mana",
                                  "You count as on Full Mana while at 90% of maximum Mana or above"], stats)
    assert idle[2] == pytest.approx(1.0)
    starved = conditions._full_mana(["10% increased Cast Speed while on Full Mana"], {**stats, "ManaRegenRecovery": 30.0})
    assert starved[2] == 0.0 and not starved[0]


def test_damage_from_blocks_with_a_raised_shield_counts_only_the_raised_blocks():
    """MECHC35 (COV38's sweep): the Ulaman suffix "You take 12% of damage from Blocked Hits with a raised Shield": a
    channelled shield all fight, every Block raised (12%); taps up 10% of the fight against a 50% Block chance, 10 / 55
    of the Blocks raised (2.2%); never raised, 0."""
    from builder.search import recovery

    line = ["You take 12% of damage from Blocked Hits with a raised Shield"]
    assert recovery.raised_block_taken(["+10 to maximum Life"], 1.0, 0.5) == (0.0, "")
    assert recovery.raised_block_taken(line, 1.0, 0.5)[0] == pytest.approx(12.0)
    assert recovery.raised_block_taken(line, 0.1, 0.5)[0] == pytest.approx(12 * 0.1 / 0.55)
    got, how = recovery.raised_block_taken(line, 0.0, 0.5)
    assert got == 0.0 and "never raises" in how


def test_decree_of_loyalty_puts_your_own_bleed_and_poison_on_energy_shield():
    """MECHC35 (COV38's sweep, an UNDERCOUNT): "Damage over Time cannot bypass your Energy Shield": your own Bleed and
    Poison (Life only otherwise) and your damage over time land on Energy Shield; the boss's Bleeds (Olroth's) then hold
    Energy Shield Recharge off too."""
    from builder.search import boss_hits, objective

    stats = {"EnergyShield": 3000.0, "SelfBleedPerSecond": 100.0, "SelfPoisonPerSecond": 50.0,
             "ChaosBuildDegen": 40.0, "ChaosEnergyShieldBypass": 100.0}
    plain = objective.own_loss(stats)
    assert plain["Life"] == pytest.approx(150.0 + 40.0) and plain["EnergyShield"] == 0.0
    held = objective.own_loss({**stats, objective.DOT_HITS_ES: 1})
    assert held["Life"] == 0.0 and held["EnergyShield"] == pytest.approx(190.0)
    assert objective.own_loss({**stats, "EnergyShield": 0.0, objective.DOT_HITS_ES: 1})["EnergyShield"] == 0.0
    olroth = {"_boss": "Olroth, Origin of the Fall", "IgniteAvoidChance": 100}
    assert not [d for d in boss_hits.es_dot(olroth) if d[2].startswith("Bleeding")]
    assert [d for d in boss_hits.es_dot({**olroth, objective.DOT_HITS_ES: 1}) if d[2].startswith("Bleeding")]


def test_stunned_recently_lines_count_at_the_share_of_the_fight_you_were_stunned():
    """MECHC35 (MECHC34's find: PoB's Stunned Recently box never set): the "haven't been Stunned Recently" bonus loses
    the stunned share (it was on all fight), the "have been" lines gain it, Last Stand's Heavy line only the Heavy share,
    "for each time" the expected Stuns in 4 s; a build the boss never Stuns keeps its whole "haven't" bonus."""
    from builder.search import bosses, recovery

    lines = ["20% increased Stun Threshold if you haven't been Stunned Recently",
             "25% increased Attack Damage if you have been Heavy Stunned Recently",
             "100% increased Stun Threshold for each time you've been Stunned Recently",
             "40% faster start of Energy Shield Recharge if you've been Stunned Recently"]
    kinds = [k for _, _, k in recovery.stunned_recently_lines(lines)]
    assert kinds == ["havent", "heavy", "each", "have"]
    stats = {"_boss": bosses.DEFAULT, "enemySkillTime": 1.0, "StunThreshold": 1500.0, "PhysicalTakenHit": 3000.0,
             "FireTakenHit": 3000.0, "ColdTakenHit": 3000.0, "LightningTakenHit": 3000.0, "ChaosTakenHit": 3000.0}
    mods, how = recovery.stunned_recently_mods(lines, stats, 0.0)
    by = {m["line"]: m["scale"] for m in mods}
    threshold = [m["scale"] for m in mods if m["line"] == "1% increased Stun Threshold"]
    assert "1% increased Attack Damage" not in by  # (no Heavy Stuns)
    assert len(threshold) == 2 and -20 < min(threshold) < 0 < max(threshold)  # (the "haven't" share off; "each" on)
    assert 0 < by["1% faster start of Energy Shield Recharge"] <= 40
    heavy, _ = recovery.stunned_recently_mods(lines, stats, 0.5)
    assert {m["line"]: m["scale"] for m in heavy}["1% increased Attack Damage"] == pytest.approx(25 * (1 - math.exp(-2)), abs=0.01)
    calm = {**stats, "StunAvoidChance": 100}
    assert recovery.stunned_recently_mods(lines, calm, 0.0)[0] == []


def test_buffs_on_you_expire_slower_slows_the_buffs_timer_not_their_increased_duration():
    """MECHC35 (RESEARCH29): "Buffs on you expire 10% slower" lines add, and a Buff lasts x 1 / (1 - S), capped at 75%
    slower (x4); "faster" lines subtract; the Archon's duration and Unholy Might's 15 s read it."""
    from builder.search import conditions, models

    assert conditions.buff_expiry(["+10 to maximum Life"]) == (1.0, "")
    two, how = conditions.buff_expiry(["Buffs on you expire 10% slower", "Buffs on you expire 10% slower"])
    assert two == pytest.approx(1 / 0.8) and "PoB's reading" in how
    assert conditions.buff_expiry(["Buffs on you expire 50% slower"] * 2)[0] == pytest.approx(4.0)
    assert conditions.buff_expiry(["Buffs on you expire 10% faster"])[0] == pytest.approx(1 / 1.1)
    plain = models.archon_uptime(0.0, 0.0, 5.0)
    slowed = models.archon_uptime(0.0, 0.0, 5.0, expiry=1.25)
    assert slowed == pytest.approx(12.5 / (12.5 + 20 + 5)) and slowed > plain
    on, _, share = conditions.unholy_might_uptime([], {"_kurgal": (0.02, "x"), "_buff_expiry": (1.25, "y")})
    assert share == pytest.approx(min(1.0, 15 * 1.25 * 0.02))


def test_everlasting_sacrifice_cycles_from_the_builds_energy_shield_refill():
    """MECHC36 (PATCH_CHECK MB-14): "When you reach full Energy Shield, Sacrifice all Energy Shield to gain +5% to all
    Maximum Resistances for 4 seconds". A cycle lasts ES / (recovery - the boss's hits on ES - own loss); the +5% is up
    min(1, 4 s x expiry / cycle); a boss hit meets half the Energy Shield (linear refill); never refilled: none held, no
    buff; with the line, never on Full Energy Shield."""
    from builder.search import conditions, objective
    from builder.search.recovery import everlasting_sacrifice, full_es_share

    stats = {"enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 0, "PhysicalTakenHit": 200,
             "EnergyShield": 1000, "EnergyShieldRegenRecovery": 300.0}
    taken = sum(objective.boss_taken(stats)[0].values())
    up, held, how = everlasting_sacrifice(stats)
    cycle = 1000 / (300.0 - taken)
    assert up == pytest.approx(min(1.0, 4 / cycle)) and held == 0.5 and f"{cycle:.1f} s" in how
    slow = everlasting_sacrifice(stats, conditions.buff_expiry(["Buffs on you expire 20% slower"]))
    assert slow[0] == pytest.approx(min(1.0, 5 / cycle)) and "slower" in slow[2]
    dry = everlasting_sacrifice({**stats, "EnergyShieldRegenRecovery": 0.0})
    assert dry[:2] == (0.0, 0.0) and "never refills" in dry[2]
    assert everlasting_sacrifice({**stats, "EnergyShield": 0})[:2] == (0.0, 1.0)
    assert full_es_share(stats)[0] > 0
    assert full_es_share({**stats, "EverlastingSacrifice": 1}) == (0.0, full_es_share({**stats, "EverlastingSacrifice": 1})[1])
    assert "Everlasting Sacrifice" in full_es_share({**stats, "EverlastingSacrifice": 1})[1]
    assert "EverlastingSacrifice" in objective.RECOVERY_INPUTS


def test_consume_gain_reads_the_main_skills_hits_and_the_consumed_share():
    """MECHC38: conditions.consume_gain keys the main skill's description to its hits a second x the consumed share."""
    from types import SimpleNamespace

    from builder.search import conditions

    text = "Strike. Consume their Shock and grant you a Power Charge."
    build = SimpleNamespace(ctx=SimpleNamespace(all_gems={"Siphoning Strike": {"description": text}}),
                            call=lambda name, fields=None: {"Speed": 2.0, "HitChance": 90.0})
    got = conditions.consume_gain(build, "Siphoning Strike", 0.5, "of its hits Consume a Shock")
    assert got[text][0] == pytest.approx(0.9)
    assert conditions.consume_gain(build, "Siphoning Strike", 0.0, "") == {}
    assert conditions.consume_gain(build, "Unknown", 0.5, "") == {}


def test_instant_flask_recovery_counts_at_every_use_its_charges_allow():
    """MECHC38 (COV40's routed UNDERCOUNT): the instant part of a flask's recovery isn't held to one use per duration:
    "Life Recovery from Flasks is instant" (all of a Life flask's), "Instant Recovery" / "N% of Recovery applied
    Instantly" (the flask's own), not with Reverie's "Recovery from Life Flasks cannot be Instant"."""
    from dataclasses import replace

    from builder.search import flasks

    info = {"lifeTotal": 1000.0, "duration": 4.0, "chargesUsed": 10.0, "chargesMax": 60.0}
    gain = flasks.Charges(flask=10.0)  # 10 charges a second: one use a second, the duration allows one per 4 s
    uses = flasks.drink_charges(info, gain, "Life") / flasks.charges_per_use(info, gain)
    plain = flasks.recovery_per_second(info, gain, "Life")
    assert plain == pytest.approx(1000.0 * min(uses, 0.25))
    instant = flasks.recovery_per_second(info, replace(gain, life_flask_instant=True), "Life")
    assert instant == pytest.approx(1000.0 * uses) and instant > plain
    never = replace(gain, life_flask_instant=True, life_flask_never_instant=True)
    assert flasks.recovery_per_second(info, never, "Life") == pytest.approx(plain)
    part = flasks.recovery_per_second(info, gain, "Life", "Ultimate Life Flask\n25% of Recovery applied Instantly")
    assert part == pytest.approx(1000.0 * (0.25 * uses + 0.75 * min(uses, 0.25)))
    assert flasks.instant_share(gain, "Mana", "Instant Recovery") == 1.0


def test_ngamahus_chosen_fills_rage_on_each_charm_use():
    """MECHC38 (COV40's routed UNDERCOUNT): "Grants up to your maximum Rage on use" gains the maximum Rage at each of the
    charm's uses (conditions' "_item_uses"), and nothing when its uses weren't read."""
    from builder.search import conditions

    line = "Grants up to your maximum Rage on use"
    rate, events, how = conditions._rage_gain(line, {"MaximumRage": 30.0, "_item_uses": {line: (0.1, "Ruby Charm")}},
                                              True)
    assert (rate, events) == (pytest.approx(3.0), pytest.approx(0.1)) and "maximum" in how
    assert conditions._rage_gain(line, {"MaximumRage": 30.0}, True)[0] == 0.0


def test_bulwarks_less_damage_while_rolling_is_a_row_at_the_rolling_share():
    """MECHC38 (COV40's routed UNDERCOUNT): "Take 30% less Damage from Hits while Dodge Rolling" is a row: 30% x the
    rolling share on average, 0 when the build doesn't roll."""
    from builder.search import recovery

    assert recovery.ROLLING_LESS.match("Take 30% less Damage from Hits while Dodge Rolling")
    row = recovery.rolling_less_row(30.0, 0.2)
    assert "6.0% less on average" in row["value"] and "without it" in row["basis"]
    assert recovery.rolling_less_row(30.0, 0.0)["value"].startswith("0:")


def test_a_charms_guard_is_a_row_part_time_and_in_the_floors_all_fight():
    """MECHC38 (COV41's routed UNDERCOUNT): "Also grants N Guard" on a charm: a row at the charm's uptime, in the floors
    (PoB's Guard pool) only up all fight; capped at 200% of maximum Life."""
    from builder.search import recovery

    assert recovery.CHARM_GUARD.search("Rarity: Magic\nRuby Charm\nAlso grants 308 Guard\nUsed when you take Fire damage")
    guard, row = recovery.charm_guard_row("Ruby Charm", 308.0, [], 0.4, 3000.0)
    assert guard == 308.0 and "40% of the fight" in row["value"] and "a row" in row["label"]
    assert recovery.full_guard_mods(guard, 0.4) == []
    guard, row = recovery.charm_guard_row("Ruby Charm", 308.0, ["50% increased Guard gained"], 1.0, 200.0)
    assert guard == 400.0 and "in the defences" in row["label"]


def test_emergent_vigour_adds_energy_shield_from_armour_strength_requirements():
    """MECHC38 (COV41's routed UNDERCOUNT): the rune's two text lines give 50% of the four armour slots' Strength
    requirements as base Energy Shield; one line alone (no "Strength Requirements") is no match."""
    from builder.search import recovery

    lines = ["Gain Maximum Energy Shield equal to 50% of total", "Strength Requirements of Equipped Armour Items"]
    stats = {"StrRequirementsOnHelmet": 100.0, "StrRequirementsOnBody Armour": 150.0, "StrRequirementsOnBoots": 50.0}
    es, row = recovery.emergent_vigour(lines, stats)
    assert es == pytest.approx(150.0) and "+150 maximum Energy Shield" in row["value"]
    assert recovery.emergent_vigour([" ".join(lines)], stats)[0] == pytest.approx(150.0)
    assert recovery.emergent_vigour(lines[:1], stats) == (0.0, None)
    shield = {**stats, recovery.VIGOUR_OFFHAND_FIELD: 100.0}  # (MECHC39, R31-13 (b): a Shield / Focus counts)
    assert recovery.emergent_vigour(lines, shield, off_hand_armour=True)[0] == pytest.approx(200.0)
    assert recovery.emergent_vigour(lines, shield)[0] == pytest.approx(150.0)  # (a weapon there: not armour)


def test_soul_mantle_curses_you_at_the_totem_death_rate():
    """MECHC38 (R31-2): Soul Mantle's level-1 curses, five equally likely, 6 s each, at the totem deaths a second: the
    average in the headline, each in full in the floors' recalculation from 7%; immunity, no deaths: none; the
    per-Curse-on-you line at the expected count; the level-gate reading as a row."""
    import math

    from builder.search import recovery

    line = "Inflicts a random Curse on you when your Totems die, ignoring Curse limit"
    assert recovery.soul_mantle(["+10 to Strength"], 1.0, "") == ([], 0.0, [])
    mods, share, rows = recovery.soul_mantle([line], 0.25, "two totems, 8 s")
    up = 1 - math.exp(-0.25 * 6 / 5)
    assert share == pytest.approx(up) and len(rows) == 2 and "level gate" in rows[1]["label"]
    got = {m["name"]: m["value"] for m in mods}
    assert got["Damage"] == pytest.approx(-20 * up, abs=0.01) and got["ElementalResist"] == pytest.approx(-40 * up, abs=0.01)
    full = {m["name"]: m["value"] for m in recovery.soul_mantle([line], 0.25, "", full=True)[0]}
    assert full["ElementalResist"] == pytest.approx(-40) and full["ActionSpeed"] == pytest.approx(-40)
    halved = {m["name"]: m["value"] for m in recovery.soul_mantle([line, "50% reduced effect of Curses on you"], 0.25, "")[0]}
    assert halved["Damage"] == pytest.approx(-10 * up, abs=0.01)
    assert recovery.soul_mantle([line, "You are Immune to Curses"], 0.25, "")[0] == []
    assert recovery.soul_mantle([line], 0.0, "no totem")[0] == []
    per = recovery.soul_mantle([line, "15% increased Damage per Curse on you"], 0.25, "")[0]
    assert any(m["name"] == "Multiplier:CurseOnSelf" and m["value"] == pytest.approx(5 * up, abs=0.01) for m in per)


def test_divinity_pays_the_costs_from_converted_energy_shield_and_holds_the_main_skill():
    """MECHC39 (R31-3, the manager's ruling): Immaculate Adherence's maximum Divinity = PoB's MaximumEnergyShield x 100%
    x (1 + (increased - 20 x Corrupted items) / 100), 25% of it a second paying the main skill's Mana + Life costs 1:1;
    PoB's costs zeroed (tagged), the main skill held to the share paid; no line, nothing."""
    from builder.search import recovery

    lines = ["Cannot have Energy Shield", "Convert 100% of maximum Energy Shield to maximum Divinity",
             "50% increased maximum Divinity", "20% reduced maximum Divinity per Corrupted Item Equipped",
             "Skills Cost Divinity instead of Mana or Life"]
    mods, rows, pool = recovery.divinity(lines, 2000.0, 1, 100.0, 20.0, "Spark")
    assert pool == pytest.approx(2000 * 1.3)  # (+50% - 20% x 1)
    names = {(m["name"], m["type"]): m["value"] for m in mods}
    assert names[("ManaCost", "MORE")] == -100 and names[("LifeCost", "MORE")] == -100
    assert ("DPS", "MORE") not in names  # (650 a second pays 120)
    assert all(m.get("tag") == recovery.DIVINITY_TAG for m in mods)
    assert "all paid" in rows[0]["value"] and rows[1]["value"].startswith("0")
    held, rows, pool = recovery.divinity(lines, 400.0, 0, 200.0, 0.0, "Spark")  # (600 x 25% = 150 of 200 a second)
    dps = next(m for m in held if m["name"] == "DPS")
    assert dps["value"] == pytest.approx(-25.0) and dps["skill"] == "Spark" and "75% paid" in rows[0]["value"]
    nothing, _, _ = recovery.divinity(lines, 0.0, 0, 50.0, 0.0, "Spark")  # (no Energy Shield: no Divinity, no use)
    assert next(m for m in nothing if m["name"] == "DPS")["value"] == pytest.approx(-100.0)
    assert recovery.divinity(lines[:4], 2000.0, 0, 100.0, 0.0, "Spark") == ([], [], 0.0)
    assert recovery.corrupted_items({"Amulet": "Rarity: UNIQUE\nX\nCorrupted", "Ring 1": "Rarity: RARE\nY",
                                     "Flask 1": "Corrupted", "Jewel 1": "Corrupted", "Weapon 1 Swap": "a\nCorrupted"}) == 1


def test_rage_on_kill_is_the_word_kill_not_kill_inside_skill():
    """MECHC39 (the manager 05:20, SEARCH27's item 4): the Rage resolver tested `"kill" in when`, so a gain whose wording
    holds "skill" read as an on-kill gain (Siphoning Strike's charge had the same bug, fixed by MECHC38 in
    conditions._charge_rate). Now the word: "on kill" counts the build's kills, "use a Skill" doesn't."""
    from builder.search import conditions

    stats = {"Speed": 2.0, "HitChance": 100, "_boss": "Arbiter of Ash", "_kills": (5.0, 5.0, "five a second")}
    assert conditions._rage_gain("Gain 5 Rage on kill", stats, True)[1] == pytest.approx(5.0)
    rage, events, how = conditions._rage_gain("Gain 5 Rage when you use a Skill", stats, True)
    assert "on kill" not in how and events != pytest.approx(5.0)


def test_the_bosss_freeze_counts_its_buildup_and_the_protection_after_it():
    """MECHC39 (R31-8 / R31-8b): a Freeze lasts 2 s on players; each Cold hit adds hit / Freeze Threshold x 2 of buildup,
    kept between hits; after a Freeze the threshold is x6 for 14 s (the headline), "no protection" a row. RESEARCH31's
    worked case: 150% buildup a hit, ~19 Cold hits a minute: a Freeze every ~17 s (about 3.5 a minute) with the
    protection, every ~5 s without. The share Frozen and the Chill multiply into one action-speed INC."""
    from builder.search import boss_hits

    # (MECHC40: equal hits need exactly ceil(n); the count is averaged over a +-25% hit size, so it's smooth)
    assert boss_hits._hits_needed(2.0, 0.0) == 2 and boss_hits._hits_needed(0.2) == 1.0
    assert 1.9 < boss_hits._hits_needed(1.32) <= 2.0  # (2b75a under the protection: 76% a hit, two hits)
    assert boss_hits._hits_needed(3.8667) == pytest.approx(1 + 1 + 1 + (1.25 - 3 / 3.8667) / 0.5 + (1.25 - 4 / 3.8667) / 0.5)
    assert boss_hits._freeze_cycle(0.32, 1.5, 2.0, 1.0, 0.0) == pytest.approx(1 / 0.32)  # (one hit is enough)
    # the manager's 17:55 check, 2b75a: 17.9 Cold hits a minute at 454% a hit, 2 s Freezes, x6 for 14 s: two hits
    # after each Freeze, so a Freeze every 2 + 2 / 0.298 s: Frozen about 23% (the n + half-hit form read 28.7%)
    gap = boss_hits._freeze_cycle(17.9 / 60, 4.54, 2.0, 6.0, 14.0)
    assert 2 / (2 + gap) == pytest.approx(0.230, abs=0.005)
    steps = [boss_hits._freeze_cycle(0.32, b / 1000, 2.0, 6.0, 14.0) for b in range(200, 2000)]
    assert all(a >= b for a, b in zip(steps, steps[1:]))  # (smooth and falling: no jumps as buildup grows)
    assert max((a - b) / a for a, b in zip(steps, steps[1:])) < 0.01  # (no step: ceil jumped by a whole hit)
    stats = {"_boss": "Pinnacle (worst case)", "enemySkillTime": 0.7, "AilmentThreshold": 2500.0, "Life": 5000.0,
             "ColdTakenHit": 1900.0, "FreezeAvoidChance": 0, "SelfFreezeDuration": 100}
    up, bare, row = boss_hits.boss_freeze_on_you(stats, [])
    assert 0 < up < bare < 1 and row["label"] == "The boss Freezes you*"
    assert boss_hits.boss_freeze_on_you({**stats, "FreezeAvoidChance": 100}, [])[0] == 0.0
    sturdy = boss_hits.boss_freeze_on_you(stats, ["100% increased Freeze Threshold"])
    assert sturdy[0] <= up and sturdy[1] < bare  # (with the protection both need one hit after its 14 s)
    assert boss_hits.boss_freeze_on_you(stats, [], frozen_box=True) == (0.0, 0.0, None)
    # PoB's additive form (the headline): -20% and Frozen 10% add -30%; the multiplier is the row's
    inc, rows = boss_hits.speed_from_chill_and_freeze(-20.0, 0.10, 1.2, 0.0)
    assert inc == pytest.approx(-28.0) and "x0.864" in rows[0]["basis"]  # (the Chill only while not Frozen: -18 - 10)


def test_a_charge_bypasses_a_cooldown_only_with_the_charges_wasted_at_the_maximum():
    """MECHC40 (PATCH_CHECK K2-9): "This Skill's cooldown can be bypassed by expending an Endurance Charge". The headline
    spends only the charges that arrive at the maximum (they'd only refresh), each skill up to its use rate above its
    cooldown; the count drops by the charge-time each spend waits for the next gain. Spending every charge is a row."""
    from builder.search import conditions
    from builder.search.conditions import _charges_held

    assert conditions.bypass_kind("Infernal Cry", "... This Skill's cooldown can be bypassed by expending an Endurance "
                                  "Charge.") == "Endurance"
    assert conditions.bypass_kind("Ice-Tipped Arrows", "cooldown can be bypassed by expending a Frenzy Charge") == "Frenzy"
    assert conditions.bypass_kind("Fireball", "Launch a fiery ball") is None
    line = ["Gain an Endurance Charge on Critical Hit"]
    base = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "EnduranceChargesMax": 3, "EnduranceChargesDuration": 15}
    plain = _charges_held("Endurance")(line, dict(base))[0]  # 1 gain/s: at the maximum nearly all fight
    cry = dict(base, _bypass={"Endurance": [("Infernal Cry", 0.75, "0.125/s on its 8.00 s cooldown, at most 0.87/s", 8.0)]})
    held = _charges_held("Endurance")(line, cry)[0]
    e, how, all_in, cooldown = cry["_bypass_uses"]["Infernal Cry"]
    assert 0.7 < e <= 0.75 and all_in == pytest.approx(0.75) and cooldown == 8.0
    assert held == pytest.approx(plain - e, abs=0.06)  # (each spend costs a charge until the next gain, 1 s away)
    slow = dict(cry, CritChance=2.5)  # 0.05 gains/s: rarely at the maximum, so little to spare
    _charges_held("Endurance")(line, slow)
    assert slow["_bypass_uses"]["Infernal Cry"][0] < 0.01
    # beside a consumer: only the net gains above the maximum's upkeep (3 / 15 s = 0.2/s)
    eaten = dict(cry, _consumers={"Endurance": {"Boneshatter": 0.5}})
    _charges_held("Endurance")(line, eaten)
    assert eaten["_bypass_uses"]["Infernal Cry"][0] == pytest.approx(1.0 - 0.5 - 0.2)
    # (R32-7 (1): the bypass's spend counts as consumed, beside Boneshatter's)
    assert eaten["_charge_flow"]["Endurance"][0] == pytest.approx(0.5 + 0.3) and "bypassing" in eaten["_charge_flow"]["Endurance"][1]
    mods, rows, inc = conditions.bypass_mods(eaten["_bypass_uses"], rage_support=True)
    assert inc["Infernal Cry"] == pytest.approx(100 * 0.3 * 8.0) and mods[0]["skill"] == "Infernal Cry"
    assert mods[0]["name"] == "CooldownRecovery" and "NOT counted" in rows[0]["basis"]
    none = conditions.bypass_mods({"Infernal Cry": (0.0, "x", 0.0, 8.0)})
    assert none[0] == [] and rows and none[1][0]["value"].startswith("0:")


def test_shackles_immunity_after_an_ignite_shock_or_freeze_shortens_each():
    """MECHC40 (COV43's routed undercount): Shackles of the Wretched's "You cannot be Ignited / Shocked / Frozen for 6
    seconds after being ..." in the Chill line's form: D / (max(N, D) + 1 / c) for Ignite and Shock; a Freeze no sooner
    than N s after the last one's start. Each row says the share without it."""
    from builder.search import boss_hits

    base = {"enemySkillTime": 1.0, "Life": 4000.0, "AilmentThreshold": 2000.0, "FireTakenHit": 2000.0,
            "LightningTakenHit": 2000.0}
    lines = [f"You cannot be {a} for 6 seconds after being {a}" for a in ("Ignited", "Shocked", "Frozen")]
    assert boss_hits.immune_after(lines, "Shocked") == 6.0 and boss_hits.immune_after(lines[:1], "Shocked") == 0.0
    for boss, key in (("Arbiter of Ash", "ignited"), ("Arbiter of Divinity", "shocked")):
        free = boss_hits.boss_ailments_on_you({**base, "_boss": boss})
        held = boss_hits.boss_ailments_on_you({**base, "_boss": boss}, texts=lines)
        assert 0 < held[key] < free[key] and "after each" in held["notes"][0]["basis"]
    stats = {"_boss": "Pinnacle (worst case)", "enemySkillTime": 0.7, "AilmentThreshold": 2500.0, "Life": 5000.0,
             "ColdTakenHit": 4000.0, "FreezeAvoidChance": 0, "SelfFreezeDuration": 100}
    up, bare, _ = boss_hits.boss_freeze_on_you(stats, [])
    held_up, held_bare, row = boss_hits.boss_freeze_on_you(stats, lines)
    assert held_up <= up and held_bare < bare and "no Freeze for 6 s" in row["basis"]


def test_shared_charges_reach_your_minions_and_nothing_without_one():
    """MECHC40 (COV43's routed item): Lochtonial Caress's "Share Charges with Allies in your Presence": each charge you
    gain your minions gain too (Minions are Allies, KeywordPopups), up to their own 3, refreshing over 15 s; no minion
    skill, no ally against a lone boss: 0, said. Your own charges are unchanged."""
    from builder.search import conditions

    lines = ["Gain a Power Charge on Critical Hit", "Share Charges with Allies in your Presence"]
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "PowerChargesDuration": 15,
             "_has_ally": True}
    own = conditions._charges_held("Power")(lines, dict(stats))[0]
    count, how = conditions.minion_shared("Power")(lines, dict(stats))
    assert 0 < count <= 3 and "Minions are Allies" in how
    assert conditions._charges_held("Power")(lines, dict(stats))[0] == own
    alone = conditions.minion_shared("Power")(lines, {**stats, "_has_ally": False})
    assert alone[0] == 0.0 and "no ally" in alone[1]
    assert conditions.minion_shared("Power")(lines[:1], dict(stats)) == (0.0, "")
    assert conditions.minion_shared("Frenzy")(lines, dict(stats))[0] == 0.0  # (no Frenzy gains to share)


def test_melee_minions_share_charges_only_with_the_boss_inside_your_presence():
    """MECHC41 (R32-7 (3), MEDIUM-LOW): ranged / caster minions stay near you, inside your Presence; melee minions go to
    the boss, inside only while the boss stands within your Presence. The main skill's minions decide; else the share."""
    from builder.search import conditions

    lines = ["Gain a Power Charge on Critical Hit", "Share Charges with Allies in your Presence"]
    base = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "PowerChargesDuration": 15,
            "_has_ally": True, "PresenceRadius": 40}
    every = conditions.minion_shared("Power")(lines, dict(base))[0]
    far = {**base, "_distance_choice": (5.0, "standing back")}
    near = {**base, "_distance_choice": (2.0, "close")}
    zombies = {"_minion_gems": {"Raise Zombie"}, "_main_skill": "Raise Zombie"}
    snipers = {"_minion_gems": {"Skeletal Sniper"}, "_main_skill": "Skeletal Sniper"}
    count, how = conditions.minion_shared("Power")(lines, {**far, **zombies})
    assert count == 0.0 and "outside" in how and f"every minion inside: {every:.1f}" in how
    assert conditions.minion_shared("Power")(lines, {**near, **zombies})[0] == every
    assert conditions.minion_shared("Power")(lines, {**far, **snipers})[0] == every
    # (no minion skill as the main skill: the share of the build's minion skills inside)
    mixed = {"_minion_gems": {"Raise Zombie", "Skeletal Sniper"}, "_main_skill": "Fireball"}
    half = conditions.minion_shared("Power")(lines, {**far, **mixed})[0]
    assert abs(half - round(every / 2, 1)) <= 0.1


def test_the_other_weapon_sets_runes_are_a_row_only():
    """MECHC40 (R31-14, UNVERIFIED): "per Rune Socketed in Equipped Items" counts the active weapon set's Runes (the
    headline); a row gives the other set's Runes (Soul Cores and Idols aren't Runes)."""
    line = ["Gain 1% of Damage as Extra Damage of a random Element per Rune Socketed in Equipped Items"]
    slots = {"Weapon 1 Swap": "Rarity: Rare\nX\nSpear\nRune: Desert Rune\nRune: Soul Core of Tacati\nRune: None",
             "Weapon 2 Swap": "Rarity: Rare\nY\nBuckler\nRune: Thane Myrk's Rune of Summer",
             "Weapon 1": "Rune: Iron Rune"}
    row = recovery.swap_runes_row(line, slots)
    assert row["value"].startswith("+2 Runes") and "Soul Core" not in row["basis"] and row["confidence"] == "UNVERIFIED"
    assert recovery.swap_runes_row(line, {})["value"].startswith("+0")
    assert recovery.swap_runes_row(["Gain 1% of Damage as Extra Damage"], slots) is None


def test_event_guard_counts_size_duration_and_its_events_share():
    """MECHC40 (the manager 17:55: the Guard sources PoB computes nothing for): Guard per event x increased Guard gained,
    capped at 200% of Life, for its duration at the build's own event rate: paced min(1, r x D), random 1 - e^(-r x D);
    part-time a row, up all fight PoB's Guard pool (full_guard_mods)."""
    guard, up, row = recovery.event_guard("Fortifying Cry", 368, 1 / 8, 4.0, [], 3000.0, "on its 8 s cooldown")
    assert guard == 368 and up == pytest.approx(0.5) and "a row" in row["label"] and "184 on average" in row["value"]
    more = recovery.event_guard("Fortifying Cry", 368, 1 / 8, 4.0, ["50% increased Guard gained"], 3000.0, "x")
    assert more[0] == pytest.approx(552)
    capped = recovery.event_guard("x", 10000, 1.0, 4.0, [], 1000.0, "x")
    assert capped[0] == 2000 and capped[1] == 1.0 and "in the defences" in capped[2]["label"]
    assert recovery.full_guard_mods(capped[0], capped[1])
    rand = recovery.event_guard("Savage Hits taken", 300, 0.5, 4.0, [], 3000.0, "x", paced=False)
    assert rand[1] == pytest.approx(1 - math.exp(-2.0))
    none = recovery.event_guard("Vanguard I", 400, 0.0, 4.0, [], 3000.0, "no stuns")
    assert none[1] == 0.0 and none[2]["value"].startswith("0:")
    assert recovery.SAVAGE_GUARD.match("Gain Guard equal to 10% of maximum Life for 4 seconds on taking Savage Hit")
    assert recovery.DRINKER_GUARD.match("Life Flasks applied to you grant Guard for 4 seconds equal to 8% of the Life "
                                        "Recovery per Second they apply")
    assert recovery.WARD_TIMER_GUARD.match("Every 4 seconds, gain Guard equal to 20% of maximum Runic Ward for 2 seconds")


def test_chaos_inoculation_keeps_the_ailment_threshold_of_your_life_before_it():
    """MECHC40: patch 0.5.0 fixed Chaos Inoculation's 1 Life lowering the player's ailment threshold; PoB still reads
    50% of 1: the fix adds 50% of the Life without the keystone, less the 1 PoB counted."""
    from builder.search import boss_hits

    mods, row = boss_hits.ci_ailment_threshold(3001.0)
    assert mods[0]["name"] == "AilmentThreshold" and mods[0]["value"] == pytest.approx(1500.0)
    assert mods[0]["tag"] == boss_hits.CI_THRESHOLD_TAG and "0.5.0" in row["basis"]
    assert boss_hits.ci_ailment_threshold(1.0)[0] == []


def test_duality_olroths_resolve_and_decree_of_flight_grant_guard_at_the_builds_own_rates(monkeypatch):
    """MECHC41 (the bigger Guard sources by impact): Duality's Guard lasts 0.5 s per Combo a use expends, at the Combo
    skill's uses; Olroth's Resolve grants the current Runic Ward as Guard for 10 s at the flask's uses; Decree of Flight
    grants 20% of the missing Energy Shield on each roll every 2 s, only while below full (half missing then), capped at
    200% of maximum Energy Shield. Hollow Shell grants Allies only (you aren't your own Ally): no row."""
    from types import SimpleNamespace

    from builder.search import assumptions, flasks, usage

    duality = "Gain 750 Guard for 0.5 seconds per Combo expended when using Skills"
    olroth = "Gain Guard equal to Current Runic Ward for 10 seconds when Effect ends"
    decree = ["Gain Guard equal to 20% of missing Energy Shield for 4 seconds when you Dodge Roll",
              "Maximum amount of Guard is based on maximum Energy Shield instead"]
    flask = SimpleNamespace(name="Olroth's Resolve", raw=f"Olroth's Resolve\nUltimate Life Flask\n{olroth}")

    def call(name, **kw):
        if name == "stats":
            return {"Ward": 600.0, "EnergyShield": 4000.0, "Life": 1000.0}
        if name == "flaskData":
            return {"results": [{}]}
        raise AssertionError(name)

    build = SimpleNamespace(call=call, equipped={"Flask 1": flask}, groups=lambda: [], ward_sustain={})
    monkeypatch.setattr(assumptions, "usage_rules", lambda b: {"Tempest Bell": ["combo"]})
    monkeypatch.setattr(usage, "requirements", lambda name, rule: [SimpleNamespace(kind="combo", amount=10.0)])
    monkeypatch.setattr(usage, "Usage", lambda b, _: SimpleNamespace(combo_rate=lambda n, a: (0.15, 0.5, "Quarterstaff Strike")))
    monkeypatch.setattr(flasks.Charges, "of", staticmethod(lambda b: None))
    monkeypatch.setattr(flasks, "uses_per_second", lambda info, gain, raw, pool: (0.05, "every 20 s"))
    monkeypatch.setattr(recovery, "full_es_share", lambda stats: (0.4, "x"))
    hook = assumptions.Assumptions.__new__(assumptions.Assumptions)
    hook.build = build
    stats = {"Life": 1000.0, "EnergyShield": 4000.0}  # (MECHC42, R33-6: the Guard cap on ES with Decree's line)
    found = {row["label"].split(" (")[0]: (guard, up, row) for guard, up, row in
             hook.event_guards([duality, olroth, *decree], stats, None, {"Tempest Bell": {}}, None)}
    guard, up, row = found["Guard from Duality"]
    assert guard == 750 and up == pytest.approx(0.15 * 0.5 * 10)  # (5 s a use expending 10 Combo)
    guard, up, row = found["Guard from Olroth's Resolve"]
    assert guard == 600 and up == pytest.approx(0.5) and "full, 600" in row["basis"]
    guard, up, row = found["Guard from Decree of Flight"]
    assert guard == pytest.approx(0.2 * 4000 / 2) and up == pytest.approx(0.6)  # (below full 60%; 2 s rolls x 4 s)
    assert "maximum Energy Shield" in row["basis"]  # (its cap; 2,000 Life would cap at 2,000 too, not reached)
    # (the current Ward with spends: each spend's deficit while it refills; spends outrunning the recovery: empty)
    now, how = recovery.ward_current(600.0, {"Skill": (1.0, 0.4, 100.0), "WardRecoveryPerSecond": 50.0})
    assert now == pytest.approx(600 - 0.4 * 100 ** 2 / 100)
    assert recovery.ward_current(600.0, {"Skill": (1.0, 1.0, 100.0), "WardRecoveryPerSecond": 50.0})[0] == 0.0


def test_chaos_inoculation_raises_pobs_stun_threshold_to_its_value_without_the_keystone():
    """MECHC41 (patch 0.5.0): PoB's Stun Threshold under CI counts only the base Life before it; the fix is a StunThreshold
    MORE of PoB's own threshold without the keystone over it with; none when it isn't lower."""
    from builder.search import boss_hits

    mods, row = boss_hits.ci_stun_threshold(3000.0, 1200.0)
    assert mods[0]["name"] == "StunThreshold" and mods[0]["type"] == "MORE" and mods[0]["value"] == pytest.approx(150.0)
    assert mods[0]["tag"] == boss_hits.CI_STUN_TAG and "1,200 -> 3,000" in row["value"]
    assert boss_hits.ci_stun_threshold(1200.0, 1200.0) == ([], None)
    assert boss_hits.ci_stun_threshold(1000.0, 0.0) == ([], None)


def test_natures_exchange_and_helbryms_composure_grant_guard_at_the_builds_own_rates(monkeypatch):
    """MECHC41: Morrigan's Insight's Nature's Exchange (its level's Guard for 8 s) at the boss's Freezes a second, held to
    its 8 s cooldown, when it supports the main skill (a side skill: 0, said); Helbrym's Composure stores 2 s of the
    boss's damage after mitigation and grants 50% as Guard for 4 s, once per shift into the form, kept e^(-2h)."""
    from types import SimpleNamespace

    from builder.search import assumptions, conditions, models, objective

    groups = [{"main": True, "gems": [{"name": "Ice Strike"}, {"name": "Morrigan's Insight"}]},
              {"gems": [{"name": "Rampage"}, {"name": "Helbrym's Hide"}]}]

    def call(name, **kw):
        if name == "activeSkills":
            return {"skills": [{"name": "Nature's Exchange", "levelStats": {"morrigan_ward_gain_X_guard": 531},
                                "cooldownRecovery": 1.0}]}
        if name == "stats":
            return {"Speed": 2.0}
        raise AssertionError(name)

    build = SimpleNamespace(call=call, equipped={}, groups=lambda: groups)
    monkeypatch.setattr(models, "buildup_rate", lambda stats, ailment: 0.5)
    monkeypatch.setattr(conditions, "Conditions",
                        lambda b, main: SimpleNamespace(_human_shifts=lambda speed, any_form=False: (0.25, "each Rampage")))
    monkeypatch.setattr(objective, "boss_taken", lambda stats: ({"Physical": 300.0, "Fire": 100.0}, 0.5, 1.0))
    hook = assumptions.Assumptions.__new__(assumptions.Assumptions)
    hook.build = build
    found = {row["label"].split(" (")[0]: (g, up, row) for g, up, row in
             hook.event_guards([], {"Life": 3000.0}, "Ice Strike", {}, None)}
    guard, up, row = found["Guard from Nature's Exchange"]
    assert guard == 531 and up == pytest.approx(1.0)  # (held to one every 8 s, lasting 8 s)
    guard, up, row = found["Guard from Helbrym's Composure"]
    assert guard == pytest.approx(0.5 * 400 * 2) and up == pytest.approx(min(1.0, 0.25 * math.exp(-0.5) * 4))
    groups[0]["main"] = False  # (on a side skill: its hits on the Frozen boss aren't rated)
    side = {row["label"].split(" (")[0]: up for g, up, row in hook.event_guards([], {"Life": 3000.0}, "Ice Strike", {}, None)}
    assert side["Guard from Nature's Exchange"] == 0.0


def test_more_charm_charges_gained_scales_each_charms_share_of_generated_charges():
    """MECHC41 (COV44's routed UNDERCOUNT): Intricate Sigils' "20% more Charm Charges gained" (PoB: CharmChargesGained
    MORE, which the bridge's INC sum never read) multiplies the charges a charm generates; granted charges aren't scaled."""
    from dataclasses import replace

    from builder.search import flasks

    assert flasks.CHARM_GAIN_MORE.match("20% more Charm Charges gained")
    base = flasks.Charges(charm=1.0, charm_gain_inc=50.0)
    info = {"kind": "Charm", "gainBase": 0.5}
    plain = flasks.charges_per_second(info, base, None)
    more = flasks.charges_per_second(info, replace(base, charm_gain_more=1.2), None)
    assert plain == pytest.approx(1.5 * 1.5) and more == pytest.approx(plain * 1.2)


def test_deflection_after_none_recently_is_a_row_at_its_share():
    """MECHC41 (MECHC38's rows-only leftover): the Idol's "Prevent +5% of Damage from Deflected Hits if you've Deflected
    no Hits Recently" (PoB splits it over two lines) on the Deflected Hits with none in the 4 s before, e^(-4 d)."""
    texts = ["Prevent +5% of Damage from Deflected Hits if you've", "Deflected no Hits Recently"]
    row = recovery.deflect_rested_row(texts, 1.0, 25.0, 40.0)
    assert f"{math.exp(-1.0):.0%} of your Deflected Hits" in row["value"] and "40% -> 45%" in row["value"]
    assert recovery.deflect_rested_row(texts, 1.0, 0.0, 40.0)["value"].startswith("0:")
    assert recovery.deflect_rested_row(["Deflected no Hits Recently"], 1.0, 25.0, 40.0) is None


def test_citaqualotls_thesis_sacrifices_life_for_guard_on_every_roll():
    """MECHC41 (RESEARCH33 R33-1): "Sacrifice 10% of maximum Life to gain that much Guard when you Dodge Roll": 10% of
    maximum Life as Guard on every roll (one every 2 s), 4 s the headline (2 s / 10 s rows); the Life sacrificed is a
    NEGATIVE recovery field (LifeSacrificed) the sustain floors net off, not damage taken."""
    from types import SimpleNamespace

    from builder.search import assumptions

    line = "Sacrifice 10% of maximum Life to gain that much Guard when you Dodge Roll"
    income = recovery.Income()
    recovery.life_sacrifice_income(income, [line], 3000.0, 0.5)
    assert income.rates["LifeSacrificed"] == pytest.approx(-150.0) and income.pool("Life") == 0.0
    recovery.life_sacrifice_income(blank := recovery.Income(), [], 3000.0, 0.5)
    assert blank.rates["LifeSacrificed"] == 0.0
    hook = assumptions.Assumptions.__new__(assumptions.Assumptions)
    hook.build = SimpleNamespace(call=lambda name, **kw: {}, equipped={}, groups=lambda: [])
    found = [(g, up, row) for g, up, row in hook.event_guards([line], {"Life": 3000.0}, None, {}, None)
             if row["label"].startswith("Guard from Citaqualotl")]
    guard, up, row = found[0]
    assert guard == pytest.approx(300.0) and up == pytest.approx(1.0) and "10 s: up 100%" in row["basis"]


def test_last_stand_counts_while_the_life_flask_is_drunk_the_moment_a_use_fills():
    """MECHC42 (MECH89's never-on rows): "N% increased Attack Damage while you have no Life Flask uses left" (PoB's box
    never set): all fight for a Life Flask whose uses its charges pay for (drunk the moment a use fills), none for one held
    back by the dips below full Life or one that can't be used (Reverie), all fight with no Life Flask (the literal
    reading, UNVERIFIED)."""
    from dataclasses import replace
    from types import SimpleNamespace

    from builder.search import flasks

    line = "25% increased Attack Damage while you have no Life Flask uses left"
    life = {"kind": "Flask", "chargesMax": 60, "chargesUsed": 10, "duration": 4, "lifeTotal": 400}
    flask = SimpleNamespace(name="Ultimate Life Flask", raw="Ultimate Life Flask")
    build = SimpleNamespace(equipped={"Flask 1": flask}, call=lambda name, **kw: {"results": [life]})
    hit = flasks.Charges(flask=1.0, boss_hits=1.0, taken_hit=500.0)  # 0.1 uses/s paid, 1 dip/s
    assert flasks.no_life_uses_share(build, hit)[0] == 1.0
    mods, rows = flasks.no_life_uses_model(build, [line], hit)
    assert mods[0]["line"] == "1% increased Attack Damage" and mods[0]["scale"] == pytest.approx(25.0)
    assert "with it 25%" in rows[0]["play"]
    rare = replace(hit, boss_hits=0.05)  # (dips at 0.05/s under the 0.1 uses/s the charges pay: uses held)
    assert flasks.no_life_uses_share(build, rare)[0] == 0.0
    assert flasks.no_life_uses_model(build, [line], rare)[0] == []
    assert flasks.no_life_uses_share(build, replace(hit, life_flask_unusable=True))[0] == 0.0
    bare = SimpleNamespace(equipped={}, call=lambda name, **kw: {})
    share, how = flasks.no_life_uses_share(bare, hit)
    assert share == 1.0 and "UNVERIFIED" in how
    assert flasks.no_life_uses_model(build, ["25% increased Attack Damage"], hit) == ([], [])


def test_deafening_cries_counts_each_warcry_used_recently_at_the_builds_own_rates():
    """MECHC42 (MECH89's never-on rows): "8% increased Damage for each time you've Warcried Recently" (PoB's count box
    never set): 4 s x the uses a second summed over the build's Warcries, each on its cooldown; none: 0."""
    from types import SimpleNamespace

    from builder.search import assumptions

    line = "8% increased Damage for each time you've Warcried Recently"
    groups = [{"index": 1, "gems": [{"name": "Infernal Cry"}]}, {"index": 2, "gems": [{"name": "Seismic Cry"}]},
              {"index": 3, "gems": [{"name": "Infernal Cry"}]}, {"index": 4, "gems": [{"name": "Fireball"}]}]
    rates = {1: {"index": 1, "cooldown": 8.0}, 2: {"index": 2, "cooldown": 4.0}, 3: {"index": 3, "cooldown": 8.0}}
    gems = {"Infernal Cry": {"skillTypes": ["Warcry"]}, "Seismic Cry": {"skillTypes": ["Warcry"]},
            "Fireball": {"skillTypes": ["Spell"]}}
    build = SimpleNamespace(ctx=SimpleNamespace(all_gems=gems), groups=lambda: groups,
                            call=lambda name, indices=(), **kw: {"rates": [rates[i] for i in indices]})
    count, how, both = assumptions.warcries_used_recently(build)
    assert count == pytest.approx(4 * (1 / 8 + 1 / 4)) and "Infernal Cry" in how  # (the same Warcry twice: once)
    mods, rows = assumptions.warcried_model(build, [line])
    assert mods[0]["line"] == "1% increased Damage" and mods[0]["scale"] == pytest.approx(8 * 1.5)
    none = SimpleNamespace(ctx=SimpleNamespace(all_gems=gems), groups=lambda: groups[3:], call=lambda name, **kw: {})
    assert assumptions.warcries_used_recently(none)[0] == 0.0
    assert assumptions.warcried_model(none, [line])[0] == []


def test_frozen_and_bleeding_lines_count_at_the_shares_the_boss_holds_you_in():
    """MECHC42 (MECH89's never-on rows): "+500 to Armour while Frozen" and "N% increased Armour while Bleeding" are rows
    (part-time: not in the floors); "Regenerate N% of maximum Life per second while Frozen" counts at the Frozen share;
    the Bleeding share is the boss's Bleeds (Olroth's) plus your own items' chance; Venopuncture's "All Damage taken from
    Hits while Bleeding Contributes to Magnitude of Chill on you" Chills you with every type's hits while you Bleed (an
    OVERCOUNT before: unread)."""
    from builder.search import boss_hits

    texts = ["+500 to Armour while Frozen", "50% increased Armour while Bleeding",
             "Regenerate 10% of maximum Life per second while Frozen"]
    mods, rows = recovery.frozen_bleeding_lines(texts, 0.2, (0.5, "half the fight"))
    assert mods[0]["line"] == "Regenerate 1% of maximum Life per second" and mods[0]["scale"] == pytest.approx(2.0)
    assert len(rows) == 3 and "20% of the fight" in rows[0]["value"] and "50% of the fight" in rows[1]["value"]
    assert recovery.frozen_bleeding_lines(texts, 0.0, (0.0, "none"))[0] == []
    assert recovery.frozen_bleeding_lines(["10% increased Armour"], 0.5, (0.5, ""))  == ([], [])
    lone = boss_hits.bleeding_on_you([], {"_boss": "Arbiter of Ash", "Life": 3000.0})
    olroth = boss_hits.bleeding_on_you([], {"_boss": "Olroth, Origin of the Fall", "Life": 3000.0})
    assert lone[0] == 0.0 and 0.0 < olroth[0] < 1.0
    own = boss_hits.bleeding_on_you(["50% chance to be inflicted with Bleeding when Hit"],
                                    {"_boss": "Arbiter of Ash", "Life": 3000.0})
    assert own[0] > 0.0 and "your own items" in own[1]
    seen = {"_boss": "Olroth, Origin of the Fall", "Life": 3000.0, "AilmentThreshold": 100.0}
    vein = "All Damage taken from Hits while Bleeding Contributes to Magnitude of Chill on you"
    plain, _ = boss_hits.boss_chill_on_you(seen, [])
    bled, rows = boss_hits.boss_chill_on_you({**seen, "_bleeding": (0.5, "half")}, [vein])
    assert bled < plain <= 0.0 and "while you're Bleeding 50%" in rows[0]["basis"]
    assert boss_hits.boss_chill_on_you({**seen, "_bleeding": (0.0, "")}, [vein])[0] == pytest.approx(plain)


def test_killed_recently_lines_count_each_on_its_own_at_the_builds_kills():
    """MECHC42 (MECH89's never-on rows; the manager's 10:55 ruling: each line on its own, never PoB's KilledRecently
    box): "N% increased Area of Effect / Attack Speed / Critical Hit Chance if you've Killed Recently" at 1 - e^(-4 k) of
    the fight, k the build's Kills (0 against a lone boss); Arvil's Wheel's "Lose 1% of maximum Mana on Kill" as a
    NEGATIVE Mana field, its Energy Shield twin a row."""
    texts = ["15% increased Area of Effect if you've Killed Recently", "8% increased Attack Speed if you've killed Recently",
             "10% increased Critical Hit Chance if you have Killed Recently", "4% increased Movement Speed if you've Killed Recently"]
    lone = (0.0, 0.0, "a lone boss")
    mods, rows = recovery.killed_recently_lines(texts, lone)
    assert mods == [] and len(rows) == 3 and rows[0]["value"].startswith("0.00%")
    adds = (0.25, 0.25, "adds")
    share = 1 - math.exp(-1.0)
    mods, rows = recovery.killed_recently_lines(texts, adds)
    assert [m["line"] for m in mods] == ["1% increased Area of Effect", "1% increased Attack Speed",
                                         "1% increased Critical Hit Chance"]
    assert mods[0]["scale"] == pytest.approx(15 * share, abs=1e-3) and mods[1]["scale"] == pytest.approx(8 * share, abs=1e-3)
    income = recovery.Income()
    rows = recovery.kill_loss_income(income, ["Lose 1% of maximum Mana on Kill", "Lose 1% of maximum Energy Shield on Kill"],
                                     1000.0, 2000.0, adds)
    assert income.rates["ManaLostOnKill"] == pytest.approx(-2.5) and income.pool("Mana") == 0.0
    assert len(rows) == 2 and "row only" in rows[1]["basis"]
    blank = recovery.Income()
    recovery.kill_loss_income(blank, ["Lose 1% of maximum Mana on Kill"], 1000.0, 0.0, lone)
    assert blank.rates["ManaLostOnKill"] == 0.0


def test_immaterial_is_a_row_at_the_share_a_recharge_started_in_the_past_2_seconds():
    """MECHC42 (MECH89's never-on rows): Immaterial's Evasion after an Energy Shield Recharge start, a row: starts at
    h e^(-h d) a second from the boss's landed hits h and the delay d, on 1 - e^(-2 starts) of the fight; none without ES."""
    line = "50% increased Evasion Rating if Energy Shield Recharge has started in the past 2 seconds"
    row = recovery.recharge_started_row([line], {"EnergyShield": 2000.0, "EnergyShieldRechargeDelay": 4.0,
                                                 "enemySkillTime": 2.0})
    starts = 0.5 * math.exp(-2.0)
    assert f"{1 - math.exp(-2 * starts):.0%} of the fight" in row["value"]
    assert recovery.recharge_started_row([line], {"EnergyShield": 0.0})["value"].endswith("0% of the fight")
    assert recovery.recharge_started_row(["50% increased Evasion Rating"], {}) is None


def test_the_raised_block_rune_is_a_row_at_the_raised_blocks_recently():
    """MECHC42 (MECHC38's rows-only leftover): "+N% to maximum Block chance if you've Blocked with a raised Shield
    Recently" on 1 - e^(-4 r) of the fight, r = the boss's hits x the raised share; it matters only at maximum Block."""
    line = "+5% to maximum Block chance if you've Blocked with a raised Shield Recently"
    row = recovery.raised_max_block_row([line], 0.3, 1.0, 50.0, 50.0)
    assert f"{1 - math.exp(-1.2):.0%} of the fight" in row["value"] and "would raise it" in row["basis"]
    low = recovery.raised_max_block_row([line], 0.3, 1.0, 30.0, 50.0)
    assert "adds nothing now" in low["basis"] and low["play"] == ""
    assert recovery.raised_max_block_row([line], 0.0, 1.0, 50.0, 50.0)["value"].endswith("0% of the fight")
    assert recovery.raised_max_block_row(["+5% to maximum Block chance"], 1.0, 1.0, 50.0, 50.0) is None


def test_research33_answers_on_mechc42s_models():
    """MECHC42 (RESEARCH33 R33-3..R33-7): Last Stand with no Life Flask keeps the literal reading with a "without it" row
    (MEDIUM-LOW); a Warcry Ferocious Roar triggers adds no count of its own (one per press, the "2 per press" reading a
    row); the Guard cap is on maximum Energy Shield with "Maximum amount of Guard is based on maximum Energy Shield
    instead"; Helbrym's Composure counts every change out of the form, animal to animal too."""
    from types import SimpleNamespace

    from builder.search import assumptions, conditions, flasks

    bare = SimpleNamespace(equipped={}, call=lambda name, **kw: {})
    mods, rows = flasks.no_life_uses_model(bare, ["25% increased Attack Damage while you have no Life Flask uses left"])
    assert mods[0]["scale"] == pytest.approx(25.0) and len(rows) == 2
    assert rows[0]["play"].startswith("No Life Flask equipped*") and rows[1]["value"] == "0% increased Attack Damage"
    assert "MEDIUM-LOW" in rows[0]["confidence"]
    groups = [{"index": 1, "gems": [{"name": "Ferocious Roar"}, {"name": "Infernal Cry"}]},
              {"index": 2, "gems": [{"name": "Infernal Cry"}]}]
    gems = {"Ferocious Roar": {"skillTypes": ["Warcry"]}, "Infernal Cry": {"skillTypes": ["Warcry"]}}
    rates = {1: {"index": 1, "cooldown": 8.0, "repeats": 3}, 2: {"index": 2, "cooldown": 8.0}}
    build = SimpleNamespace(ctx=SimpleNamespace(all_gems=gems), groups=lambda: groups,
                            call=lambda name, indices=(), **kw: {"rates": [rates[i] for i in indices]})
    count, how, both = assumptions.warcries_used_recently(build)
    assert count == pytest.approx(4 * 2 / 8) and both == pytest.approx(4 * 3 / 8)  # (repeats ignored; Roar once)
    assert "Ferocious Roar triggering Infernal Cry" in how
    texts = ["Maximum amount of Guard is based on maximum Energy Shield instead"]
    assert recovery.guard_cap_base(texts, 3000.0, 5000.0) == 5000.0 and recovery.guard_cap_base([], 3000.0, 5000.0) == 3000.0
    bear = {"Shapeshift", "Bear"}
    others = {"Wolf skill": ({"Shapeshift", "Wolf"}, 0.25), "Human skill": (set(), 0.25)}
    assert conditions.human_shifts(bear, others, 1.0)[0] == pytest.approx(0.25)
    assert conditions.human_shifts(bear, others, 1.0, any_form=True)[0] == pytest.approx(0.5)

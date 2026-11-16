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
    rate, how = _charge_rate(magma, "Endurance", stats)
    assert 0 < rate < 0.25 and "blocks/s" in how
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
    assert recovery.channel_heavy_stun({**big, "EvadeChance": 100})[0] == 0.0
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

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


def test_life_when_stunned_counts_the_bosss_hits_that_light_stun_you():
    """MECHC15 (AUDIT27 A27-32): "Regenerate N% of maximum Life over 1 second when Stunned" at the boss's landed hits x
    the game's Light Stun chance per damage type (hit / Stun Threshold, Physical 50% more, under 15% none), less the
    build's Stun avoidance; nothing for a build that can't be Stunned."""
    stats = {"StunThreshold": 2000.0, "StunAvoidChance": 0.0, "Life": 4000.0}
    weights = {"Physical": 0.5, "Fire": 0.5}
    sizes = {"Physical": 1000.0, "Fire": 200.0}  # Physical 1000 x 1.5 / 2000 = 75%; Fire 10%: under 15%, none
    rate, how = recovery.stuns_taken(stats, sizes, weights, 2.0)
    assert rate == pytest.approx(2.0 * 0.5 * 0.75) and "Physical 50% more" in how
    half, _ = recovery.stuns_taken({**stats, "StunAvoidChance": 50.0}, sizes, weights, 2.0)
    assert half == pytest.approx(rate / 2)
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

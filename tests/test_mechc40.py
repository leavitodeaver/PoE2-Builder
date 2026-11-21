"""MECHC40's real-PoB tests (their own file, so their own small replay store: test_engine_models.py's grew past what a
4-8 GB place can load, POE2T3748-3751)."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.objective import number
from builder.search.skeletons import enumerate_skeletons


@pytest.fixture(scope="module")
def engine():
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


def test_fortifying_cry_grants_guard_for_a_bosss_power_at_its_use_rate(engine):
    """MECHC40: Fortifying Cry "Grants {0} Guard for every 5 enemy Power in range, counting up to 20 Power" for 4 s:
    a lone Unique boss's 20 Power gives 4 x the gem's value, up its uses x 4 s (on its 8 s cooldown: about half)."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Fortifying Cry 20/0 1")
    present = {s["name"]: s for s in build.call("activeSkills")["skills"] if not s.get("disabled")}
    stats = build.call("stats", fields=["Life", "Speed"])
    found = search.assumptions.event_guards([], stats, "Boneshatter", present, None)
    guard, up, row = found[0]
    print(row)
    assert row["label"].startswith("Guard from Fortifying Cry") and guard == pytest.approx(4 * 92)
    assert 0.3 < up <= 0.55


def test_a_chaos_inoculation_build_keeps_its_ailment_threshold(engine):
    """MECHC40 (patch 0.5.0): 2b75c is Chaos Inoculation; PoB's Ailment Threshold counts 50% of its 1 Life (plus its
    Energy Shield lines). With the assumptions it gains 50% of the Life it would have without the keystone (PoB's own
    what-if without it), x its increases."""
    from builder import ROOT
    from builder.pob import codes
    from builder.search.assumptions import Assumptions
    from builder.search.objective import Objective
    from builder.search.state import Build

    xml = codes.decode((ROOT / "benchmarks" / "reference" / "2b75c.txt").read_text(encoding="utf-8").strip())
    engine.call("loadXml", xml=xml)
    bare = engine.call("stats", fields=["AilmentThreshold", "Life"])
    ci = next(p for p in engine.call("facts", fields=[])["passives"] if p["name"] == "Chaos Inoculation")
    life = engine.call("nodeDeltas", cases=[{"remove": [ci["id"]]}], fields=["Life"], fullDPS=False)["results"][0]["Life"]
    ctx = Context(make_payload(BuildRequest(class_name="Huntress", main_skill="Explosive Spear")))
    build = Build(engine, ctx, Objective(ctx, full=True))
    build.refresh()
    Assumptions(build).apply()
    after = engine.call("stats", fields=["AilmentThreshold"])["AilmentThreshold"]
    print(bare, life, after)
    assert bare["Life"] == 1 and life > 100
    assert after - bare["AilmentThreshold"] > 0.25 * life  # (50% of it, x the build's own increases)
    # (MECHC41: and its Stun Threshold: PoB's own value without the keystone, through a StunThreshold MORE; this also
    # probes that a MORE of that name reaches PoB's threshold, CalcDefence 2767)
    stun = engine.call("stats", fields=["StunThreshold"])["StunThreshold"]
    stun_without = engine.call("nodeDeltas", cases=[{"remove": [ci["id"]]}], fields=["StunThreshold"],
                               fullDPS=False)["results"][0]["StunThreshold"]
    print(bare, stun, stun_without)
    assert stun == pytest.approx(stun_without, rel=0.01)


def test_soul_mantle_curses_you_at_the_shared_totem_death_rate(engine):
    """MECHC41: Soul Mantle's curses come at the shared totem-death rate (assumptions.totem_deaths, MECHB39): a Shockwave
    Totem build's totems over their own duration, re-placed as they expire; the "level gate blocks them" row stays."""
    payload = make_payload(BuildRequest(main_skill="Shockwave Totem", ascendancies=["Warbringer"], level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Shockwave Totem"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    mantle = chr(10).join(["Rarity: UNIQUE", "Soul Mantle", "Sacrificial Mantle", "+75 to Spirit",
                           "+1 to maximum number of Summoned Totems",
                           "Inflicts a random Curse on you when your Totems die, ignoring Curse limit"])
    build.call("equip", slot="Body Armour", raw=mantle, fields=["FullDPS"])
    texts = [e["text"] for e in build.call("sourceLines")["lines"]]
    if not any("random Curse on you when your Totems die" in t for t in texts):
        pytest.skip("PoB didn't equip Soul Mantle in this skeleton")
    skills = build.call("activeSkills")["skills"]
    _, notes, _ = search.assumptions.models(skills)
    row = next(n for n in notes if str(n.get("label", "")).startswith("Soul Mantle: random Curses"))
    print(row)
    assert "totems die a second" in row["basis"] and "Shockwave Totem" in row["basis"]
    assert any(str(n.get("label", "")).startswith("Soul Mantle: if the level gate") for n in notes)


def test_vanguard_grants_its_stun_guard_on_every_stun_and_more_on_heavy_stuns(engine):
    """MECHC44 (an UNDERCOUNT before: only the Heavy Stun stat was counted, alone): Vanguard I's text "Gain 2 Guard per
    ten Strength when you Stun an Enemy / Gain an additional 4 Guard per ten Strength if Heavily Stunning": a Heavy Stun
    grants 6 per 10 Strength, a Light Stun 2, each at the main skill's own stun rates (models.heavy_stun_rate /
    light_stun_rate)."""
    from builder.search import bosses, models

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]]
    build.set_group(index=main["index"], gems=[*old, {"name": "Vanguard I", "level": 1, "quality": 0}])
    present = {s["name"]: s for s in build.call("activeSkills")["skills"] if not s.get("disabled")}
    stats = {**build.call("stats", fields=list(dict.fromkeys(["Life", "Str", *models.MODEL_FIELDS]))),
             **bosses.kill_stats(bosses.DEFAULT)}
    found = [f for f in search.assumptions.event_guards([], stats, "Boneshatter", present, bosses.DEFAULT)
             if f[2]["label"].startswith("Guard from Vanguard I")]
    strength = stats["Str"]
    print(found, strength, models.heavy_stun_rate(stats), models.light_stun_rate(stats, True))
    assert found
    basis = found[0][2]["basis"]
    assert f"{6 * strength / 10:,.0f} a Heavy Stun" in basis and f"{2 * strength / 10:,.0f} a Light Stun" in basis
    assert "Light Stuns" in basis and "ROW, Heavy Stuns alone" in basis


def test_galvanic_fields_on_hit_charges_follow_its_orbs_bolts_not_its_casts(engine):
    """MECHC44 (probe POE2T4808: PoB's Speed 0.80 casts/s, HitSpeed 1.67 bolts/s): Galvanic Field carries
    skill_can_add_multiple_charges_per_action; its on-hit charge lines roll on its Orb's bolts, which come only while an
    Orb is attached (GEM20: after another skill Shocks the boss). A lone skeleton Shocks nothing: 0 hits a cast."""
    payload = make_payload(BuildRequest(main_skill="Galvanic Field", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Galvanic Field"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                         "20% chance to gain a Frenzy Charge on Hit"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    found = search.conditions.evidence()
    bolts, how = search.conditions._galvanic_bolts()
    print(found.get("overrideFrenzyCharges"), bolts, how)
    assert bolts == 0.0 and "attached 0%" in how and "1.67 bolts/s" in how
    assert number((found.get("overrideFrenzyCharges") or (0.0, ""))[0]) == 0.0  # (before: 2.5 held, its casts as hits)


def test_a_side_orb_of_storms_bolts_feed_on_hit_charges(engine):
    """MECHC44: a pressed side Orb of Storms' bolts (MECH-B's side-Orb cycle, kept as build.orb_bolts by
    Assumptions.rate_presses) feed the build's on-hit charge lines, each bolt may grant (Orb of Storms carries the
    multiple-charges flag)."""
    from builder.request.schema import Choices

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], level=90,
                                        require=Choices(skills=["Orb of Storms"])))  # (MECHB29's setup)
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                         "20% chance to gain a Frenzy Charge on Hit"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    orb = next(g["index"] for g in build.groups() if any(x["name"] == "Orb of Storms" for x in g["gems"]))
    build.call("setGroup", index=orb, fullDPS=False, fields=[])
    search.assumptions.rate_presses()
    before = search.conditions.evidence().get("overrideFrenzyCharges")
    assert getattr(build, "orb_bolts", None) is None
    build.call("setGroup", index=orb, fullDPS=True, fields=[])
    search.assumptions.rate_presses()
    bolts = getattr(build, "orb_bolts", None)
    after = search.conditions.evidence().get("overrideFrenzyCharges")
    from types import SimpleNamespace

    from builder.search.assumptions import main_time_share

    share = main_time_share(build, SimpleNamespace(name="Spark"))
    print("PROBE side orb", bolts, share, before, after)
    assert 0 < share < 1  # (MECHC44: the Orb's presses take part of Spark's time; its hits feed the lines at that share)
    assert bolts is not None and bolts[0] > 0
    assert after is not None and "side Orb of Storms" in after[1]


def test_the_main_skill_spends_charges_at_its_share_of_the_fight(engine, monkeypatch):
    """MECHC45 (an OVERCOUNT of its spending before): the main skill as a charge consumer (Lightning Spear spends Frenzy
    Charges) is taken at its share of the fight (Conditions._main_share, as objective.dps scores it), not at PoB's full
    Speed: at half the fight, half the uses a second wanted."""
    import re

    from builder.search.conditions import Conditions

    payload = make_payload(BuildRequest(main_skill="Lightning Spear", ascendancies=["Amazon"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Spear"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                         "20% chance to gain a Frenzy Charge on Hit"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    wanted = {}
    for share in (1.0, 0.5):
        monkeypatch.setattr(Conditions, "_main_share", lambda self, s=share: s)
        search.conditions.evidence()
        flow = (build.charge_flow or {}).get("Frenzy")
        print(share, flow)
        wanted[share] = float(re.search(r"([\d.]+)/s wanted by", flow[1]).group(1))
    assert wanted[1.0] > 0 and wanted[0.5] == pytest.approx(wanted[1.0] / 2, abs=0.006)


def test_the_main_crystal_skill_spends_power_charges_for_creeping_chill_at_its_share(engine, monkeypatch):
    """MECHC46 (MECH93's routed item; an OVERCOUNT of its spending before): Frost Wall (main) with Creeping Chill joins
    the Power Charge consumers at its share of the fight (Conditions._main_share), not at PoB's full Speed: at half the
    fight, half the uses a second wanted."""
    import re

    from builder.search.conditions import Conditions

    payload = make_payload(BuildRequest(main_skill="Frost Wall"))
    skeleton = enumerate_skeletons(Context(payload), ["Frost Wall"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]
           if g["name"] != "Creeping Chill"]
    build.set_group(index=main["index"], gems=[*old, {"name": "Creeping Chill", "level": 1, "quality": 0}])
    amulet = chr(10).join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                           "Gain a Power Charge every 2 seconds"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    wanted = {}
    for share in (1.0, 0.5):
        monkeypatch.setattr(Conditions, "_main_share", lambda self, s=share: s)
        found = search.conditions.evidence()
        flow = (build.charge_flow or {}).get("Power")
        print(share, flow)
        wanted[share] = float(re.search(r"([\d.]+)/s wanted by", flow[1]).group(1))
    assert wanted[1.0] > 0 and wanted[0.5] == pytest.approx(wanted[1.0] / 2, abs=0.006)
    # (MECHC47, R37-3) a Frost Wall cast spends a Power Charge per crystal: its wall's crystals, at least your maximum
    basis = " ".join(str(v[1]) for k, v in found.items() if k in ("overridePowerCharges", "usePowerCharges"))
    assert "per Ice Crystal" in basis and "Frost Wall" in basis


def test_nebulochs_attacks_spend_endurance_charges(engine):
    """MECHC46 (an OVERCOUNT of Endurance Charges held before): with "Attacks consume an Endurance Charge to Critically
    Hit" (Nebuloch's line, here on a rare ring) the main Attack (Boneshatter) spends Endurance Charges: it joins the
    Endurance consumers; without the line it doesn't."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    flows = {}
    for line in ("", "Attacks consume an Endurance Charge to Critically Hit"):
        ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                             "Gain an Endurance Charge every 2 seconds", *([line] if line else [])])
        build.call("equip", slot="Ring 1", raw=ring, fields=[])
        search.conditions.evidence()
        flows[bool(line)] = (build.charge_flow or {}).get("Endurance")
        print(bool(line), flows[bool(line)])
    assert flows[True] is not None and "Boneshatter" in flows[True][1]
    assert flows[False] is None or "Boneshatter" not in flows[False][1]


def test_nebulochs_attacks_critically_hit_when_they_spend_a_charge(engine):
    """MECHC46: with Nebuloch's line the main Attack's crit chance is f x 100 + (1 - f) x PoB's own, f the share of its
    uses finding an Endurance Charge (conditions.NEBULOCH_CRIT's note): above PoB's own with a charge source."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    crit = {}
    for line in ("", "Attacks consume an Endurance Charge to Critically Hit"):
        ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                             "Gain an Endurance Charge every 2 seconds", *([line] if line else [])])
        build.call("equip", slot="Ring 1", raw=ring, fields=[])
        for _ in range(2):
            search.conditions_round()
            search.settle()
        crit[bool(line)] = number(build.call("stats", fields=["PreEffectiveCritChance"]).get("PreEffectiveCritChance"))
        print(bool(line), crit[bool(line)], getattr(build, "nebuloch_found", None))
    found = build.nebuloch_found
    assert found is not None and found[0] > 0
    assert crit[True] > crit[False]
    assert crit[True] == pytest.approx(found[0] * 100 + (1 - found[0]) * crit[False], abs=1.0)
    # (MECHC47, R36-22) the UNVERIFIED row where only hits spend the charge: never below the headline's share
    assert len(found) == 4 and found[0] <= found[2] <= 1.0 and 0.0 < found[3] <= 1.0
    if found[3] < 1.0 and found[0] < 1.0:
        assert found[2] > found[0]


def test_hateforge_gains_a_random_charge_on_reaching_maximum_rage(engine):
    """MECHC47 (an UNDERCOUNT before, COV48's MECH-C row): Hateforge's "Gain a random Charge on reaching Maximum Rage, no
    more than once every N seconds" beside its "Lose all Rage on reaching Maximum Rage" gives a charge each climb to the
    maximum (at most one per N s), a third to each kind (conditions.RAGE_MAX_CHARGE's note)."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    found = {}
    for line in ("", "Gain a random Charge on reaching Maximum Rage, no more than once every 4 seconds"):
        ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                             "Gain 5 Rage on Hit", "Lose all Rage on reaching Maximum Rage", *([line] if line else [])])
        build.call("equip", slot="Ring 1", raw=ring, fields=[])
        found[bool(line)] = search.conditions.evidence()
        print(bool(line), found[bool(line)].get("multiplierRage"), found[bool(line)].get("overridePowerCharges"),
              found[bool(line)].get("overrideFrenzyCharges"))
    for key in ("overridePowerCharges", "overrideFrenzyCharges", "overrideEnduranceCharges"):
        got = found[True].get(key)
        assert got is not None and got[0] > 0 and "one random Charge every" in got[1]
        before = found[False].get(key)
        assert before is None or "random Charge" not in before[1]


def test_druidic_prowess_from_rage_spent_raises_skill_speed(engine):
    """MECHC47 (an UNDERCOUNT before, COV48's MECH-C rows 17.4 / 8.8): "Gain 1 Druidic Prowess for every 20 total Rage
    spent" with a Rage-spending main skill (Furious Slam, 10 Rage a use) gives stacks of 10% increased Skill Speed at the
    Rage it really spends (conditions.DRUIDIC_PROWESS's note): the main skill's Speed rises with the line."""
    payload = make_payload(BuildRequest(main_skill="Furious Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Furious Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    speed = {}
    for line in ("", "Gain 1 Druidic Prowess for every 20 total Rage spent"):
        ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                             "Gain 10 Rage on Hit", *([line] if line else [])])
        build.call("equip", slot="Ring 1", raw=ring, fields=[])
        for _ in range(2):
            search.conditions_round()
            search.settle()
        speed[bool(line)] = number(build.call("stats", fields=["Speed"]).get("Speed"))
        print(bool(line), speed[bool(line)], getattr(build, "rage_spent", None))
    spent = build.rage_spent
    assert spent is not None and spent[0] > 0 and "Furious Slam" in spent[1]
    assert speed[True] > speed[False]


def test_a_side_twisters_hits_a_cast_feed_on_hit_charges(engine):
    """MECHC47 (MECHC46's labelled UNDERCOUNT): a flagged Twister in another group grants on-Hit charges on each of its
    twisters' hits a cast (recovery.twister_hits' headline from its own group's ProjectileCount and Duration), not one a
    cast (conditions.SIDE_TWISTER's note)."""
    from builder.request.schema import Choices

    payload = make_payload(BuildRequest(main_skill="Lightning Spear", require=Choices(skills=["Twister"])))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Spear"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                         "20% chance to gain a Frenzy Charge on Hit"])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    twister = next((g["index"] for g in build.groups() if any(x["name"] == "Twister" for x in g["gems"])), None)
    if twister is None:
        pytest.skip("the skeleton has no Twister group")
    build.call("setGroup", index=twister, fullDPS=True, fields=[])
    search.assumptions.rate_presses()
    found = search.conditions.evidence()
    # (Lightning Spear spends Frenzy Charges, so none may be held: the basis is on the count or on the use box, probe
    # POE2T4852: "x 1.27 hits a cast from its 1 twister(s)")
    basis = " ".join(str(v[1]) for k, v in found.items() if k in ("overrideFrenzyCharges", "useFrenzyCharges"))
    print("PROBE side twister", basis)
    assert "hits a cast from its" in basis


def test_the_hammer_of_faiths_shrines_raise_crit_and_skill_speed(engine):
    """MECHC47 (an UNDERCOUNT before, COV48's MECH-C row 16.3; RESEARCH26 R26 (a)): "Every 10 seconds, gain a random
    non-damaging Shrine buff for 20 seconds" keeps Diamond and Acceleration up 17.9% of the fight (R37-10's weights): Diamond's crits blend into the
    crit chance and Acceleration's 60% Skill Speed counts at that share (conditions.SHRINE_EVERY's note)."""
    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    seen = {}
    for line in ("", "Every 10 seconds, gain a random non-damaging Shrine buff for 20 seconds"):
        ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                             "+10% to all Elemental Resistances", *([line] if line else [])])
        build.call("equip", slot="Ring 1", raw=ring, fields=[])
        for _ in range(2):
            search.conditions_round()
            search.settle()
        got = build.call("stats", fields=["PreEffectiveCritChance", "Speed"])
        seen[bool(line)] = (number(got.get("PreEffectiveCritChance")), number(got.get("Speed")))
        print(bool(line), seen[bool(line)])
    up = 1 - (1 - 30 / 320) ** 2  # (R37-10: the Shrines table's weights)
    assert seen[True][0] == pytest.approx(up * 100 + (1 - up) * seen[False][0], abs=1.0)
    assert seen[True][1] > seen[False][1]


def test_natures_archon_gives_a_plant_main_skill_more_damage_at_its_uptime(engine):
    """MECHC47 (an UNDERCOUNT before: only Accelerated Growth read the Archon's share): with "N% chance to gain Nature's
    Archon when your Plants Overgrow" and an Overgrow source, a Plant main skill (Vine Arrow) gets 25% more Damage x the
    Archon's uptime (assumptions.natures_archon_effects); without an Overgrow source, nothing."""
    from builder.search import assumptions

    payload = make_payload(BuildRequest(main_skill="Vine Arrow"))
    skeleton = enumerate_skeletons(Context(payload), ["Vine Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    got = {}
    for extra in ([], ["Plants have a 50% chance to immediately Overgrow"]):
        ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0",
                             "50% chance to gain Nature's Archon when your Plants Overgrow", *extra])
        build.call("equip", slot="Ring 1", raw=ring, fields=[])
        from builder.search import models

        stats = build.call("stats", fields=["Speed"])
        texts = [line["text"] for line in build.call("sourceLines")["lines"]]
        skills = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
        main = next(s for s in skills if s.name == "Vine Arrow")
        mods, notes = assumptions.natures_archon_effects(build, skills, texts, stats, main, {})
        got[bool(extra)] = (mods, notes)
        print(bool(extra), mods, [n["value"] for n in notes], [n["basis"][:200] for n in notes])
    assert not got[False][0] and got[False][1]
    mods = got[True][0]
    assert mods and mods[0]["name"] == "Damage" and 0 < mods[0]["value"] < 25


def test_a_side_galvanic_fields_orb_bolts_feed_on_hit_charges(engine):
    """MECHC48 (MECHC47's labelled UNDERCOUNT): a Galvanic Field in another group was left out of the side hits (its
    casts aren't hits). Its Orb's bolts are: the Orb set's HitSpeed (setRates, MECHC48's field) x the Orb's attached
    share after the main skill's Shocks (conditions.SIDE_GALVANIC's note); flagged, so each bolt may grant."""
    from builder.request.schema import Choices
    from builder.search import conditions

    payload = make_payload(BuildRequest(main_skill="Spark", require=Choices(skills=["Galvanic Field"])))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    galvanic = next((g["index"] for g in build.groups() if any(x["name"] == "Galvanic Field" for x in g["gems"])), None)
    if galvanic is None:
        pytest.skip("the skeleton has no Galvanic Field group")
    sets = build.call("setRates", index=galvanic).get("sets") or []
    orb = next((x for x in sets if x.get("label") == "Orb"), {})
    print("PROBE galvanic sets", sets)
    assert number(orb.get("HitSpeed")) > 0  # (the bridge now gives each set's HitSpeed)
    rows = conditions.side_hit_rows({galvanic: {"name": "Galvanic Field", "speed": 1.0}}, build.groups(),
                                    {"Galvanic Field"}, None)
    assert rows and rows[0]["galvanic"] and rows[0]["hits"] == 0.0  # (its casts aren't hits)
    cond = search.conditions
    cond._side_galvanic(rows, {}, {"Speed": 2.0, "HitChance": 100, "ShockChancePerHit": 50})
    print("PROBE side galvanic", rows[0])
    assert rows[0]["hits"] > 0 and "Galvanic Field's Orb" in rows[0]["how"]
    none = conditions.side_hit_rows({galvanic: {"name": "Galvanic Field", "speed": 1.0}}, build.groups(),
                                    {"Galvanic Field"}, None)
    cond._side_galvanic(none, {}, {"Speed": 2.0, "HitChance": 100, "ShockChancePerHit": 0})
    assert none[0]["hits"] == 0.0 and "no Orb" in none[0]["how"]  # (nothing Shocks the boss: no Orb)


def test_fear_on_culling_mods_reach_pob(engine):
    """MECHC48 (a probe, LANE_RULES "PoB already does X"): the Fear mods conditions.fear_mods makes reach PoB: Fear
    Overwhelming's AreaOfEffect INC for Attacks raises an Attack's AreaOfEffectMod (Boneshatter's), and Fear Incarnate's
    CullPercent INC is read as a Culling Strike Threshold increase."""
    from builder.search import conditions

    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    lines = ["Gain 1 Fear Incarnate when you Cull a target", "Gain 1 Fear Overwhelming when you Cull a target"]
    mods, _ = conditions.fear_mods(lines, 0.1, "adds", True, "test")
    base = list(search.assumptions.mods or [])
    before = build.call("setAssumptions", mods=base, fields=["AreaOfEffectMod"])["stats"]
    after = build.call("setAssumptions", mods=base + mods, fields=["AreaOfEffectMod"])["stats"]
    print("PROBE fear", mods, before, after)
    area = next(m["value"] for m in mods if m["name"] == "AreaOfEffect")
    assert number(after["AreaOfEffectMod"]) > number(before["AreaOfEffectMod"])
    assert number(after["AreaOfEffectMod"]) == pytest.approx(number(before["AreaOfEffectMod"]) + area / 100, abs=0.01)
    parsed = engine.call("parseLines", lines=["20% increased Culling Strike Threshold"])["results"]
    print("PROBE cull parse", parsed)
    assert "CullPercent" in str(parsed)

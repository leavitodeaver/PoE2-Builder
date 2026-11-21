"""MECHC50: Blazing Critical at its buff's uptime (plans/gs-blazing-critical-duration.md)."""
import math

import pytest

from builder.search import assumptions as a
from builder.search import models


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


def test_blazing_uptime_is_a_poisson_streams():
    assert a.blazing_uptime(0.0) == 0.0
    assert a.blazing_uptime(0.2) == pytest.approx(1 - math.exp(-1.0))
    assert a.blazing_uptime(2.0) > 0.99


def test_blazing_critical_counts_its_gain_at_the_buffs_uptime_not_pobs_box(engine):
    """(MECHC50, an OVERCOUNT at PoB's cutoff, an UNDERCOUNT below it) PoB gives Blazing Critical's whole Fire gain to
    Attacks while its 'Crit recently' box is on, none while off. The model takes PoB's gain back under the box (probe:
    the box on with the take-back reads as the box off) and adds gain x (1 - e^(-crits x 5 s))."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]]
    build.set_group(index=main["index"], gems=[*old, {"name": a.BLAZING_CRITICAL, "level": 1, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])

    def hit(box: bool, mods: list) -> float:
        build.set_config({"conditionCritRecently": box})
        build.call("setAssumptions", mods=mods, fields=[])
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"]

    take_back = models.raw_mod("DamageGainAsFire", "BASE", -15, condition="CritRecently", tag=a.BLAZING_TAG)
    off, on, on_taken = hit(False, []), hit(True, []), hit(True, [take_back])
    print("PROBE blazing", off, on, on_taken)
    assert on > off * 1.05 and on_taken == pytest.approx(off)

    build.set_config({"conditionCritRecently": True})
    build.call("setAssumptions", mods=[], fields=[])
    skills = build.call("activeSkills")["skills"]
    found = [models.Skill.of(s) for s in skills if not s["disabled"]]
    skill = next(s for s in found if s.group == main["index"] and s.name == "Boneshatter")
    stats = build.call("stats", fields=models.MODEL_FIELDS)
    mods, notes = search.assumptions.blazing_critical(skills, skill, stats)
    crits = models.hits_per_second(stats) * stats["CritChance"] / 100
    scale = build.call("stats", fields=["DurationMod"])["DurationMod"] or 1.0  # (MECHC51, R43-9: its own duration)
    up = a.blazing_uptime(crits, a.BLAZING_SECONDS * scale)
    print("PROBE blazing model", mods, notes[0]["value"], notes[0]["basis"])
    assert mods[0]["value"] == -15 and mods[0]["condition"] == "CritRecently"
    if up >= 0.05:
        assert 0 < mods[1]["value"] <= 15 * up + 0.01  # (at the main skill's share of the fight)
    assert "Blazing Critical" in notes[0]["label"] and "PoB's 'Crit recently' box (on" in notes[0]["basis"]
    assert "PoB's fixed 4 s window" in notes[0]["basis"]


def test_atziris_impatience_drains_each_pool_at_its_supported_skills_uses(engine):
    """(MECHC50, an OVERCOUNT: the support looked free) Atziri's Impatience on Infernal Cry: 25% of maximum Life, Mana and
    Energy Shield lost a use, as each pool's DegenPercent at the cry's uses a second; PoB's Life regeneration net falls
    by that share of Life (probe)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": "Infernal Cry", "level": 20, "quality": 0},
                          {"name": a.IMPATIENCE, "level": 1, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])
    skills = build.call("activeSkills")["skills"]
    found = [models.Skill.of(s) for s in skills if not s["disabled"]]
    main = build.main_group()
    skill = next(s for s in found if s.group == main["index"] and s.name == "Boneshatter")
    stats = build.call("stats", fields=models.MODEL_FIELDS)
    mods, notes = search.assumptions.impatience_loss(skills, skill, stats)
    print("PROBE impatience", mods, notes[0]["value"], notes[0]["basis"])
    assert "Infernal Cry" in notes[0]["basis"]
    if mods:  # (the cry used: one loss a use)
        assert {m["name"] for m in mods} == {"LifeDegenPercent", "ManaDegenPercent", "EnergyShieldDegenPercent"}
        before = build.call("stats", fields=["LifeRegenRecovery", "Life"])
        build.call("setAssumptions", mods=[mods[0]], fields=[])
        after = build.call("stats", fields=["LifeRegenRecovery"])
        assert after["LifeRegenRecovery"] == pytest.approx(
            before["LifeRegenRecovery"] - before["Life"] * mods[0]["value"] / 100, rel=0.02, abs=0.5)


def test_toxic_domains_bloom_raises_skill_costs_at_the_share_inside(engine):
    """(MECHC50, an OVERCOUNT of sustain) Toxic Domain's 'increased Skill costs' in the Bloom (25%) as PoB's Cost INC at
    the share of the fight a Bloom stands; it raises the main skill's Mana cost a second in PoB (probe)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert search.assumptions.toxic_domain_cost(None) == ([], [])
    build.set_group(gems=[{"name": a.TOXIC_DOMAIN, "level": 20, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])
    mods, notes = search.assumptions.toxic_domain_cost(None)
    print("PROBE toxic", mods, notes)
    assert "Toxic Domain" in notes[0]["label"]
    if mods:
        before = build.call("stats", fields=["ManaPerSecondCost"])["ManaPerSecondCost"]
        build.call("setAssumptions", mods=mods, fields=[])
        after = build.call("stats", fields=["ManaPerSecondCost"])["ManaPerSecondCost"]
        assert before == 0 or after > before


def test_selfless_remnants_takes_its_skills_remnants_away_from_you():
    from builder.search import conditions

    groups = [{"enabled": True, "gems": [{"name": "Snap"}, {"name": conditions.SELFLESS_REMNANTS}]},
              {"enabled": True, "gems": [{"name": "Frost Bomb"}]},
              {"enabled": False, "gems": [{"name": "Orb of Storms"}, {"name": conditions.SELFLESS_REMNANTS}]}]
    assert conditions.selfless_skills(groups) == {"Snap"}


def test_pobs_cooldown_carries_second_winds_slower_recovery(engine):
    """(MECHC50; RESEARCH42 R42-16, the probe LANE_RULES asks for) Second Wind II's 'base_cooldown_speed_+%_final' -40
    reaches PoB's cooldown (SkillStatMap 597: CooldownRecovery MORE), so Assumptions.second_wind_life's one use per
    PoB cooldown is the sustained rate (uses recover one at a time), not an overcount."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    cry = build.set_group(gems=[{"name": "Infernal Cry", "level": 20, "quality": 0}])
    plain = build.call("useRates", indices=[cry["index"]])["rates"][0]
    build.set_group(index=cry["index"], gems=[{"name": "Infernal Cry", "level": 20, "quality": 0},
                                              {"name": "Second Wind II", "level": 1, "quality": 0}])
    wind = build.call("useRates", indices=[cry["index"]])["rates"][0]
    print("PROBE second wind", plain.get("cooldown"), wind.get("cooldown"))
    assert wind["cooldown"] == pytest.approx(plain["cooldown"] / 0.6, rel=0.02)


def test_war_banners_attack_speed_pob_never_applies_comes_at_the_banner_share(engine):
    """(MECHC50, an UNDERCOUNT) probe: with the banner planted PoB's Attack Speed doesn't move (its statMap has the damage
    and Accuracy, not base_skill_buff_banner_attack_speed_+%_to_apply); the model adds 25% x the banner share."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("setAssumptions", mods=[], fields=[])
    build.set_config({"conditionBannerPlanted": True})
    before = build.call("stats", fields=["Speed", "TotalDPS"])
    build.set_group(gems=[{"name": a.WAR_BANNER, "level": 20, "quality": 0}])
    after = build.call("stats", fields=["Speed", "TotalDPS"])
    print("PROBE war banner", before, after)
    # (POE2T4929: TotalDPS didn't move either: the banner isn't active in this bare build (its 200 Glory usage
    # condition), so this shows only that adding it moves no Speed; the statMap reading is the evidence)
    assert after["Speed"] == pytest.approx(before["Speed"])
    search.assumptions.apply()
    note = next(n for n in search.assumptions.active if n["label"].startswith("War Banner: its aura"))
    print("PROBE war banner note", note)
    assert "of the fight under a placed banner" in note["value"]


def test_thaumaturgical_dynamism_splits_a_charge_every_5_s_by_the_skill_gems_requirements():
    """(MECHC50; R39-18, an UNDERCOUNT) a charge every 5 s, its type in proportion to the socketed skill gems' Str / Dex /
    Int requirement totals (supports not counted; a row with them)."""
    from builder.search import conditions

    groups = [{"enabled": True, "gems": [{"name": "A"}, {"name": "S", "support": True}]},
              {"enabled": True, "gems": [{"name": "B"}]}, {"enabled": False, "gems": [{"name": "C"}]}]
    gems = {"A": {"reqStr": 30, "reqInt": 10}, "B": {"reqDex": 40}, "S": {"reqInt": 100}, "C": {"reqInt": 500}}
    reqs = conditions.gem_attribute_reqs(groups, gems)
    assert reqs["skills"] == {"Str": 30, "Dex": 40, "Int": 10} and reqs["all"]["Int"] == 110
    stats = {"_gem_attr_reqs": reqs}
    line = "Grants Thaumaturgical Dynamism"
    assert conditions._charge_rate(line, "Endurance", stats)[0] == pytest.approx(30 / 80 / 5)
    assert conditions._charge_rate(line, "Frenzy", stats)[0] == pytest.approx(40 / 80 / 5)
    rate, how = conditions._charge_rate(line, "Power", stats)
    assert rate == pytest.approx(10 / 80 / 5) and "with them" in how
    assert conditions._charge_rate(line, "Power", {})[0] == 0.0


def test_probe_charged_staff_stacks_follow_pobs_held_power_charges(engine):
    """(MECHC50, the probe plans/gs-charged-staff-stacks.md asks for before any model) PoB's Charged Staff buff adds its
    Lightning per Power Charge held (RemovablePowerCharge), whatever the build's gain rate: the main Quarterstaff
    attack's damage with 1 and 3 Power Charges held, and what the resolver sets (printed for the model)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Falling Thunder", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": "Charged Staff", "level": 20, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])
    seen = {}
    for held in (0, 1, 3):
        build.set_config({"usePowerCharges": held > 0, "overridePowerCharges": held})
        seen[held] = build.call("stats", fields=["TotalDPS", "RemovablePowerCharges", "PowerChargesMax"])
    found = search.conditions.evidence()
    print("PROBE charged staff", seen, "resolver:", found.get("usePowerCharges"), found.get("overridePowerCharges"))
    assert seen[3]["TotalDPS"] >= seen[1]["TotalDPS"] >= seen[0]["TotalDPS"]


def test_light_stuns_on_you_cost_their_half_second_each():
    """(MECHC49's held model, landed by MECHC50 per the manager's call; R40-4) the share lost = Light Stuns a second (over
    the boss's own USES, 2/3 of its dodgeable hits dodged, red-flash avoided; the worst case at the worst real preset) x
    PoB's StunDuration (0.5 s without the field); "Cannot be Light Stunned" or no hit: 0, no row; the defence cadence is
    the upper row."""
    from builder.search import bosses, recovery

    base = {"enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 0, "PhysicalTakenHit": 1500,
            "ColdTakenHit": 1500, "FireTakenHit": 1500, "LightningTakenHit": 1500, "ChaosTakenHit": 1500,
            "StunThreshold": 1000, "StunAvoidChance": 0, "StunDuration": 0.528}
    named = {**base, "_boss": bosses.REAL[0]}
    lost, rows = recovery.light_stun_lost(named)
    rate = recovery._light_stuns(named)[0]
    upper = recovery._light_stuns(named, "defence")[0]
    assert 0 < rate < upper and lost == pytest.approx(min(rate * 0.528, 0.9))
    assert rows and "defence cadence" in rows[0]["value"] and "without Light Stuns" in rows[0]["basis"]
    assert recovery.light_stun_lost({**named, "StunDuration": 0})[0] == pytest.approx(min(rate * 0.5, 0.9))
    assert recovery.light_stun_lost({**named, "StunAvoidChance": 100}) == (0.0, [])
    worst = recovery.light_stun_lost(base)
    assert worst[0] > 0 and "worst real preset" in worst[1][0]["basis"]


def test_the_bosss_time_cuts_reach_pob_as_less_speed_and_switch_off(engine, monkeypatch):
    """(MECHC50, item 3) red-flash moves and Light Stuns as Speed MORE (tags RedFlashMoves / LightStunned) with their
    notes; assumptions.BOSS_TIME_CUTS False leaves them out (the two fixed-speed tests' setup)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.assumptions.apply()
    tags = {m.get("tag") for m in search.assumptions.mods}
    labels = {n["label"] for n in search.assumptions.active}
    print("PROBE time cuts", [m for m in search.assumptions.mods if m.get("tag") in ("RedFlashMoves", "LightStunned")])
    assert "RedFlashMoves" in tags and "Time spent moving out of the boss's red-flash attacks*" in labels
    speed = search.build.call("stats", fields=["Speed"])["Speed"]
    monkeypatch.setattr(a, "BOSS_TIME_CUTS", False)
    search.assumptions.apply()
    assert not {m.get("tag") for m in search.assumptions.mods} & {"RedFlashMoves", "LightStunned"}
    assert search.build.call("stats", fields=["Speed"])["Speed"] > speed

"""MECHC51's models (MECH-C): Charged Staff's buff at the Power Charges it spends."""
import pytest

from builder.search import assumptions as a
from builder.search import conditions, models


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


def test_consume_all_split_takes_what_the_other_consumers_leave_up_to_its_presses():
    """(MECHC51, CONSUMES_ALL's note) Charged Staff spends every Power Charge the others leave, at most its presses x
    the maximum held; no press rate: none."""
    split = conditions.consume_all_split
    assert split({"Other": 0.5}, "Power", 1.0, 3) == {}
    got = split({"Charged Staff": 0.3, "Spark": 0.3}, "Power", 1.0, 3)
    assert got["Charged Staff"][0] == pytest.approx(0.7) and got["Charged Staff"][1] == 0.3
    assert split({"Charged Staff": 0.1}, "Power", 1.0, 3)["Charged Staff"][0] == pytest.approx(0.3)  # (0.1 x 3)
    assert split({"Charged Staff": 0.0}, "Power", 1.0, 3)["Charged Staff"][0] == 0.0
    assert split({"Charged Staff": 1.0, "Spark": 2.0}, "Power", 1.0, 3)["Charged Staff"][0] == 0.0
    assert split({"Charged Staff": 1.0}, "Frenzy", 1.0, 3) == {}


def test_charged_staff_held_count_falls_to_an_instant_when_the_staff_spends_everything():
    """(MECHC51) the charge model: beside a staff spending what's left, a Power Charge is held for an instant only."""
    held = conditions._charges_held("Power")
    lines = ["Gain a Power Charge every 2 seconds"]
    stats = {"PowerChargesMax": 3, "PowerChargesDuration": 15, "_consumers": {"Power": {"Charged Staff": 0.5}}}
    count, basis = held(lines, stats)
    assert count <= 0.1 and "0.50/s made" in basis
    assert stats["_consume_all"]["Power"]["Charged Staff"][0] == pytest.approx(0.5)
    assert stats["_charge_flow"]["Power"][0] == pytest.approx(0.5)


def test_charged_staff_stacks_are_its_spent_charges_x_6_s_not_pobs_held_count(engine):
    """(MECHC51; R41-6) stacks S = the Power Charges the staff spends a second x 6 s, PoB's held count taken back. Probe:
    S = 3 with none held reads as PoB's 3 held (MECHC50's 179.7 vs 121.8 at 0); S = 0 with 3 held reads as 0 held."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Falling Thunder", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": a.CHARGED_STAFF, "level": 20, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])
    main = build.main_group()

    def dps(held: int, spent: float | None) -> tuple[float, list, list]:
        build.set_config({"usePowerCharges": held > 0, "overridePowerCharges": held})
        build.call("setAssumptions", mods=[], fields=[])
        mods, notes = [], []
        if spent is not None:
            build.consume_all = {"Power": {a.CHARGED_STAFF: (spent, 0.2, "stand-in")}}
            skills = build.call("activeSkills")["skills"]
            found = [models.Skill.of(s) for s in skills if not s["disabled"]]
            skill = next(s for s in found if s.group == main["index"])
            mods, notes = search.assumptions.charged_staff(skills, skill)
            build.call("setAssumptions", mods=mods, fields=[])
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"], mods, notes

    none, three = dps(0, None)[0], dps(3, None)[0]
    modelled, mods, notes = dps(0, 0.5)
    print("PROBE charged staff model", none, three, modelled, mods, notes[0]["value"])
    assert three > none * 1.1 and modelled == pytest.approx(three, rel=0.01)
    assert "3.0 stacks" in notes[0]["value"] and "6 s" in notes[0]["basis"]
    taken, mods, _ = dps(3, 0.0)
    assert taken == pytest.approx(none, rel=0.01) and all(m["tag"] == a.CHARGED_STAFF_TAG for m in mods)


def test_blazing_critical_window_scales_with_the_supported_skills_duration(engine):
    """(MECHC51; R43-9) the window is 5 s x the supported skill's Skill Effect Duration: 50% increased Skill Effect
    Duration on the main skill lengthens it to 7.5 s, so the uptime rises (PoB's 4 s a lower row)."""
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

    def window(extra: list) -> tuple[float, float]:
        build.call("setAssumptions", mods=extra, fields=[])
        skills = build.call("activeSkills")["skills"]
        found = [models.Skill.of(s) for s in skills if not s["disabled"]]
        skill = next(s for s in found if s.group == main["index"] and s.name == "Boneshatter")
        stats = build.call("stats", fields=[*models.MODEL_FIELDS, "DurationMod"])
        notes = search.assumptions.blazing_critical(skills, skill, stats)[1]
        return stats["DurationMod"], float(notes[0]["basis"].split("= ")[1].split("%")[0])

    plain = window([])
    longer = window([models.raw_mod("Duration", "INC", 50, tag="Probe")])
    print("PROBE blazing window", plain, longer)
    assert longer[0] == pytest.approx(plain[0] + 0.5, abs=0.01)
    assert longer[1] >= plain[1]


def test_charges_reaching_splits_the_flow_by_what_each_consumer_asks():
    """(MECHC51, for MECH-B's Raise Zombie) a consumer's share of the charges consumed a second, by what it asks."""
    class B:
        charge_flow = {"Power": (0.6, "0.60/s made")}
        charge_wanted = {"Power": {"Raise Zombie": 0.5, "Spark": 1.0}}

    rate, how = conditions.charges_reaching(B, "Power", "Raise Zombie")
    assert rate == pytest.approx(0.2) and "0.50/s it asks" in how
    assert conditions.charges_reaching(B, "Power", "Other")[0] == 0.0
    assert conditions.charges_reaching(B, "Frenzy", "Raise Zombie")[0] == 0.0
    B.charge_flow = {"Power": (3.0, "")}
    assert conditions.charges_reaching(B, "Power", "Raise Zombie")[0] == pytest.approx(0.5)


def test_channelling_share_is_the_main_channels_share_plus_side_presses_held(monkeypatch):
    """(MECHC51, the manager's 12:05 ruling) a channelled main skill at its share of the fight; a side channel at its
    presses x its payoff hold (MECHC52, the user's 23:45 ruling: Incinerate held to its maximum stages, 4 x 0.5 s);
    nothing channelled: 0."""
    from types import SimpleNamespace

    gems = {"Flameblast": {"skillTypes": ["Spell", "Channel"]}, "Spark": {"skillTypes": ["Spell"]},
            "Incinerate": {"skillTypes": ["Spell", "Channel"]}, "Sup": {"support": True, "skillTypes": ["Channel"]}}

    class B:
        ctx = SimpleNamespace(gems=gems, all_gems=gems)

        def call(self, method, **kw):
            if method == "groups":
                return {"mainSocketGroup": 1, "groups": [
                    {"index": 1, "gems": [{"name": self.main}, {"name": "Sup"}]},
                    {"index": 2, "gems": [{"name": "Incinerate"}]}, {"index": 3, "gems": [{"name": "Spark"}]}]}
            if method == "activeSkills":
                return {"skills": [{"name": "Incinerate", "levelStats": {"incinerate_maximum_stages": 4,
                                                                          "incinerate_gain_stage_every_x_ms": 500}}]}
            assert method == "useRates" and kw["indices"] == [2]
            return {"rates": [{"index": 2, "speed": 5.0}]}

    monkeypatch.setattr(models, "press_choices", lambda build: {})
    monkeypatch.setattr(conditions, "skill_use_rate", lambda row, choices: (0.25, "chosen", False))
    b = B()
    b.main = "Flameblast"
    share, how = conditions.channelling_share(b, "Flameblast", 0.3)
    assert share == pytest.approx(0.3 + 0.25 * 2.0) and "4 stages" in how  # (at most 1)
    b.main = "Spark"
    assert conditions.channelling_share(b, "Spark", 0.9)[0] == pytest.approx(0.5)
    line = "Gain Arcane Surge while Channelling"  # (MECH-C's own "while Channelling" reader takes the share)
    assert conditions._arcane_surge_source(line, [line], {"_channelling_share": 0.4})[0] == pytest.approx(0.4)


def test_fist_of_kalguur_boosts_the_slams_the_leftover_ward_pays_for(engine):
    """(MECHC51; MECHB51's routed item, an UNDERCOUNT) the main Slam gains 20% as Physical x the share of its uses the
    Runic Ward left after the Ward-cost skills pays 150% of its cost for; no Ward left: none."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Sunder", ascendancies=["Warbringer"]))
    skeleton = enumerate_skeletons(Context(payload), ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]]
    build.set_group(index=main["index"], gems=[*old, {"name": a.FIST_OF_KALGUUR, "level": 1, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])
    skills = build.call("activeSkills")["skills"]
    pools = build.call("stats", fields=["ManaCost", "Speed", "TotalDPS"])
    need = pools["Speed"] * pools["ManaCost"] * 1.5
    build.ward_sustain = {"WardRecoveryPerSecond": need / 2 + 3.0, "Other": (1.0, 1.0, 3.0), "_share": 1.0}
    mods, notes = search.assumptions.kalguur_boost(skills, "Sunder")
    print("PROBE kalguur", pools, mods, notes[0]["value"], notes[0]["basis"])
    assert mods and mods[0]["name"] == "DamageGainAsPhysical"
    assert mods[0]["value"] == pytest.approx(10.0, rel=0.05)  # (half the uses boosted: 20% x 1/2)
    build.call("setAssumptions", mods=mods, fields=[])
    assert build.call("stats", fields=["TotalDPS"])["TotalDPS"] > pools["TotalDPS"] * 1.03
    build.ward_sustain = {"WardRecoveryPerSecond": 3.0, "Other": (1.0, 1.0, 3.0), "_share": 1.0}
    assert search.assumptions.kalguur_boost(skills, "Sunder")[0] == []


def test_pyromantic_pacts_reset_hits_you_for_life_and_es_every_cycle():
    """(MECHC51; un-pyromantic-pact (b), an OVERCOUNT of survival) one Fire hit of maximum Life + ES every 2 x Mana /
    ManaPerSecondCost s; no cost: none."""
    from builder.search import recovery

    line = "Take maximum Life and Energy Shield as Fire Damage when Infernal Flame reaches maximum"
    stats = {"Mana": 500, "ManaPerSecondCost": 50, "Life": 3000, "EnergyShield": 1000}
    taken, hits, notes = recovery.pact_self_hit([line], stats)
    assert hits == pytest.approx(1 / 20) and taken == pytest.approx(4000 / 20) and "every 20.0 s" in notes[0]["value"]
    assert recovery.pact_self_hit([line], {**stats, "ManaPerSecondCost": 0})[:2] == (0.0, 0.0)
    assert recovery.pact_self_hit(["Other"], stats) == (0.0, 0.0, [])
    assert recovery.PACT_NO_MANA.search("Gain Infernal Flame instead of spending Mana for Skill costs")


def test_mote_steady_state_is_a_birth_death_chains_mean():
    """(MECHC51; R42-4) no hits: full; gain = hits: the middle; no gain: none."""
    assert a.mote_steady_state(1.0, 0.0, 9) == 9.0
    assert a.mote_steady_state(1.0, 1.0, 9) == pytest.approx(4.5)
    assert a.mote_steady_state(0.0, 1.0, 9) == 0.0
    assert 8 < a.mote_steady_state(1 / 1.525, 0.2, 9) < 9


def test_virtuous_barrier_holds_its_motes_at_the_steady_state_not_pobs_full_count(engine):
    """(MECHC51; R42-4, an OVERCOUNT) PoB's Mote Multipliers are taken back to the steady state under the boss's landed
    hits: maximum Life with the model's mods falls below PoB's, and taking every Mote back reads as no Strength Mote."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": a.VIRTUOUS_BARRIER, "level": 20, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])
    skills = build.call("activeSkills")["skills"]
    if not any(s["name"] == a.VIRTUOUS_BARRIER and not s["disabled"] for s in skills):
        pytest.skip("PoB doesn't activate Virtuous Barrier from a socket group here")
    life = build.call("stats", fields=["Life"])["Life"]
    mods, notes = search.assumptions.virtuous_barrier(skills)
    print("PROBE virtuous barrier", life, mods, notes[0]["value"], notes[0]["basis"])
    assert "Motes held on average" in notes[0]["value"]
    build.call("setAssumptions", mods=mods, fields=[])
    held = build.call("stats", fields=["Life"])["Life"]
    assert held < life if mods else held == life


def test_leyline_cycle_ends_when_the_ward_runs_out_and_waits_8_s():
    """(MECHC51; R42-17) on for the refilled Ward / the drain, off 8 s (or until full), at most the user's 75%."""
    cycle = conditions.leyline_cycle
    assert cycle(1000, 0, 0)[0] == 0.75
    assert cycle(0, 50, 0)[0] == 0.0 and "R44-6" in cycle(0, 50, 0)[1] and "75%" in cycle(0, 50, 0)[1]
    share, how = cycle(400, 100, 0)  # (5% regen: 20/s, 160 back in 8 s = 40%: on 1.6 s / off 8; or full 4 s / off 20)
    assert share == pytest.approx(max(1.6 / 9.6, 4 / 24)) and "R42-17" in how
    assert cycle(10000, 10, 0)[0] == 0.75


def test_ghost_dance_shrouds_come_faster_with_cooldown_recovery():
    """(MECHC51; GEM31's route, an UNDERCOUNT) the Shroud interval over the skill's Cooldown Recovery Rate."""
    from builder.search import recovery

    level = {recovery.GHOST_INTERVAL_STAT: 10000, recovery.GHOST_REGEN_STAT: 120}
    plain, faster = recovery.Income(), recovery.Income()
    recovery.ghost_dance_income(plain, level, 1000, 1.0, "hits")
    recovery.ghost_dance_income(faster, level, 1000, 1.0, "hits", 2.0)
    assert faster.rates["EnergyShieldGhostDanceRegen"] == pytest.approx(2 * plain.rates["EnergyShieldGhostDanceRegen"])
    assert "every 5.0 s" in faster.parts[0]


def test_time_of_need_blessings_recover_life_at_their_interval():
    """(MECHC51; GEM31's route, an UNDERCOUNT) 628 Life every 8.1 s at level 20, faster with Cooldown Recovery."""
    from builder.search import recovery

    level = {recovery.GHOST_INTERVAL_STAT: 8100, recovery.TIME_OF_NEED_LIFE: 628}
    income = recovery.Income()
    recovery.time_of_need_income(income, level, 1.5)
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(628 / (8.1 / 1.5))
    empty = recovery.Income()
    recovery.time_of_need_income(empty, {}, 1.0)
    assert empty.rates["LifeOnEventRecovery"] == 0.0


def test_charged_staff_stacks_scale_with_its_skill_effect_duration_and_pob_does_not(engine):
    """(MECHC51; R44-5) +50% Skill Effect Duration: PoB's DPS at 3 Power Charges held doesn't move (its stacks follow the
    held count only), and the model's stacks go x1.5 (0.5 charges/s x 6 s x 1.5 = 4.5)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Falling Thunder", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": a.CHARGED_STAFF, "level": 20, "quality": 0}])
    longer = [models.raw_mod("Duration", "INC", 50, tag="Probe")]
    build.set_config({"usePowerCharges": True, "overridePowerCharges": 3})
    build.call("setAssumptions", mods=[], fields=[])
    plain = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    build.call("setAssumptions", mods=longer, fields=[])
    assert build.call("stats", fields=["TotalDPS"])["TotalDPS"] == pytest.approx(plain)
    build.set_config({"usePowerCharges": False, "overridePowerCharges": 0})
    build.call("setAssumptions", mods=longer, fields=[])
    build.consume_all = {"Power": {a.CHARGED_STAFF: (0.5, 0.2, "stand-in")}}
    skills = build.call("activeSkills")["skills"]
    found = [models.Skill.of(s) for s in skills if not s["disabled"]]
    main = build.main_group()
    skill = next(s for s in found if s.group == main["index"])
    mods, notes = search.assumptions.charged_staff(skills, skill)
    print("PROBE charged staff duration", plain, mods, notes[0]["value"], notes[0]["basis"])
    assert "4.5 stacks" in notes[0]["value"] and "flat 6 s (LOW): 3.0" in notes[0]["basis"]


def test_surge_rate_is_the_supply_or_the_uses_whichever_is_lower():
    """(MECHC51, for MECH-B's Double Vision) batches over the cap waste the excess; a not-consume chance stretches the
    supply; no gain or no qualifying use: 0."""
    rate = conditions.surge_rate
    assert rate(0.5, 4, 3.0)[0] == pytest.approx(2.0)
    assert rate(0.5, 4, 1.0)[0] == pytest.approx(1.0)
    assert rate(0.5, 8, 10.0)[0] == pytest.approx(3.0)  # (cap 6)
    assert rate(0.5, 4, 10.0, 0.5)[0] == pytest.approx(4.0)
    assert rate(0, 4, 1)[0] == 0.0 and rate(1, 4, 0)[0] == 0.0


def test_virtuous_barrier_draws_on_full_types_are_wasted_in_the_headline():
    """(MECHC51; R44-8) per-type chains hold fewer Motes than the pooled one (the row)."""
    per = sum(a.mote_steady_state(1 / 1.5 / 3, 0.5 * m / 11, m) for m in (5, 3, 3))
    assert per < a.mote_steady_state(1 / 1.5, 0.5, 11)


def test_main_share_readers_take_the_main_skill_hit_cut():
    """(MECHC51, the manager 18:15 / 18:28) main_time_share / with_main_share and Conditions._main_share give the share
    x build.main_hit_cut (clamped to [0, 1]); hits=False (Conditions.evidence, time readers) leaves it out; a cut mod on
    another skill isn't the main skill's."""
    from types import SimpleNamespace

    class Objective:
        press_scales = {"Main": 0.8}

        def busy(self):
            return 0.0

    build = SimpleNamespace(objective=Objective(), main_hit_cut=0.5)
    main = SimpleNamespace(name="Main")
    assert a.main_time_share(build, main) == pytest.approx(0.4)
    assert a.main_time_share(build, main, hits=False) == pytest.approx(0.8)
    assert a.with_main_share(build, {}, main)["_main_share"] == pytest.approx(0.4)
    build.main_hit_cut = 3.0
    assert a.main_time_share(build, main) == pytest.approx(0.8)
    build.main_hit_cut = None
    # (MECHC52: MECH-C's cuts go through main's assumptions.hit_cut, MECHB52's one helper)
    a.hit_cut(build, [models.raw_mod("DPS", "MORE", -50, "Main"), models.raw_mod("DPS", "MORE", -90, "Side")], "Main")
    assert build.main_hit_cut == pytest.approx(0.5)
    build.main_hit_cut = None
    a.hit_cut(build, [models.raw_mod("DPS", "MORE", -90, "Side")], "Main")
    assert build.main_hit_cut is None
    conds = conditions.Conditions.__new__(conditions.Conditions)
    conds.build, conds.main_skill = SimpleNamespace(objective=Objective(), main_hit_cut=0.5), "Main"
    assert conds._main_share() == pytest.approx(0.4) and conds._main_share(hits=False) == pytest.approx(0.8)


def test_probe_temper_weapons_channel_cut_reaches_main_hit_cut(engine):
    """(MECHC51, the manager 18:15) Temper Weapon's channel time off the main attack (its "DPS" MORE on the main skill)
    now also sets build.main_hit_cut to the same factor, so the ailment and Glory readers count the hits that happen."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Temper Weapon 20/0 1")
    search.settle()
    mods, notes = search.assumptions.wanted()
    temper = [m for m in mods if m.get("tag") == "TemperWeapon" and m.get("name") == "DPS"]
    print("PROBE temper cut", temper, build.main_hit_cut, [n.get("value") for n in notes if "Temper" in str(n.get("label"))])
    if not temper:
        pytest.skip("Temper Weapon took no channel time on this build")
    assert build.main_hit_cut == pytest.approx(1 + temper[0]["value"] / 100, rel=1e-3)


def test_abyssal_apparitions_hit_part_raises_the_main_hit_cut_and_the_readers_clamp_it():
    """MECHC52 (the manager 18:15, MIXED): Abyssal Apparition's copied casts ADD hits: its "hit_cut" key (1 + the share
    of casts copied), not its whole DPS factor (the copies' 60% less is damage per hit), goes on build.main_hit_cut through
    main's assumptions.hit_cut; a raise multiplies into an earlier cut, and the readers clamp the product at 1."""
    from types import SimpleNamespace
    from builder.search.models import Skill

    line = "Damaging Spells consume a Power Charge if able to trigger Abyssal Apparition"
    spark, types, stats = Skill(name="Spark", spell=True), {"Spell", "Damage", "Projectile"}, {"Speed": 2.0}
    mods, _ = models.abyssal_apparition_model([line], spark, types, stats, 1.0, "")  # half the casts copied
    assert mods[0]["hit_cut"] == pytest.approx(1.5) and mods[0]["value"] == pytest.approx(20.0)
    build = SimpleNamespace(main_hit_cut=None)
    a.hit_cut(build, mods, "Spark")
    assert build.main_hit_cut == pytest.approx(1.5)  # the hit part, not the x1.2 damage factor
    assert a.hit_cut_share(build, 0.8) == pytest.approx(0.8)  # alone: clamped, PoB's rate kept
    build.main_hit_cut = 0.5  # an earlier cut (a channel's time off the main skill)
    a.hit_cut(build, mods, "Spark")
    assert build.main_hit_cut == pytest.approx(0.75) and a.hit_cut_share(build, 0.8) == pytest.approx(0.6)


def test_pinnacle_of_power_spends_the_maximum_a_press_beside_the_other_consumers():
    """MECHC52 (CONSUMES_ALL): Pinnacle of Power spends what the others leave, at most its cooldown presses x the
    maximum; beside Charged Staff the staff (listed first) takes first."""
    split = conditions.consume_all_split
    got = split({"Pinnacle of Power": 0.25, "Spark": 0.2}, "Power", 1.0, 3)
    assert got["Pinnacle of Power"][0] == pytest.approx(0.75)  # (0.25 presses x 3; 0.8 left)
    assert split({"Pinnacle of Power": 0.25}, "Power", 0.2, 3)["Pinnacle of Power"][0] == pytest.approx(0.2)
    both = split({"Charged Staff": 1.0, "Pinnacle of Power": 0.25}, "Power", 1.0, 3)
    assert both["Charged Staff"][0] == pytest.approx(1.0) and both["Pinnacle of Power"][0] == 0.0


def test_probe_pinnacle_of_power_buff_at_its_uptime_not_pobs_held_count(engine):
    """MECHC52 (PINNACLE_OF_POWER's note): PoB holds the buff all fight at the HELD count; the model gives 15% x the
    maximum more Elemental Damage at uptime min(1, spent x 5 s x DurationMod) and takes PoB's held buff back. Probe on a
    Lightning attack at 3 Power Charges held (Falling Thunder's own per-charge bonus in every reading): with Pinnacle's
    group and no spend it reads as no Pinnacle; at full uptime as no Pinnacle + 45% more Elemental Damage."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Falling Thunder", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    most = models.number(build.call("stats", fields=["PowerChargesMax"])["PowerChargesMax"])
    build.set_config({"usePowerCharges": True, "overridePowerCharges": int(most)})

    def total(mods: list) -> float:
        build.call("setAssumptions", mods=mods, fields=[])
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"]

    plain = total([])
    elemental = total([models.raw_mod("ElementalDamage", "MORE", 15 * most, skill="Falling Thunder", tag="Probe")])
    build.set_group(gems=[{"name": a.PINNACLE_OF_POWER, "level": 20, "quality": 0}])
    pob = total([])

    def modelled(spent: float) -> tuple[float, list, list]:
        build.call("setAssumptions", mods=[], fields=[])
        build.consume_all = {"Power": {a.PINNACLE_OF_POWER: (spent, 0.25, "stand-in")}}
        skills = build.call("activeSkills")["skills"]
        found = [models.Skill.of(s) for s in skills if not s["disabled"]]
        skill = next(s for s in found if s.group == main["index"])
        mods, notes = search.assumptions.pinnacle_of_power(skills, skill)
        return total(mods), mods, notes

    taken, mods, _ = modelled(0.0)
    full, _, notes = modelled(1.0)
    part, part_mods, _ = modelled(1 / 15)
    print("PROBE pinnacle", most, plain, elemental, pob, taken, full, part, mods, part_mods, notes[0]["basis"])
    assert most >= 1 and pob > plain * 1.2  # (PoB's held-count buff)
    assert taken == pytest.approx(plain, rel=0.01) and all(m["tag"] == a.PINNACLE_TAG for m in mods)
    assert full == pytest.approx(elemental, rel=0.01) and "100% of the fight" in notes[0]["value"]
    assert plain < part < full and 0 < part_mods[-1]["value"] < 15 * most


def test_probe_wind_serpents_fury_thrusts_at_its_frenzy_supply_and_a_serpent_per_charge(engine):
    """MECHC52 (WIND_SERPENT's note): PoB scores one set at the full attack rate; the game needs a Frenzy Charge a use and
    makes a Serpent per charge consumed. Probe: PoB's two sets (Serpent at its own 199%-at-20 multiplier, below the
    Thrust); charges spent = half PoB's uses: half the thrusts, one Serpent each; spent = twice its uses: every thrust,
    two Serpents each; none spent: no use."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=a.WIND_SERPENT, ascendancies=["Ritualist"]))
    skeleton = enumerate_skeletons(Context(payload), [a.WIND_SERPENT])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("setAssumptions", mods=[], fields=[])
    group = build.main_group()
    sets = {x["label"]: x["TotalDPS"] for x in
            build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]}
    speed = models.number(build.call("stats", fields=["Speed"])["Speed"])
    skills = build.call("activeSkills")["skills"]
    main = next(models.Skill.of(s) for s in skills if not s["disabled"] and s["group"] == group["index"]
                and s["name"] == a.WIND_SERPENT)
    print("PROBE wind serpent sets", sets, speed, main.stat_set)
    assert sets["Thrust"] > sets["Serpent"] > 0
    scored = sets[main.stat_set]

    def factor(spent: float) -> tuple[float, dict, dict]:
        build.consume_all = {"Frenzy": {a.WIND_SERPENT: (spent, speed, "stand-in")}}
        mods, notes = search.assumptions.wind_serpent(main)
        dps = next(m for m in mods if m["name"] == "DPS")  # (MECHC53: a Damage MORE first where PoB scores the Serpent)
        product = 1.0
        for m in mods:
            product *= 1 + m["value"] / 100
        return product, dps, notes[0]

    half, mod, note = factor(speed / 2)
    assert half == pytest.approx((0.5 * sets["Thrust"] + 0.5 * sets["Serpent"]) / scored, rel=0.01)
    assert mod["hit_cut"] == pytest.approx(0.5) and mod[models.HIT_CUT_PER_USE] == pytest.approx(2.0)
    assert "1.0 a thrust" in note["basis"]  # (MECHC52: MECH99's split, half the uses, two hits each)
    double, mod, _ = factor(2 * speed)
    assert double == pytest.approx((sets["Thrust"] + 2 * sets["Serpent"]) / scored, rel=0.01)
    assert mod["hit_cut"] == pytest.approx(1.0) and mod[models.HIT_CUT_PER_USE] == pytest.approx(3.0)
    none, mod, note = factor(0.0)
    assert none == pytest.approx(0.0, abs=1e-6) and mod["hit_cut"] == 0 and mod[models.HIT_CUT_PER_USE] == 0
    assert "can't be used" in note["play"]


def test_probe_wind_serpents_fury_ailments_are_the_thrusts_whichever_set_pob_scores(engine):
    """MECHC53 (GEM33's mixed-model split; WIND_SERPENT's note): PoB builds Bleed from the scored set's hit and holds one
    Bleed, the strongest. Wind Serpent's note hands its set shares to GEM22's strongest_ailment_model. Probe with a 100%
    Bleed chance stand-in and a charge a use: scored on the Serpent set (POE2T4999: PoB's default here), PoB's Bleed is a
    Serpent's, far below the Thrust-scored one; with both models the two scorings read the same Bleed and the same hits."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=a.WIND_SERPENT, ascendancies=["Ritualist"]))
    skeleton = enumerate_skeletons(Context(payload), [a.WIND_SERPENT])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    bleed = [models.raw_mod("BleedChance", "BASE", 100, skill=a.WIND_SERPENT, tag="Probe")]
    group = build.main_group()
    active = group.get("mainActiveSkill") or 1
    labels = [x["label"] for x in build.call("setRates", index=group["index"], active=active)["sets"]]
    speed = models.number(build.call("stats", fields=["Speed"])["Speed"])

    def scored_on(label: str) -> dict:
        build.call("setGroup", index=group["index"], statSet=labels.index(label) + 1, fields=[])
        build.call("setAssumptions", mods=bleed, fields=[])
        skills = build.call("activeSkills")["skills"]
        main = next(models.Skill.of(s) for s in skills if not s["disabled"] and s["group"] == group["index"]
                    and s["name"] == a.WIND_SERPENT)
        before = build.call("stats", fields=["TotalDPS", "BleedDPS"])
        build.consume_all = {"Frenzy": {a.WIND_SERPENT: (speed, speed, "stand-in")}}  # (a charge a use)
        mods, notes = search.assumptions.wind_serpent(main)
        build.call("setAssumptions", mods=bleed + mods, fields=[])
        sets = build.call("setRates", index=group["index"], active=active)["sets"]
        shares = notes[0].get("set_shares")
        strong, strong_notes = (models.strongest_ailment_model(main, sets, shares, notes[0]["set_uses"]) if shares
                                else ([], []))
        build.call("setAssumptions", mods=bleed + mods + strong, fields=[])
        after = build.call("stats", fields=["TotalDPS", "BleedDPS"])
        print("PROBE wind serpent ailments", label, main.stat_set, before, after, mods, strong,
              [n["basis"] for n in strong_notes])
        return {"set": main.stat_set, "before": before, "after": after, "shares": shares}

    thrust, serpent = scored_on("Thrust"), scored_on("Serpent")
    assert thrust["set"] == "Thrust" and serpent["set"] == "Serpent"
    shares = thrust["shares"]
    assert shares and shares["Thrust"] == pytest.approx(shares["Serpent"]) and 0 < shares["Thrust"] <= 1  # (one each)
    assert serpent["shares"] is None  # (scored on the Serpent: not handed over, POE2T7000's roll-average overcount)
    assert models.number(serpent["before"]["BleedDPS"]) < 0.5 * models.number(thrust["before"]["BleedDPS"])
    # (the chooser's two options: the same hits, the Thrust's Bleed higher, so it keeps the Thrust where Bleed counts)
    assert models.number(serpent["after"]["TotalDPS"]) == pytest.approx(models.number(thrust["after"]["TotalDPS"]),
                                                                          rel=0.02)
    assert models.number(serpent["after"]["BleedDPS"]) < models.number(thrust["after"]["BleedDPS"])


def test_boss_pool_note_gives_the_headline_pool_and_the_other_readings_as_rows():
    """MECHC52 (the manager 12:12: MECH98's boss_pool readings in the play guide): the headline pool over every Life bar,
    the other readings as rows; the worst case reads poe2db's (ES on top), its conversion reading a row."""
    from builder.search import bosses

    worst = a.boss_pool_note(bosses.WORST)
    assert worst["value"] == f"{conditions.boss_pool(bosses.WORST):,.0f}" and "headline reading" in worst["basis"]
    assert worst["confidence"].startswith("LOW") and "Life and Energy Shield to take off" in worst["play"]
    for name in bosses.PRESETS:
        note = a.boss_pool_note(name)
        assert note["value"] == f"{conditions.boss_pool(name):,.0f}"
        assert ("rows: none" in note["basis"]) == (not conditions.boss_pool_rows(name))


def test_life_sacrificed_on_skill_use_reaches_the_floors(engine):
    """MECHC52 (GEM30's route): models.sacrifice_life_model's build.sacrifice_life (Extraction / Sacrificial Offering:
    in no PoB cost) is netted off in the floors as LifeSacrificed; without it nothing."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Falling Thunder", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("setAssumptions", mods=[], fields=[])
    skills = build.call("activeSkills")["skills"]
    build.sacrifice_life = (0.0, "")
    search.assumptions.life_mana_income(skills, "Falling Thunder")
    before = build.recovery_sources.get("LifeSacrificed", 0.0)
    build.sacrifice_life = (50.0, "stand-in: 5% of 1,000 Life x 1 use a second")
    search.assumptions.life_mana_income(skills, "Falling Thunder")
    assert build.recovery_sources["LifeSacrificed"] == pytest.approx(before - 50.0)


def test_channelling_share_leaves_out_a_release_skills_end(monkeypatch):
    """MECHC52 (MECHB53 via the manager 21:16; R44-1 / R44-14): a channel-and-release main skill channels one attack time
    of each 1 + end use, so its share counts x 1 / (1 + end share), the end from the gem's own stats (Snipe's x its end
    animation multiplier); other channels whole."""
    from types import SimpleNamespace

    gems = {n: {"skillTypes": ["Attack", "Channel"]} for n in ("Perfect Strike", "Snipe", "Incinerate")}
    stats = {"Perfect Strike": {models.CHANNEL_END_STAT: 35},
             "Snipe": {models.CHANNEL_END_STAT: 133, models.CHANNEL_END_MULT_STAT: 2500}}

    class B:
        ctx = SimpleNamespace(gems=gems, all_gems=gems)

        def call(self, method, **kw):
            if method == "groups":
                return {"mainSocketGroup": 1, "groups": [{"index": 1, "gems": [{"name": self.main}]}]}
            assert method == "activeSkills"
            name = kw["levelStats"][0]
            return {"skills": [{"name": name, "levelStats": stats.get(name, {})}]}

    monkeypatch.setattr(models, "press_choices", lambda build: {})
    b = B()
    for name, want in (("Perfect Strike", 0.8 / 1.35), ("Snipe", 0.8 / 1.3325), ("Incinerate", 0.8)):
        b.main = name
        share, how = conditions.channelling_share(b, name, 0.8)
        assert share == pytest.approx(want), name
    assert conditions._release_end_share(b, "Gathering Storm")[0] == 0.0  # (no stats on the stand-in: 0)


def test_release_end_share_reads_the_gems_own_stats(engine):
    """MECHC52: the end shares from PoB's gem data (R44-1 / R44-14: Perfect Strike 35%, Gathering Storm 85%, Plasma
    Blast 35%, Snipe 133% x 0.25)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Perfect Strike", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Perfect Strike"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    share, how = conditions._release_end_share(search.build, "Perfect Strike")
    print("PROBE release end", share, how)
    assert share == pytest.approx(0.35) and "R44-1" in how


def test_probe_pinnacle_of_power_reaches_only_the_elemental_part_of_an_untagged_skill(engine):
    """MECHC52 (RESEARCH45 R45-3, MEDIUM-HIGH: more Elemental Damage of EVERY skill, no skill-type condition). Probe on
    Boneshatter (an Attack with no Cold / Fire / Lightning type) with added Fire: PoB's buff gives it nothing (its
    SkillType condition); the model at full uptime raises its Fire part x(1 + 15% x max) and leaves its Physical part,
    with nothing taken back (PoB gave nothing)."""
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
    most = models.number(build.call("stats", fields=["PowerChargesMax"])["PowerChargesMax"])
    build.set_config({"usePowerCharges": True, "overridePowerCharges": int(most)})
    fire = [models.raw_mod("FireMin", "BASE", 50, skill="Boneshatter", tag="Probe"),
            models.raw_mod("FireMax", "BASE", 100, skill="Boneshatter", tag="Probe")]

    def read(mods: list, added: list | None = None) -> float:
        build.call("setAssumptions", mods=(fire if added is None else added) + mods, fields=[])
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"]

    physical = read([], [])  # (no added Fire: the Physical part)
    plain = read([])
    build.set_group(gems=[{"name": a.PINNACLE_OF_POWER, "level": 20, "quality": 0}])
    pob = read([])
    build.call("setAssumptions", mods=fire, fields=[])
    build.consume_all = {"Power": {a.PINNACLE_OF_POWER: (1.0, 0.25, "stand-in")}}
    skills = build.call("activeSkills")["skills"]
    found = [models.Skill.of(s) for s in skills if not s["disabled"]]
    skill = next(s for s in found if s.group == main["index"])
    mods, notes = search.assumptions.pinnacle_of_power(skills, skill)
    modelled = read(mods)
    print("PROBE pinnacle untagged", most, physical, plain, pob, modelled, mods)
    assert plain > physical > 0
    assert pob == pytest.approx(plain, rel=1e-6)  # (PoB: no buff on an untagged skill)
    assert [m["name"] for m in mods] == ["ElementalDamage"]  # (nothing taken back)
    more = 1 + 15 * most / 100
    assert modelled == pytest.approx(physical + (plain - physical) * more, rel=0.01)  # (the Fire part only)


def test_channelling_share_reads_models_end_constants_for_the_other_channels():
    """MECHC52 (the manager 22:28: one source): Volcano's eruption channels (1.5 + k) / (2 + k) of its time at
    volcano_channel_model's best k (3: 0.9); Fury of the Mountain / Hollow Form (end cut off by the dodge in
    channel_windup_model's headline) and Oil Barrage (its end a row only) are left whole, each named."""
    from types import SimpleNamespace

    end, how = conditions._release_end_share(SimpleNamespace(), models.VOLCANO)
    assert 1 / (1 + end) == pytest.approx(4.5 / 5) and "k = 3" in how
    for name in ("Fury of the Mountain", "Hollow Form", models.OIL_BARRAGE):
        end, how = conditions._release_end_share(SimpleNamespace(), name)
        assert end == 0.0 and "row" in how


def test_fist_of_kalguur_shares_the_ward_with_a_supported_side_slam(engine, monkeypatch):
    """MECHC52 (the manager 21:16 from MECHB53: the boost was read on the main Slam only): every Slam Fist of Kalguur
    supports asks 150% of its cost a use; one share of uses boosted for all (build.kalguur_boosted, for MECH-B's
    Empowered / Aftershock readers); the main Slam's gain at that share. A side Slam with no use rate asks nothing."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Sunder", ascendancies=["Warbringer"]))
    skeleton = enumerate_skeletons(Context(payload), ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]]
    build.set_group(index=main["index"], gems=[*old, {"name": a.FIST_OF_KALGUUR, "level": 1, "quality": 0}])
    side = build.set_group(gems=[{"name": "Rolling Slam", "level": 20, "quality": 0},
                                 {"name": a.FIST_OF_KALGUUR, "level": 1, "quality": 0}])
    build.call("setAssumptions", mods=[], fields=[])
    skills = build.call("activeSkills")["skills"]
    assert any(s["name"] == "Rolling Slam" and s.get("slam") for s in skills)
    pools = build.call("stats", fields=["ManaCost", "Speed"])
    row = build.call("useRates", indices=[side["index"]])["rates"][0]
    need_main = pools["Speed"] * pools["ManaCost"] * 1.5
    need_side = 1.0 * models.number(row.get("manaCost")) * 1.5
    assert need_side > 0
    build.ward_sustain = {"WardRecoveryPerSecond": need_main / 2, "_share": 1.0}
    monkeypatch.setattr(conditions, "skill_use_rate", lambda r, c: (0.0, "no rate", True))
    mods, _ = search.assumptions.kalguur_boost(skills, "Sunder")
    assert mods[0]["value"] == pytest.approx(10.0, rel=0.05) and build.kalguur_boosted["Rolling Slam"] == 0.0
    monkeypatch.setattr(conditions, "skill_use_rate", lambda r, c: (1.0, "stand-in", False))
    mods, notes = search.assumptions.kalguur_boost(skills, "Sunder")
    share = (need_main / 2) / (need_main + need_side)
    print("PROBE kalguur side", pools, row.get("manaCost"), mods, build.kalguur_boosted, notes[0]["basis"])
    assert mods[0]["value"] == pytest.approx(20 * share, rel=0.05)
    assert build.kalguur_boosted["Rolling Slam"] == pytest.approx(share, rel=0.05) == build.kalguur_boosted["Sunder"]


def test_side_channel_hold_is_each_skills_payoff_point_not_a_flat_second():
    """MECHC52 (THE USER, 21 Nov ~23:45: "as long as needed to get the desired result, it changes based on the ability"):
    a release skill one attack time (PoB's time a use is AT x its end% for Perfect Strike / Gathering Storm / Snipe, AT
    for Plasma Blast); a stage skill its maximum stages x its stage time, or x one use time; else one use time (LOW)."""
    hold = conditions.side_channel_hold
    at, how, conf = hold("Perfect Strike", {"speed": 1 / 0.35}, {models.CHANNEL_END_STAT: 35})
    assert at == pytest.approx(1.0) and conf == "MEDIUM" and "perfect window" in how
    assert hold("Plasma Blast", {"speed": 0.5}, {models.CHANNEL_END_STAT: 35})[0] == pytest.approx(2.0)
    assert hold("Incinerate", {"speed": 4.0}, {"incinerate_maximum_stages": 8,
                                               "incinerate_gain_stage_every_x_ms": 250})[0] == pytest.approx(2.0)
    got = hold("Flameblast", {"speed": 2.0}, {"flameblast_maximum_stages": 10})
    assert got[0] == pytest.approx(5.0) and got[2] == "MEDIUM-LOW"
    got = hold("Meditate", {"speed": 1.25}, {})
    assert got[0] == pytest.approx(0.8) and got[2] == "LOW" and "no payoff point" in got[1]


def test_charge_hits_a_use_no_longer_clashes_with_the_per_use_cut():
    """MECHC52: MECHC44's (hits a use, how) tuple for a flagged Twister / Galvanic Field moved to "_charge_hits_a_use";
    under MECH99's "_hits_per_use" (a float) it read as 0 in models.per_use_factor, so every hit reader through
    at_main_share saw no hits. Now the tuple leaves the readers alone and MECH99's cut reaches the charge readers."""
    stats = {"Speed": 2.0, "HitChance": 100, "_main_share": 1.0, "_multi_charge": "x",
             "_charge_hits_a_use": (2.5, "Twister's twisters")}
    assert models.at_main_share(stats)["Speed"] == pytest.approx(2.0)
    line = "Gain a Frenzy Charge on Hit"
    assert conditions._charge_rate(line, "Frenzy", stats)[0] == pytest.approx(5.0)  # (2 hits/s x 2.5 a use)
    cut = {**stats, "_charge_hits_a_use": None, "_multi_charge": None, "_hits_per_use": 0.5}
    assert conditions._charge_rate(line, "Frenzy", cut)[0] == pytest.approx(1.0)  # (MECH99's per-use cut: half)


def test_remove_ignite_when_you_warcry_shortens_the_bosss_ignite_on_you_at_the_builds_warcry_rate():
    """MECHC53 (boss_hits.REMOVE_IGNITE_ON_WARCRY's note): Extinguishing Exhalation ends each Ignite on you at your next
    Warcry, at the build's own Warcry rate (periodic: min(L, U) on average, U uniform up to the gap); no Warcry, no cut."""
    from builder.search import boss_hits

    assert boss_hits.removed_length(4.0, 0.0) == 4.0
    assert boss_hits.removed_length(4.0, 0.5) == pytest.approx(1.0)  # (a Warcry every 2 s, shorter than the Ignite)
    assert boss_hits.removed_length(1.0, 0.25) == pytest.approx(1.0 - 1.0 / 8.0)  # (every 4 s: 1 - 1 / (2 x 4))
    base = {"enemySkillTime": 1.0, "Life": 4000.0, "AilmentThreshold": 2000.0, "FireTakenHit": 2000.0,
            "_boss": "Arbiter of Ash"}
    plain = boss_hits.boss_ailments_on_you(base)
    cut = boss_hits.boss_ailments_on_you(base, removals={"Ignite": (0.5, "stand-in 0.5/s")})
    none = boss_hits.boss_ailments_on_you(base, removals={"Ignite": (0.0, "no Warcry")})
    assert 0 < cut["ignited"] < plain["ignited"] and cut["ignite"] < plain["ignite"]
    assert none["ignited"] == pytest.approx(plain["ignited"])
    assert "next Warcry" in cut["notes"][0]["basis"] and "without it: Ignited" in cut["notes"][0]["basis"]
    assert boss_hits.REMOVE_IGNITE_ON_WARCRY.match("Remove Ignite when you Warcry")


def test_the_devouring_diadem_recovers_life_from_the_corpses_the_fight_gives():
    """MECHC53 (recovery.DIADEM's note): 20% of maximum Life a corpse, one every 3 s at most, from the corpses there are
    (the build's own supply); none to consume (a lone boss): 0, said."""
    from builder.search import recovery
    from builder.search.recovery import Income

    line = "Every 3 seconds, Consume a nearby Corpse to Recover 20% of maximum Life"
    assert any(p.search(line) for p in recovery.SPENT_LINES)
    stats = {"Life": 3000.0, "Mana": 500.0}
    income = Income()
    recovery.spent_income(income, stats, [line], diadem=(0.2, "test"))
    assert income.rates["LifeOnConsumeRecovery"] == pytest.approx(0.20 * 3000 * 0.2)
    none = Income()
    recovery.spent_income(none, stats, [line], diadem=(0.0, "no corpse to consume on the boss"))
    assert none.rates["LifeOnConsumeRecovery"] == 0 and any("Devouring Diadem" in p for p in none.parts)


def test_probe_a_main_concoction_is_capped_at_its_own_throw_rate_not_its_trigger_rate(engine, monkeypatch):
    """MECHC53 (flask_main's cap): with the Mana Flask's charges allowing far more throws than the attack rate, the
    throws are capped at PoB's own Speed for the skill; once the trigger rate is in, the cap is read without it (not the
    circular read-back)."""
    from builder.request.schema import BuildRequest
    from builder.search import flasks
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    name = "Explosive Concoction"
    payload = make_payload(BuildRequest(main_skill=name, ascendancies=["Pathfinder"]))
    skeleton = enumerate_skeletons(Context(payload), [name])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, assume = search.build, search.assumptions
    build.call("setGroup", gems=[{"name": name, "level": 20, "quality": 0}], main=True, fields=[])
    build.call("setAssumptions", mods=[], fields=[])
    assume.mods = []
    own = models.number(build.call("stats", fields=["Speed"])["Speed"])
    monkeypatch.setattr(flasks, "charge_use_rate", lambda *a, **k: (100.0, "stand-in: 100 throws/s of charges"))
    skills = build.call("activeSkills")["skills"]
    found = assume.flask_main(skills, name)
    assert found is not None
    mods, note = found
    print("PROBE concoction", own, mods, note["value"], note["basis"])
    assert 0 < own < 10 and note["value"] == pytest.approx(own, rel=0.01) and "capped at its own throw rate" in note["basis"]
    # (the next pass, after an inflated rate: PoB now reports 50 as Speed; the cap must still be the skill's own)
    assume.mods = models.trigger_rate(name, 50.0)
    build.call("setAssumptions", mods=assume.mods, fields=[])
    assert models.number(build.call("stats", fields=["Speed"])["Speed"]) == pytest.approx(50.0, rel=0.01)
    again, note2 = assume.flask_main(skills, name)
    assert note2["value"] == pytest.approx(own, rel=0.01)
    assert models.number(build.call("stats", fields=["Speed"])["Speed"]) == pytest.approx(50.0, rel=0.01)  # (put back)


def test_probe_a_side_fireball_or_ember_fusillade_shares_the_main_skills_infusions(engine):
    """MECHC53 (GEM34's find; other_infusion_users): a consumer is found by its own stat
    active_skill_consumes_a_<element>_infusion, not only an "<Element>-Infused" set label. A side Fireball takes Fire
    Infusions, a side Ember Fusillade Lightning ones, each at its press rate; neither takes Cold."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, assume = search.build, search.assumptions
    setattr(build.objective, models.PRESS_CHOICES, {"Fireball": 0.5, "Ember Fusillade": 0.25})
    for name in ("Fireball", "Ember Fusillade"):
        build.call("setGroup", gems=[{"name": name, "level": 20, "quality": 0}], fullDPS=True, fields=[])
    raw = {s["name"]: s for s in build.call("activeSkills", levelStats=["Fireball", "Ember Fusillade"])["skills"]}
    print("PROBE infusion stats", {n: {k: v for k, v in (raw[n].get("levelStats") or {}).items() if "infusion" in k}
                                   for n in ("Fireball", "Ember Fusillade") if n in raw})
    fire, fire_how = assume.other_infusion_users("Fire")
    lightning, lightning_how = assume.other_infusion_users("Lightning")
    cold, _ = assume.other_infusion_users("Cold")
    print("PROBE infusion users", fire, fire_how, lightning, lightning_how, cold)
    assert fire > 0 and "Fireball" in fire_how and "Ember" not in fire_how
    assert lightning > 0 and "Ember Fusillade" in lightning_how and "Fireball" not in lightning_how
    assert cold == 0

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
    presses x 1 s held (LOW); nothing channelled: 0."""
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
            assert method == "useRates" and kw["indices"] == [2]
            return {"rates": [{"index": 2, "speed": 5.0}]}

    monkeypatch.setattr(models, "press_choices", lambda build: {})
    monkeypatch.setattr(conditions, "skill_use_rate", lambda row, choices: (0.25, "chosen", False))
    b = B()
    b.main = "Flameblast"
    share, how = conditions.channelling_share(b, "Flameblast", 0.6)
    assert share == pytest.approx(0.6 + 0.25) and "LOW" in how
    b.main = "Spark"
    assert conditions.channelling_share(b, "Spark", 0.9)[0] == pytest.approx(0.25)
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

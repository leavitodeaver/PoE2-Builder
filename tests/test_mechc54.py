"""MECHC54: the flat-rate audit. No charge, Rage, Onslaught or Arcane Surge line falls to a flat "one per 4 s" any more
(conditions.no_event_rate's note): each line the audit found has its own event from the build, or reads 0 with a note."""
import pytest

from builder.search import conditions


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


STATS = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "enemySkillTime": 1.0, "_kills": (0.0, 0.0, "lone boss"),
         "_events": {}, "ManaPerSecondCost": 20.0, "MaximumRage": 30, "PowerChargesMax": 4, "PowerChargesDuration": 15}
POWER_WITHIN = "20% increased Critical Damage Bonus if you've gained a Power Charge Recently"


def test_the_power_within_recently_line_is_no_power_charge_source():
    """The notable's condition line ("if you've gained ... Recently") was read as a gain at one per 4 s: 3.8 Power
    Charges held from nothing (an OVERCOUNT)."""
    assert conditions._charge_rate(POWER_WITHIN, "Power", STATS) is None
    held, how = conditions._charges_held("Power")([POWER_WITHIN, "+1 to Maximum Power Charges"], STATS)
    assert held == 0 and "one per 4 s" not in how


@pytest.mark.parametrize("text,kind", [
    ("10% chance to gain a Power Charge if you Knock an Enemy Back with Melee Damage", "Power"),
    ("15% chance to gain a Frenzy Charge when your Trap is triggered by an Enemy", "Frenzy"),
    ("25% chance to gain a Power Charge when you Throw a Trap", "Power"),
    ("Gain 2 Endurance Charge on use", "Endurance"),  # (on an item not in use)
])
def test_a_charge_event_with_no_rate_from_the_build_reads_zero_with_a_note(text, kind):
    rate, how = conditions._charge_rate(text, kind, STATS)
    assert rate == 0 and "one per 4 s" not in how and how


def test_the_other_readers_fall_to_zero_not_a_flat_rate():
    rage = conditions._rage_gain("Gain 5 Rage when you Throw a Trap", STATS, True)
    assert rage[0] == 0 and "one per 4 s" not in rage[2]
    up, how = conditions._onslaught_source("Gain Onslaught when you Throw a Trap", [], STATS)
    assert up == 0 and "one per 4 s" not in how
    up, how = conditions._arcane_surge_source("Gain Arcane Surge when you Throw a Trap", [], STATS)
    assert up == 0 and "one per 4 s" not in how


def test_power_charge_per_mana_spent_and_per_infusion_consumed():
    mana = conditions._charge_rate("Gain a Power Charge after Spending a total of 200 Mana", "Power", STATS)
    assert mana[0] == pytest.approx(0.1)  # 20 Mana a second / 200
    text = "Gain a Power Charge when you consume an Elemental Infusion"  # (the notable Infusion of Power)
    assert conditions._charge_rate(text, "Power", STATS)[0] == 0  # (no consumption read)
    fed = {**STATS, "_infusion_consumed": (0.5, "test")}
    assert conditions._charge_rate(text, "Power", fed)[0] == pytest.approx(0.5)


def test_the_two_lineage_supports_gain_where_they_work():
    """Their descriptions are PoB's own; Ailith's Chimes: 25% a Combo the main skill expends; Guatelitzi's Ablation: one
    every 4 s while a supported Offering is up."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "vendor" / "PathOfBuilding-PoE2" / "src" / "Data" / "Skills"
    found = set()
    for name, file in ((conditions.AILITH, "sup_dex.lua"), (conditions.GUATELITZI, "sup_int.lua")):
        src = (root / file).read_text(encoding="utf-8")
        block = src[src.index(f'name = "{name}"'):]
        found.add(re.search(r'description = "([^"]*)"', block).group(1).strip())
    assert found == set(conditions.POWER_SUPPORT_TEXT)
    ailith, guat = (next(t for t, n in conditions.POWER_SUPPORT_TEXT.items() if n == name)
                    for name in (conditions.AILITH, conditions.GUATELITZI))
    assert conditions._charge_rate(ailith, "Power", STATS)[0] == 0  # not on the main skill
    assert conditions._charge_rate(guat, "Power", STATS)[0] == 0  # no Offering supported
    on = {**STATS, "_combo_on_use": (4.0, "Tempest Bell at 4 Combo"),
          "_power_supports": {conditions.AILITH: "on the main skill", conditions.GUATELITZI: "supporting Pain Offering"}}
    assert conditions._charge_rate(ailith, "Power", on)[0] == pytest.approx(0.25 * 4 * 2.0)
    assert conditions._charge_rate(guat, "Power", on)[0] == pytest.approx(0.25)


def test_mist_raven_culls_at_the_builds_kills_and_lingering_illusion_at_zero():
    raven = ("Summons a mysterious undamageable Mist Raven that can be Commanded to dive at target location, crashing "
             "through an otherworldly mirror and damaging enemies in an area. The raven grants you a Frenzy Charge for "
             "each enemy it Culls.")
    assert conditions._charge_rate(raven, "Frenzy", STATS)[0] == 0  # a lone boss
    adds = {**STATS, "_kills": (0.2, 0.2, "the preset's adds")}
    assert conditions._charge_rate(raven, "Frenzy", adds)[0] == pytest.approx(0.2)
    illusion = ("While active, creates a short-lived illusory copy of yourself whenever you dodge roll. The copy can be "
                "damaged by enemies, and copies that are destroyed by an enemy will drop a Remnant that grants you a "
                "Power Charge on pickup.")
    rate, how = conditions._charge_rate(illusion, "Power", adds)
    assert rate == 0 and "one per 4 s" not in how


def test_probe_ailith_and_guatelitzi_reach_the_power_charge_model(engine):
    """Real PoB: Ailith's Chimes on a Tempest Bell main skill and Guatelitzi's Ablation on a Pain Offering group reach
    the Power Charge basis through their description lines, at their own events (before: one per 4 s each)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Tempest Bell", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Tempest Bell"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    group = build.main_group()
    gems = [{"name": g["name"]} for g in group["gems"]] + [{"name": conditions.AILITH, "level": 1, "quality": 0}]
    build.call("setGroup", index=group["index"], gems=gems, fields=[])
    build.call("setGroup", gems=[{"name": "Pain Offering", "level": 20, "quality": 0},
                                 {"name": conditions.GUATELITZI, "level": 1, "quality": 0}], fields=[])
    lines = build.call("sourceLines")["lines"]
    sources = {ln["source"] for ln in lines}
    evidence = search.conditions.evidence()
    held = evidence.get("overridePowerCharges")  # (the count's basis; "usePowerCharges" carries the source line)
    print("PROBE power", sorted(s for s in sources if s.startswith("gem:")), held)
    assert f"gem:{conditions.AILITH}" in sources and f"gem:{conditions.GUATELITZI}" in sources
    assert held is not None and "one per 4 s" not in held[1]
    assert f"{conditions.AILITH}: 25% a Combo expended" in held[1]
    assert f"{conditions.GUATELITZI}: one every 4 s" in held[1] and "Pain Offering" in held[1]


def test_charged_staff_gives_no_buff_with_a_talisman_equipped(engine):
    """RESEARCH47 R47-3 row 9 (a live bug, UNVERIFIED on 0.5.5h): with a Talisman in any weapon slot the buff counts 0,
    PoB's held-count mod taken back too; the "works" reading stays in the note."""
    from types import SimpleNamespace

    from builder.request.schema import BuildRequest
    from builder.search import assumptions as a
    from builder.search import models
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Falling Thunder", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(gems=[{"name": a.CHARGED_STAFF, "level": 20, "quality": 0}])
    main = build.main_group()
    build.set_config({"usePowerCharges": True, "overridePowerCharges": 3})
    build.call("setAssumptions", mods=[], fields=[])
    three = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    build.set_config({"usePowerCharges": False, "overridePowerCharges": 0})
    build.call("setAssumptions", mods=[], fields=[])
    none = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    build.set_config({"usePowerCharges": True, "overridePowerCharges": 3})
    build.consume_all = {"Power": {a.CHARGED_STAFF: (0.5, 0.2, "stand-in")}}
    build.equipped["Weapon 1 Swap"] = SimpleNamespace(type="Talisman", raw="")
    try:
        skills = build.call("activeSkills")["skills"]
        skill = next(s for s in (models.Skill.of(r) for r in skills if not r["disabled"]) if s.group == main["index"])
        mods, notes = search.assumptions.charged_staff(skills, skill)
        build.call("setAssumptions", mods=mods, fields=[])
        dps = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    finally:
        build.equipped.pop("Weapon 1 Swap", None)
    print("PROBE talisman", none, three, dps, notes[0]["value"])
    assert three > none * 1.1 and dps == pytest.approx(none, rel=0.01)
    assert "Talisman" in notes[0]["value"] and "if it works: 3.0 stacks" in notes[0]["basis"]


def test_devour_and_killing_palm_add_the_bells_they_eat():
    devour = ("Shapeshift into a Wyvern and devour a Corpse or Cullable enemy, and additional nearby Corpses. For each "
              "enemy or corpse devoured, you regenerate life and gain a Power Charge.")
    assert conditions._charge_rate(devour, "Power", STATS)[0] == 0  # a lone boss, no bells
    fed = {**STATS, "_bell_charges": {"Devour": (0.4, "0.400 Hollow Focus bells eaten a second")}}
    rate, how = conditions._charge_rate(devour, "Power", fed)
    assert rate == pytest.approx(0.4) and "Hollow Focus bells" in how


def test_probe_killing_palm_eats_hollow_focus_bells_for_power_charges(engine):
    """Real PoB: Killing Palm (main) with Hollow Focus: bells eaten = min(its uses, the bells a second), each a Power
    Charge in the Power Charge basis; Killing Palm Hits the bell it Culls, so no shockwave is taken off (no mod).
    (Devour's skeleton read PoB Speed 0 for Devour in POE2T7029: its own check is next.)"""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Killing Palm", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Killing Palm"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("setGroup", gems=[{"name": models.HOLLOW_FOCUS, "level": 20, "quality": 0}], fields=[])
    main_group = build.main_group()
    skills = build.call("activeSkills", levelStats=[models.HOLLOW_FOCUS, "Killing Palm"])["skills"]  # (as Assumptions.models)
    found = [models.Skill.of(s) for s in skills if not s["disabled"]]
    main = next(s for s in found if s.group == main_group["index"] and s.name == "Killing Palm")
    stats = build.call("stats", fields=["Speed"])
    mods, notes = search.assumptions.hollow_focus_bells(found, main, stats)
    bell = next(s for s in found if s.name == models.HOLLOW_FOCUS)
    bells = (bell.cooldown_recovery or 1.0) / (number_of(bell.level_stats.get(models.REPEAT_STAT)) / 1000)
    eaten = build.bell_charges["Killing Palm"][0]
    print("PROBE bells", stats, bells, build.bell_charges, mods, notes[0]["basis"])
    assert 0 < eaten <= bells + 1e-9 and mods == []
    evidence = search.conditions.evidence()
    held = evidence.get("overridePowerCharges")
    print("PROBE bells held", held)
    assert held is not None and "Hollow Focus bells eaten" in held[1]


def number_of(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def test_skyfall_builds_glory_from_the_ward_other_skills_spend():
    """Skyfall's active_skill_generates_%_of_ward_spent_as_glory 100 (270 Glory a use, a 20 s instance): before, "no
    source counted" and never used; now one use per (270 / Ward a second + 20 s)."""
    from builder.search import models

    plan, how = models.glory_plan(["Skyfall"], {"_ward_spent": (27.0, "test"), "Speed": 1.0}, None, [], [])
    assert plan["Skyfall"][0] == pytest.approx(1 / 30) and "Runic Ward spent" in how
    plan, how = models.glory_plan(["Skyfall"], {"Speed": 1.0}, None, [], [])
    assert "Skyfall" not in plan and "never" in how

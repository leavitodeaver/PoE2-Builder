"""MECH89 (MECH-A): the other hitting groups' Armour Break in the Fully Broken share (conditions.break_others)."""
import pytest

from builder.search import conditions
from builder.search.conditions import BREAK_MORE_STAT, _armour_broken, break_others

GROUPS = [{"index": 1, "gems": [{"name": "High Velocity Rounds"}]},
          {"index": 2, "gems": [{"name": "Armour Piercing Rounds"}, {"name": "Armour Break I"}]}]
EVERY = {"High Velocity Rounds": {"skillTypes": ["Attack", "Projectile"]},
         "Armour Piercing Rounds": {"skillTypes": ["Attack", "Projectile"]}}
ROW = {"index": 2, "name": "Armour Piercing Rounds", "triggered": True, "speed": 2.0, "HitChance": 50.0,
       "CritChance": 0.0, "PhysicalHitAverage": 1000.0}


def _skill(level=None, support=None):
    return {"name": "Armour Piercing Rounds", "levelStats": level or {}, "supportStats": support or {}}


def test_a_side_groups_own_break_stats_count_at_its_hits():
    flat = _skill({"apply_X_armour_break_on_hit": 300})
    got = break_others([flat], {2: ROW}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY)
    # 300 a hit x 2 uses/s x 50% hit chance
    assert got[0][0] == "Armour Piercing Rounds" and got[0][1] == pytest.approx(300.0)
    # Armour Break I's 10% of Physical on its own hit (the row's PhysicalHitAverage), x its Armour Demolisher's 50% more
    share = _skill(support={"armour_break_physical_damage_%_dealt_as_armour_break": 10, BREAK_MORE_STAT: 50})
    got = break_others([share], {2: ROW}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY)
    assert got[0][1] == pytest.approx(0.10 * 1000 * 1.0 * 1.5) and "+50% more" in got[0][2]
    # the main group is never counted here (its lines are _armour_broken's own)
    assert break_others([flat], {1: {**ROW, "index": 1}}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY) == []


def test_a_side_group_breaks_with_the_builds_own_hit_lines_but_not_the_shared_bar_ones():
    lines = ["Hits Break 100 Armour", "Fully Break Armour on Heavy Stun"]
    got = break_others([_skill()], {2: ROW}, GROUPS, {"Armour Piercing Rounds"}, 1, lines, EVERY)
    assert got[0][1] == pytest.approx(100.0) and "shared bar" in got[0][2]


def test_cannot_break_armour_and_no_uses_count_nothing():
    stop = _skill({"apply_X_armour_break_on_hit": 300}, {conditions.CANNOT_BREAK: 1})
    got = break_others([stop], {2: ROW}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY)
    assert got[0][1] == 0 and "cannot Break" in got[0][2]
    idle = {**ROW, "triggered": False, "speed": 0.0}
    got = break_others([_skill({"apply_X_armour_break_on_hit": 300})], {2: idle}, GROUPS, {"Armour Piercing Rounds"},
                       1, [], EVERY, {})
    assert got[0][1] == 0 and "no uses" in got[0][2]


def test_side_breaks_alone_fully_break_the_boss():
    """High Velocity Rounds (cannot Break) as the main skill, Armour Piercing Rounds beside it: before MECH89 the share
    was 0 (no main-skill line), so HVR's "Vs Armour Broken" part never counted (an UNDERCOUNT)."""
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "_main_types": {"Attack"}, "_enemy_armour": 1200}
    assert _armour_broken([], stats) == (0.0, "")
    side = {**stats, "_break_others": [("Armour Piercing Rounds", 100.0, "Armour Piercing Rounds at a test rate")]}
    up, basis = _armour_broken([], side)  # 12 s to break, 12 s Broken: 50%
    assert up == pytest.approx(0.5) and "beside the main skill: Armour Piercing Rounds" in basis
    # the build's "Break X% increased Armour" lines scale them too
    up, basis = _armour_broken(["Break 100% increased Armour"], side)  # 6 s to break: 12 / 18
    assert up == pytest.approx(2 / 3) and "x2.00 from the build's Break lines" in basis


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


def test_armour_piercing_rounds_beside_high_velocity_rounds_fully_breaks_the_boss(engine):
    """The probe (LANE_RULES): High Velocity Rounds as the main skill, Armour Piercing Rounds pressed beside it. Before
    MECH89 the Fully Broken share (build.full_break_up, which HVR's "Vs Armour Broken" part reads) stayed 0."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="High Velocity Rounds", ascendancies=["Witchhunter"]))
    skeleton = enumerate_skeletons(Context(payload), ["High Velocity Rounds"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    search.conditions.evidence()
    before = build.full_break_up
    build.set_group(gems=[{"name": "Armour Piercing Rounds", "level": 20, "quality": 0}], label="MECH89 side")
    search.conditions.evidence()
    unpressed = build.full_break_up
    setattr(build.objective, "press_choices", {"Armour Piercing Rounds": 1.0})
    search.conditions.evidence()
    after = build.full_break_up
    print("PROBE full_break_up", before, unpressed, after)
    assert unpressed[0] == pytest.approx(before[0])  # (no press chosen: no uses, 0)
    assert after[0] > before[0] and "beside the main skill: Armour Piercing Rounds" in after[1]


def test_fangs_of_frosts_chance_not_to_consume_parried_feeds_more_hits():
    """MECH89 (B2, GEM21's routing): with chance c not to consume Parried, each Parried feeds 1 / (1 - c) hits, so the
    share of a consumer's hits on a Parried boss rises (an UNDERCOUNT before); 0 keeps the old one hit per Parried."""
    from builder.search import models

    parry = models.Skill("Parry", attack=True, cast_time=1.0)
    stats = {"enemySkillTime": 0.7, "Speed": 2.0, "HitChance": 100}
    one = models.parry_plan([parry], stats, True, True, payoff=2.0)
    kept = models.parry_plan([parry], stats, True, True, payoff=2.0, keep=0.15)
    assert kept.box == pytest.approx(min(1.0, one.box / 0.85)) and "1.18 hits per Parried" in kept.how
    assert models.parry_plan([parry], stats, True, True, payoff=2.0, keep=0.0).box == pytest.approx(one.box)
    # a non-consumer ignores it
    assert models.parry_plan([parry], stats, True, False, keep=0.5).how == models.parry_plan([parry], stats, True).how


def test_grinning_immolations_burn_costs_at_its_self_ignited_share():
    """MECH89 (COV43's OVERCOUNT candidate): the Ignited box came on from Grinning Immolation, its 15% of Life + ES a
    second didn't count. Now it's PoB's degen at the share of the fight your crits keep you Ignited."""
    line = ("Become Ignited when you deal a Critical Hit, taking 15% of your maximum Life and Energy Shield as Fire "
            "Damage per second")
    stats = {"Life": 3000, "EnergyShield": 1000, "CritChance": 50, "Speed": 2.0, "HitChance": 100,
             "IgniteAvoidChance": 0}
    up = conditions.grinning_ignited(stats)[0]
    assert up == pytest.approx(1.0)  # (no avoidance: every crit Ignites, always Ignited)
    burn, how, plain = conditions.grinning_burn([line], stats)
    assert burn == pytest.approx(0.15 * 4000) == pytest.approx(plain) and "Fire resistance" in how
    half = conditions.grinning_burn([line], {**stats, "IgniteAvoidChance": 90, "SelfIgniteEffect": 50})[0]
    assert half == pytest.approx(0.15 * 4000 * 0.5 * conditions.grinning_ignited({**stats, "IgniteAvoidChance": 90})[0])
    assert conditions.grinning_burn([], stats) == (0.0, "", 0.0)
    # R32-7 (6b): the magnitude-scaled headline, the unscaled reading beside it
    scaled = conditions.grinning_burn([line], {**stats, "SelfIgniteEffect": 50})
    assert scaled[0] == pytest.approx(300.0) and scaled[2] == pytest.approx(600.0)
    assert conditions.grinning_burn([line], {**stats, "IgniteAvoidChance": 100})[0] == 0


def test_corrosions_poison_breaks_armour_at_its_poison_damage():
    """MECH89: Corrosion's "Poison applied by supported Skills also Breaks Armour equal to N% of Poison damage" (every
    Poison wording gave 0 before, an UNDERCOUNT): N% of PoB's PoisonDPS a second."""
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "_main_types": {"Attack"}, "_enemy_armour": 1200,
             "PoisonDPS": 1000.0}
    line = conditions.BREAK_STATS["armour_break_for_%_of_poison_damage_over_poison_duration"].format("10")
    up, basis = _armour_broken([line], stats)  # 100 a second: 12 s to break, 12 s Broken
    assert up == pytest.approx(0.5) and "PoisonDPS" in basis
    # beside the main skill, from its group's row (PoisonDPS is in the bridge's useRates rows since MECH88)
    corr = _skill(support={"armour_break_for_%_of_poison_damage_over_poison_duration": 10})
    got = break_others([corr], {2: {**ROW, "PoisonDPS": 500.0}}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY)
    assert got[0][1] == pytest.approx(50.0)
    # pressed at half its own speed: half its Poison
    pressed = {**ROW, "PoisonDPS": 500.0, "triggered": False, "speed": 2.0}
    got = break_others([corr], {2: pressed}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY,
                       {"Armour Piercing Rounds": 1.0})
    assert got[0][1] == pytest.approx(25.0)


def test_mark_for_death_breaks_with_every_hit_on_the_marked_boss():
    """MECH89: Mark for Death's "Hits against targets Marked by Supported Skills Break Armour equal to N% of Physical
    damage dealt" (no reader before, an UNDERCOUNT): the main skill's and each side group's Physical hit x N% at the
    Marked share."""
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "_main_types": {"Attack"}, "_enemy_armour": 1200,
             "PhysicalHitAverage": 2000.0, "_mark_break": (10.0, 0.5, "test: Marked half the fight")}
    up, basis = _armour_broken([], stats)  # 10% x 2,000 x 1 hit/s x 50% = 100 a second: 50%
    assert up == pytest.approx(0.5) and "Mark for Death" in basis
    got = break_others([_skill()], {2: ROW}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY, None, (10.0, 0.5, ""))
    assert got[0][1] == pytest.approx(0.10 * 1000 * 1.0 * 0.5)
    assert break_others([_skill()], {2: ROW}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY, None,
                        (10.0, 0.0, "")) == []


def test_brittle_armour_breaks_with_every_hit_on_the_frozen_boss():
    """MECH89: Brittle Armour on the main skill (no reader before, an UNDERCOUNT): every hit's Physical x N% at the
    Frozen share."""
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "_main_types": {"Attack"}, "_enemy_armour": 1200,
             "PhysicalHitAverage": 2000.0, "_brittle_break": (10.0, 0.25, "test: Frozen a quarter of the fight")}
    up, basis = _armour_broken([], stats)  # 10% x 2,000 x 25% = 50 a second: 24 s to break, 12 s Broken
    assert up == pytest.approx(12 / 36) and "Brittle Armour" in basis and "Frozen" in basis
    got = break_others([_skill()], {2: ROW}, GROUPS, {"Armour Piercing Rounds"}, 1, [], EVERY, None, None,
                       (10.0, 0.25, ""))
    assert got[0][1] == pytest.approx(0.10 * 1000 * 1.0 * 0.25)


def test_rending_apex_breaks_with_the_final_strike_once_a_combo():
    """MECH89: Rending Apex (no reader before, an UNDERCOUNT): 75% of the Final Strike's hit once per combo."""
    sets = [{"label": "Normal Strikes", "TotalDPS": 1000.0, "Speed": 2.0},
            {"label": "Third Strike", "TotalDPS": 1500.0, "Speed": 1.0}]
    rate, how = conditions.final_strike_break("Ice Strike", 75, sets)
    # Normal, Normal, Third: 0.5 + 0.5 + 1.0 = 2 s a combo; the Third Strike 1,500 a use; 75% of it every 2 s
    assert rate == pytest.approx(0.75 * 1500 / 2.0) and "Third Strike" in how
    assert conditions.final_strike_break("Cleave", 75, sets) == (0.0, "")
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "_main_types": {"Attack"}, "_enemy_armour": 1200,
             "_final_break": (100.0, "test")}
    assert _armour_broken([], stats)[0] == pytest.approx(0.5)


def test_rending_apex_on_ice_strike_fully_breaks_the_boss(engine):
    """The probe: Ice Strike with Rending Apex in its group Breaks Armour with its Third Strike (0 before MECH89)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Ice Strike", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Ice Strike"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    search.conditions.evidence()
    before = build.full_break_up
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]]
    build.set_group(index=main["index"], gems=[*old, {"name": "Rending Apex", "level": 1, "quality": 0}])
    search.conditions.evidence()
    after = build.full_break_up
    print("PROBE rending apex", before, after)
    assert after[0] > before[0] and "Rending Apex" in after[1]



def test_rare_in_presence_probe(engine):
    """MECH89's probe (LANE_RULES "PoB already does X"; the coverage audit marks the line never-on, "nothing sets it"):
    PoB counts "N% increased Attack Speed while a Rare or Unique Enemy is in your Presence" with the boss inside your
    Presence and not outside it (POE2T4166: 1.008 plain, 1.0752 at 1 m, 1.008 at 10 m, Presence 4 m), as the plain
    Attack Speed control: no model needed, the audit row is a misclassification."""
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

    def speed(distance: int, line: str | None) -> float:
        build.set_config({"enemyDistance": distance})
        if line:
            ring = chr(10).join(["Rarity: Rare", "Test Ring", "Ruby Ring", "Item Level: 82", "Implicits: 0", line])
            build.call("equip", slot="Ring 1", raw=ring, fields=[])
        out = build.call("stats", fields=["Speed", "PresenceRadius"])
        return out["Speed"], out.get("PresenceRadius")

    near_plain = speed(10, "+10 to Strength")
    control = speed(10, "8% increased Attack Speed")
    near = speed(10, "8% increased Attack Speed while a Rare or Unique Enemy is in your Presence")
    far = speed(100, "8% increased Attack Speed while a Rare or Unique Enemy is in your Presence")
    far_plain = speed(100, "+10 to Strength")
    print("PROBE rare-in-presence speeds: plain", near_plain, "control", control, "near", near, "far", far,
          "far plain", far_plain, "config", build.call("getConfig")["input"].get("enemyIsBoss"))
    assert control[0] > near_plain[0]
    assert near[0] == pytest.approx(control[0]) and far[0] == pytest.approx(far_plain[0])



def test_exited_and_entered_presence_recently_lines_count_at_the_reentry_share():
    """MECH89 (never-on rows, UNDERCOUNTS): Decisive Retreat's crit damage vs an enemy that left your Presence Recently
    counts at 1 - e^(-4 r), r the boss's re-entries from your rolls; 0 when the boss stays outside your Presence."""
    import math

    from builder.search import models

    lines = ["50% increased Critical Damage Bonus against Enemies that have exited your Presence Recently",
             "12% increased Critical Hit Chance against Enemies that have entered your Presence Recently"]
    stats = {"PresenceRadius": 40, "MovementSpeedMod": 1.0}  # 4 m Presence; a 4.6 m roll from 1 m leaves it
    mods, notes = conditions.presence_recently_mods(lines, stats, 1.0)
    share = 1 - math.exp(-4 / (2 * models.DODGE_EVERY))
    assert [m["line"] for m in mods] == ["1% increased Critical Damage Bonus", "1% increased Critical Hit Chance"]
    assert [m["scale"] for m in mods] == [pytest.approx(50 * share, abs=1e-2), pytest.approx(12 * share, abs=1e-2)]
    assert "staying put: none" in notes[0]["play"]
    far, far_notes = conditions.presence_recently_mods(lines, stats, 10.0)
    assert far == [] and far_notes[0]["value"] == "0%"

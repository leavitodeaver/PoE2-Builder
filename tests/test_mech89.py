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
    burn, how = conditions.grinning_burn([line], stats)
    assert burn == pytest.approx(0.15 * 4000) and "Fire resistance" in how
    half = conditions.grinning_burn([line], {**stats, "IgniteAvoidChance": 90, "SelfIgniteEffect": 50})[0]
    assert half == pytest.approx(0.15 * 4000 * 0.5 * conditions.grinning_ignited({**stats, "IgniteAvoidChance": 90})[0])
    assert conditions.grinning_burn([], stats) == (0.0, "")
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

"""MECH86 (MECH-A): each curse's own activation delay (PoB's output.CurseDelay, was a flat 1.5 s) and the "further than
N m" distance trial kept within a shortened Projectile Range (RESEARCH29 R29-2). Unit cases need no PoB; the `engine`
one is the before / after probe (real PoB, in the queue)."""
import pytest

from builder.search import conditions, models
from builder.search.models import Skill


@pytest.fixture(scope="module")
def engine():
    """One real PoB engine for the module (test_engine_models.py's fixture)."""
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


# A curse's own activation delay ------------------------------------------------------------------------------------

GEMS = {"Despair": {"skillTypes": ["Spell", "AppliesCurse"]}, "Hexblast": {"skillTypes": ["Spell"]}}
HEXBLAST_GROUP = {"enabled": True, "main": True, "gems": [{"name": "Hexblast", "support": False}]}
CURSE_GROUP = {"enabled": True, "gems": [{"name": "Despair", "support": False}]}


@pytest.mark.parametrize("delays, delay", [
    ({}, 1.5),  # PoB gave none: the 1.5 s fallback (as before)
    ({"Despair": 0.759}, 0.759),  # Focused Curse's 50% less
    ({"Despair": 0.0}, 0.0),  # "Curses have no Activation Delay": 0 kept, not taken for "none"
])
def test_a_curses_delay_is_pobs_and_moves_hexblasts_cycle(delays, delay):
    stats = {"_curse_durations": {"Despair": 8.0}, "_curse_delays": delays}
    (use,) = models.curse_uses([HEXBLAST_GROUP, CURSE_GROUP], GEMS, stats, 8.0)
    assert use.hexblast and use.delay == pytest.approx(delay)
    assert models.hexblast_cycle(use, 0.5) == pytest.approx(0.5 + delay + 4.0)
    hexblast, despair = Skill("Hexblast", 1, spell=True), Skill("Despair", 2, spell=True, cast_time=0.5)
    mods, notes = models.hexblast_model([hexblast, despair], [use], {"Speed": 1.0}, hexblast)
    less = [m["scale"] for m in mods if m["line"] == "1% less Damage"]
    assert less == [pytest.approx(100 * (1 - 1 / (0.5 + delay + 4.0)), rel=1e-3)]
    assert f"{delay:.3g} s delay" in notes[0]["basis"]
    # every way of applying it carries the delay (here: not the main skill, cast by hand)
    (plain,) = models.curse_uses([{**HEXBLAST_GROUP, "main": False}, CURSE_GROUP], GEMS, stats, 8.0)
    assert plain.delay == pytest.approx(delay) and f"recast {delay:g} s before" in plain.how


def test_the_bridge_reports_a_curses_delay_with_the_builds_modifiers(engine):
    """Probe (before / after): Despair alone lands 1.5 s after the cast (curse_delay_duration_ms); with Focused Curse
    (support_focus_curse_curse_delay_+%_final -50) 0.75 s, rounded up to a server tick (0.033 s): PoB's own
    output.CurseDelay, which nothing read before (every curse landed after 1.5 s)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Despair 20/0 1")
    skills = {s["name"]: s for s in build.call("activeSkills")["skills"]}
    plain = skills["Despair"].get("curseDelay")
    build.call("addSkillGroup", text="Despair 20/0 1\nFocused Curse 1/0 1")
    focused = [s.get("curseDelay") for s in build.call("activeSkills")["skills"] if s["name"] == "Despair"]
    print("PROBE curse delay", plain, focused, skills["Spark"].get("curseDelay"))
    assert plain == pytest.approx(1.5, abs=0.034)
    assert min(focused) == pytest.approx(0.75, abs=0.034)
    assert skills["Spark"].get("curseDelay") is None


# The "further than N m" trial within a shortened Projectile Range ------------------------------------------------

FAR = "20% more Damage against Enemies further than 6m"
HALF_RANGE = "50% reduced Projectile Range"


def test_a_half_range_bow_isnt_tried_beyond_its_reach():
    # (an Artillery / Obliterator Bow's implicit: 12 m x 50% = 6 m, R29-2's lower end, so 6.5 m is out of reach)
    assert conditions.projectile_reach([HALF_RANGE], {"Attack", "Projectile"})[0] == pytest.approx(6.0)
    assert conditions.distance_candidates([FAR, HALF_RANGE], {"Attack", "Projectile"}) == [5.0, 2.0]
    # without the line, or for a main skill that fires no projectile: as before
    assert conditions.distance_candidates([FAR], {"Attack", "Projectile"}) == [5.0, 2.0, 6.5]
    assert conditions.projectile_reach([HALF_RANGE], {"Spell"}) == (None, "")
    assert conditions.distance_candidates([FAR, HALF_RANGE], {"Spell"}) == [5.0, 2.0, 6.5]
    # a "further than 4m" line stays within reach
    assert conditions.distance_candidates(["20% more Damage against Enemies further than 4m", HALF_RANGE],
                                          {"Attack", "Projectile"}) == [5.0, 2.0, 4.5]
    assert "R29-2" in conditions.projectile_reach([HALF_RANGE], {"Projectile"})[1]


# "if you have Stunned an Enemy Recently" from the build's own Stuns of the boss ---------------------------------------

def test_stunned_an_enemy_recently_follows_the_main_skills_stuns():
    import math

    from builder.search import bosses

    life = bosses.of({}).life
    line = ["12% increased Area of Effect if you have Stunned an Enemy Recently"]
    stats = {"Speed": 2.0, "HitChance": 100.0, "CritChance": 0.0, "PhysicalHitAverage": 0.2 * life / 1.5,
             "HeavyStunBuildupAvg": 0.0}  # (Light Stuns only: 2 hits/s x 20% = 0.4 a second)
    got = conditions._stunned_enemy_recently(line, stats)
    assert got[2] == pytest.approx(1 - math.exp(-4 * 0.4), rel=1e-3) and "Light 0.400/s" in got[1]
    assert conditions._stunned_enemy_recently([], stats) == (0.0, "")  # (no line, no box)
    small = {**stats, "PhysicalHitAverage": 0.1 * life / 1.5}  # (10%: under the 15% floor, and no buildup)
    assert conditions._stunned_enemy_recently(line, small)[0] == 0.0
    assert conditions._stunned_enemy_recently(line, {**stats, "Flag:CannotStun": True})[0] == 0.0
    assert conditions.STUNNED_ENEMY_RECENTLY.search(
        "25% increased Area of Effect if you've Stunned an Enemy with a Two Handed Melee Weapon Recently")


# Nocturne: "Parried enemies take more Spell Damage instead of more Attack Damage" -------------------------------------

def test_nocturnes_parried_gives_an_attack_main_skill_nothing():
    parry = models.Skill("Parry", attack=True, cast_time=1.0)
    stats = {"enemySkillTime": 0.7}
    assert models.parry_plan([parry], stats, True).box > 0  # (as before, without the line)
    nocturne = {**stats, "_texts": ["Parried enemies take more Spell Damage instead of more Attack Damage"]}
    plan = models.parry_plan([parry], nocturne, True)
    assert plan.box == 0 and plan.lost == 0 and "Nocturne" in plan.how and "gains nothing" in plan.how
    spell = models.parry_plan([parry], nocturne, False)
    assert spell.box == 0 and "UNDERCOUNT" in spell.how


def test_the_empowered_buildup_lines_hand_pob_lines_it_parses(engine):
    """Probe: the hand lines the Empowered buildup hook gives PoB parse whole (PoB applies the build's own two lines
    not at all: the first unparsed, the second with " with Empowered Attacks " over)."""
    assert conditions.empowered_buildup(["Empowered Attacks have 50% increased Stun Buildup",
                                         "20% increased Freeze Buildup with Empowered Attacks",
                                         "10% increased Freeze Buildup with Empowered Attacks"]) == {"Stun": 50.0,
                                                                                                    "Freeze": 30.0}
    results = engine.call("parseLines", lines=["1% increased Stun Buildup", "1% increased Freeze Buildup",
                                               "Empowered Attacks have 50% increased Stun Buildup",
                                               "20% increased Freeze Buildup with Empowered Attacks"])["results"]
    print("PROBE empowered buildup", results)
    assert [r["supported"] for r in results] == [True, True, False, False]


# Moving: PoB's conditionMoving from the player's play (walking while using skills, the search's choice) --------------

SPRAY = ["12% increased Attack Damage while moving", "20% reduced Accuracy Rating while moving",
         "30% less Movement Speed Penalty from using Skills while moving"]


def test_moving_is_the_players_choice_and_never_with_standing_still():
    assert conditions.moving_candidates(SPRAY)
    assert not conditions.moving_candidates(["15% reduced Movement Speed Penalty from using Skills while moving"])
    assert conditions._moving(SPRAY, {}) [0] == 0.0  # (not chosen: standing while using skills)
    on = conditions._moving(SPRAY, {"_moving": True})
    assert on[0] is True and "without it" in on[1]
    assert conditions._moving(SPRAY, {"_stationary_off": "moving scored better"})[0] is True
    assert "dodge rolls (30%" in conditions._moving(SPRAY, {"_rolling": 0.3})[1]
    assert conditions._moving(["10% increased Spell Damage"], {"_moving": True}) == (0.0, "")
    still = ["20% increased Damage while Stationary"]
    assert conditions._standing_still(still, {})[0] is True
    assert conditions._standing_still(still, {"_moving": True}) == (0.0, "")  # (walking: not standing still)


def test_moving_switches_pobs_while_moving_lines_on_a_real_build(engine):
    """Probe (before / after): Spray and Pray (23736: 12% increased Attack Damage and 20% reduced Accuracy while moving).
    Before, nothing set PoB's conditionMoving: both lines counted 0; walking while attacking (the search's choice) turns
    them on: Accuracy falls, and the box is off again when the build stands."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("allocate", add=[23736], fields=[])
    search.settle()
    standing = (bool(build.call("getConfig")["input"].get("conditionMoving")), build.call("stats", fields=["MainHand.Accuracy", "HitChance"]))
    search.conditions.moving = True
    search.settle()
    walking = (bool(build.call("getConfig")["input"].get("conditionMoving")), build.call("stats", fields=["MainHand.Accuracy", "HitChance"]))
    search.conditions.moving = False
    search.settle()
    back = bool(build.call("getConfig")["input"].get("conditionMoving"))
    print("PROBE moving", standing, walking, back)
    assert not standing[0] and walking[0] and not back
    assert walking[1]["MainHand.Accuracy"] < standing[1]["MainHand.Accuracy"]

"""MECH72: Battershout (PoB holds no damage for its explosion and doesn't end the Full Break it Consumes): each use of
the supported Warcry on a Fully Broken boss Consumes the Full Break and explodes for 40% of the Broken Armour, 50% more
against a Unique (game files 26476662; patch notes 0.2.0g / 0.3.0 / 0.4.0)."""
from __future__ import annotations

import faulthandler

import pytest

from builder.search import conditions, models


def test_the_warcry_consumes_each_full_break_part_way_through():
    # 4 s to Break, 12 s held, a Warcry every 8 s: the Full Break is Consumed 4 s in on average, always in time
    up, rate, breaks, how = conditions._battershout_cycle(to_break=4.0, full=0.0, held=12.0, uses=1 / 8)
    assert up == pytest.approx(4.0 / 8.0) and rate == pytest.approx(1 / 8.0) and breaks == pytest.approx(1 / 8.0)
    assert "Battershout" in how
    # a Warcry every 24 s: half the Full Breaks run out first (E[min(U, 12)] = 12 - 144 / 48 = 9 s)
    up, rate, breaks, _ = conditions._battershout_cycle(4.0, 0.0, 12.0, 1 / 24)
    assert up == pytest.approx(9.0 / 13.0) and rate == pytest.approx(0.5 / 13.0)
    # nothing Breaks the Armour: no Full Break, nothing to Consume
    assert conditions._battershout_cycle(float("inf"), 0.0, 12.0, 1.0)[:3] == (0.0, 0.0, 0.0)


def test_the_consume_shortens_the_armour_broken_share():
    """_armour_broken with the Battershout Warcry's uses: the box's share falls (an OVERCOUNT guard), and the
    explosion and Full Break rates are published."""
    line = "Break 100 Armour on Hit"
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "_main_types": {"Attack"}, "_enemy_armour": 1200}
    plain = conditions._armour_broken([line], dict(stats))[0]  # 12 s to break, 12 s Broken: 50%
    shout_stats = {**stats, "_battershout_uses": 0.5}  # a Warcry every 2 s: Consumed 1 s in, a 13 s cycle
    shouted, basis = conditions._armour_broken([line], shout_stats)
    assert plain == pytest.approx(0.5) and shouted == pytest.approx(1 / 13) and "Battershout" in basis
    assert shout_stats["_battershout_rate"] == pytest.approx(1 / 13)
    assert shout_stats["_battershout_breaks"] == pytest.approx(1 / 13)


def test_an_explosion_is_pobs_hit_times_the_unique_bonus_at_the_consume_rate():
    dps, how = models.battershout_model(rate=0.2, hit=4000.0, armour=10000.0, how="cycle")
    assert dps == pytest.approx(0.2 * 4000.0 * 1.5) and "Unique" in how and "cycle" in how
    assert models.battershout_model(0.0, 4000.0, 10000.0, "")[0] == 0.0
    assert models.battershout_model(0.2, 0.0, 10000.0, "")[0] == 0.0


def test_battershout_wiring_asks_pob_for_the_hit_at_40_percent_of_the_armour():
    from builder.search import assumptions

    asked = {}

    class Build:
        battershout = (0.25, 8000.0, "cycle")

        def call(self, what, **kw):
            asked[what] = kw
            return {"groups": [{"index": 2, "warcry": "Infernal Cry", "AverageDamage": 3000.0}], "found": 1}

    dps, notes = assumptions.battershout(Build())
    assert asked["battershoutHit"] == {"base": pytest.approx(3200.0), "unbroken": True}
    assert dps == pytest.approx(0.25 * 3000.0 * 1.5) and notes[0]["label"].startswith("Battershout")

    class NoShout(Build):
        battershout = (0.0, 0.0, "")

    assert assumptions.battershout(NoShout()) == (0.0, [])


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_pob_scores_the_explosion_from_the_base_it_is_given_as_a_warcry(engine):
    """bridge.battershoutHit: the explosion's hit grows with its base, is 0 with none, and takes a Warcry damage line
    (patch 0.2.0g: it counts as a Warcry; PoB's own skill types don't say so)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Infernal Cry 20/0 1\nBattershout 1/0 1")
    small = build.call("battershoutHit", base=1000.0, unbroken=True)
    print(small)
    assert small["found"] >= 1
    row = small["groups"][-1]
    assert row["warcry"] == "Infernal Cry" and row["AverageDamage"] > 0
    big = build.call("battershoutHit", base=2000.0, unbroken=True)["groups"][-1]["AverageDamage"]
    assert big > 1.5 * row["AverageDamage"]
    assert build.call("battershoutHit", base=0.0, unbroken=True)["groups"][-1].get("AverageDamage", 0.0) == 0.0
    build.call("setAssumptions", mods=[models.line("50% increased Damage with Warcries", 1.0)], fields=[])
    warcry = build.call("battershoutHit", base=1000.0, unbroken=True)["groups"][-1]["AverageDamage"]
    print(row["AverageDamage"], big, warcry)
    assert warcry > 1.2 * row["AverageDamage"]


def test_a_partly_parsed_withered_line_still_gets_pobs_can_wither_flag():
    """MECH72: "Spells have a 25% chance to inflict Withered for 4 seconds on Hit" (The Wicked Quill) is a partial parse
    in PoB, so PoB never sets CanWither for it; wither_source_flags sets it for a Spell, not for an Attack."""
    quill = "Spells have a 25% chance to inflict Withered for 4 seconds on Hit"
    assert conditions.wither_source_flags([quill], {"Spell"}) == [models.raw_mod("Condition:CanWither", "FLAG", 1)]
    assert conditions.wither_source_flags([quill], {"Attack"}) == []
    assert conditions.wither_source_flags(["25% chance to inflict Withered for 2 seconds on Hit"], {"Attack"})
    assert conditions.wither_source_flags(["Minions have 10% chance to inflict Withered on Hit"], {"Spell"}) == []


def test_pob_gives_withered_stacks_no_effect_without_can_wither_and_the_flag_fixes_it(engine):
    """The probe for the rule "PoB already does X": a chaos Spell with 5 Withered stacks set, the Wicked Quill's line
    alone (PoB parses it only in part, so it sets no CanWither) moves nothing; with wither_source_flags' flag the stacks count."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Essence Drain", ascendancies=["Lich"]))
    skeleton = enumerate_skeletons(Context(payload), ["Essence Drain"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    quill = "Spells have a 25% chance to inflict Withered for 4 seconds on Hit"
    # (the bridge refuses the line as an assumption, since PoB can't parse it: on an item PoB keeps it unapplied)
    fields = ["CombinedDPS"]
    none = build.call("setConfig", vars={"multiplierWitheredStackCount": False}, fields=fields)["stats"]["CombinedDPS"]
    five = build.call("setConfig", vars={"multiplierWitheredStackCount": 5}, fields=fields)["stats"]["CombinedDPS"]
    build.call("setAssumptions", mods=conditions.wither_source_flags([quill], {"Spell"}),
               fields=[])
    flagged = build.call("stats", fields=fields)["CombinedDPS"]
    print(none, five, flagged)
    assert none > 0 and five == pytest.approx(none)
    assert flagged > 1.1 * none

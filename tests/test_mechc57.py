"""MECHC57's models (MECH-C): Rage regeneration from PoB's total (conditions.RAGE_REGEN_FIELD's note): Eternal Rage and
Furious Wellspring's Mana Regeneration increases were unread (UNDERCOUNTS; COV52 B1, RESEARCH53 R53-4)."""
import pytest

from builder.search import conditions as c

STATS = {"Speed": 2.0, "HitChance": 100, "MaximumRage": 40, "_main_types": {"Attack"}}
WELLSPRING = "Regenerate 6% of your maximum Rage per second"
PRIMAL = "Regenerate 1 Rage per second per 4 Rage spent Recently"


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


def test_regeneration_with_no_text_line_holds_the_maximum():
    """Eternal Rage has no line: PoB's total is the gain, and regeneration stops the decay (patch 0.4.0)."""
    held, how = c.rage_held([], {**STATS, "RageRegenRecovery": 4.5})
    assert held == 40 and "never decays" in how
    assert c.rage_held([], dict(STATS)) == (0.0, "")


def test_a_regeneration_line_is_not_counted_twice_beside_pobs_total():
    stats = {**STATS, "RageRegenRecovery": 3.6, "_rage_spend": {"Spender": 3.0}}
    held, how = c.rage_held([WELLSPRING], stats)
    assert held == 40 and "3.60 Rage/s gained" in how  # (2.4 from the line would be 6.0 if added)


def test_regeneration_below_the_spend_holds_none():
    assert c.rage_held([], {**STATS, "RageRegenRecovery": 3.0, "_rage_spend": {"Spender": 5.0}})[0] == 0


def test_primal_hunger_keeps_the_line_reader():
    """PoB reads "per 4 Rage spent Recently" as per 4 Rage held, so its total isn't used beside that line."""
    assert c.rage_regen_total([PRIMAL], {**STATS, "RageRegenRecovery": 9.0}) == 0
    assert c.rage_held([PRIMAL], {**STATS, "RageRegenRecovery": 9.0})[0] == 0  # (a refund with nothing gained)


def test_a_channelled_main_skill_says_the_reading_is_unverified():
    stats = {**STATS, "RageRegenRecovery": 8.0, "_main_channel_rage": True, "_rage_spend": {"Rampage": 5.0}}
    held, how = c.rage_held([], stats)
    assert held == 40 and "UNVERIFIED" in how and "0.00 Rage/s gained, none held" in how  # (the other reading)


def test_walking_calamity_regenerates_only_during_its_buff():
    """AUDIT37: its 300 Rage a minute, while the buff is up (from its Glory uptime); none without uptime."""
    assert c.CALAMITY_REGEN == 5
    assert c.calamity_rage(0, 40, 0, 0.0, 10) == 0
    assert c.calamity_rage(0, 40, 0, 1.0, 10) == pytest.approx((8 * 20 + 2 * 40) / 10)  # (climbs 8 s to 40, stays 2 s)
    assert c.calamity_rage(0, 40, 0, 0.5, 10) == pytest.approx(12)
    assert c.calamity_rage(10, 40, -6, 1.0, 10) == 10  # (spending more than it regenerates: no climb)
    assert c.rage_held([], dict(STATS, _calamity_up=(0.0, "x"))) == (0.0, "")
    held, how = c.rage_held([], dict(STATS, _calamity_up=(0.5, "from its Glory")))
    assert held == 12 and "Walking Calamity" in how


def test_probe_eternal_rage_regenerates_in_real_pob(engine):
    """Real PoB: Eternal Rage at level 20 gives RageRegenRecovery 5/s (300 a minute) and a Rage bar that stays full."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    before = build.call("stats", fields=["RageRegenRecovery"]).get("RageRegenRecovery") or 0
    build.set_group(gems=[{"name": "Eternal Rage", "level": 20, "quality": 0}])
    for _ in range(2):
        search.settle()
    got = build.call("stats", fields=["RageRegenRecovery", "MaximumRage"])
    print("PROBE Eternal Rage", before, got)
    assert before == 0 and got["RageRegenRecovery"] == pytest.approx(5.0, rel=0.3) and got["MaximumRage"] > 0
    held, how = c.rage_held([], {**STATS, **got})
    print("PROBE held", held, how)
    assert held == int(got["MaximumRage"])
    box = search.conditions.active.get("multiplierRage")
    print("PROBE box", box)
    assert box and float(box["value"]) == int(got["MaximumRage"])

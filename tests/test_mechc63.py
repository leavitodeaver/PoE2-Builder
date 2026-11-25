"""MECHC63: skills' upfront Rage costs counted as Rage spent (conditions.RAGE_COST_LINE's note)."""
from __future__ import annotations

import pytest

from builder.search import conditions as c

COST = "Pounce" + c.RAGE_COST_KEY


def test_rage_sawtooth_takes_upfront_costs_at_their_own_amount():
    """A cost spender is a per-use spender of its own amount; a spender spending nothing (a recast channel's 0/s) no
    longer stops the sawtooth; a per-second spender still does."""
    held, how = c.rage_sawtooth(3.84, {COST: 0.6, "Rampage": 0.0}, 95, each={COST: 3.0})
    assert 90 < held < 95 and "take 3 a use" in how
    assert c.rage_sawtooth(3.84, {COST: 0.6, "Rampage": 5.0}, 95, each={COST: 3.0}) is None
    # (costs beside a "consume 10" spender: each its own amount; with gains well above both, below the maximum)
    mixed, how = c.rage_sawtooth(12.0, {COST: 1.5, "Molten Crash": 5.0}, 30, each={COST: 3.0})
    alone, _ = c.rage_sawtooth(12.0, {"Molten Crash": 5.0}, 30)
    assert mixed < alone < 30 and "10 / 3" in how
    # (no costs: unchanged)
    assert c.rage_sawtooth(5.0, {"Molten Crash": 20.0}, 30)[1].startswith("the spenders take 10 a use")


def test_rage_held_counts_costs_above_the_gain():
    """Costs that outrun the gain: before, an unknown spender read none held; now the sawtooth's level (regeneration
    keeps it from decaying), below the maximum; with the gain above the costs, still below the maximum between spends."""
    base = {"MaximumRage": 30, "RageRegenRecovery": 2.0, "_rage_spend": {COST: 3.0}, "_rage_cost_each": {COST: 3.0}}
    held, basis = c.rage_held([], dict(base))
    assert 0 < held < 10 and "3.00/s spent by Pounce (Rage cost) 3.00/s" in basis
    rich, basis = c.rage_held([], {**base, "RageRegenRecovery": 6.0})
    assert held < rich < 30 and "below it between spends" in basis


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
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_real_pob_rage_cost_reaches_use_rates_and_the_spend(engine):
    """Real PoB: "Skills have +5 to Rage cost" (Furious Wellspring's line, PoB's RageCostBase) gives each pressed skill
    a rageCost in the bridge's useRates; Conditions._rage_costs spends it at the main skill's share of Speed; without
    the line, nothing."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    search = spark_with(engine, [])
    build = search.build
    main = build.call("groups")["mainSocketGroup"]
    assert not number(build.call("useRates", indices=[main])["rates"][0].get("rageCost"))
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                        "Skills have +5 to Rage cost", "+20 to Maximum Rage"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    search.settle()
    row = build.call("useRates", indices=[main])["rates"][0]
    assert number(row.get("rageCost")) == pytest.approx(5.0)
    stats = {"Speed": 2.0, "_main_share": 0.5}
    spend, each = search.conditions._rage_costs(stats)
    key = search.conditions.main_skill + c.RAGE_COST_KEY
    assert each[key] == pytest.approx(5.0) and spend[key] == pytest.approx(5.0 * 2.0 * 0.5)


def number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def test_rage_held_weighs_at_maximum_lines_by_the_share_at_the_maximum():
    """(RAGE_AT_MAX_LINE; the manager 13:27: a share, not a cut) Small costs against a fast refill: with a line naming
    "at maximum Rage" (Rage III) the box holds the maximum, weighed at the share of the time there, with the sawtooth's
    below-maximum average as its off value (Search.weigh_uptimes); without such a line, the average as before; under
    SWITCH_ON at the maximum, the below value alone."""
    base = {"MaximumRage": 95, "RageRegenRecovery": 3.8, "_rage_spend": {COST: 1.5}, "_rage_cost_each": {COST: 3.0}}
    out: dict = {}
    mean, _ = c.rage_sawtooth(3.8, {COST: 1.5}, 95, each={COST: 3.0}, out=out)
    assert 94 < mean < 95 and 0.5 < out["at_max"] < 1 and 91 < out["below"] < 95
    # (the average over the whole time is the two parts' blend)
    assert mean == pytest.approx(out["at_max"] * 95 + (1 - out["at_max"]) * out["below"], rel=1e-6)
    plain = c.rage_held([], dict(base))
    assert len(plain) == 2 and plain[0] < 95
    rage3 = "Supported Skills have signficantly higher Attack speed while you or supported minion are not at maximum Rage."
    held, basis, share, off = c.rage_held([rage3], dict(base))
    assert held == 95 and share == pytest.approx(out["at_max"]) and off == int(out["below"]) and off < 95
    assert "weighed at that share" in basis
    # (big costs, a slow refill: rarely at the maximum, under SWITCH_ON: the below value alone)
    poor = {**base, "RageRegenRecovery": 1.6, "_rage_spend": {COST: 1.5}}
    low: dict = {}
    c.rage_sawtooth(1.6, {COST: 1.5}, 95, each={COST: 3.0}, out=low)
    got = c.rage_held([rage3], dict(poor))
    if 0 < low["at_max"] < c.SWITCH_ON:
        assert len(got) == 2 and got[0] < 95 and "alone" in got[1]


def test_powered_by_verisium_quality_lengthens_its_own_infusions_in_the_pool():
    """(MECHC63: VERISIUM_DURATION_STAT, infusion_pool_seconds) The gem's quality "N% increased Verisium Infusion duration"
    comes back from verisium_infusions and lengthens its own share of a pool's timer; a pool without Verisium, and the
    element's own lines, as before."""
    import math
    from types import SimpleNamespace

    from builder.search import assumptions as a

    class B(SimpleNamespace):
        def call(self, name, **_):
            return {"skills": [{"name": a.VERISIUM, "levelStats": {a.VERISIUM_COUNT_STAT: 1,
                                                                    a.VERISIUM_DURATION_STAT: 50}}]}

    verisium = a.verisium_infusions(B(ward_sustain={a.VERISIUM: (0.2, 0.1, 155)}))
    assert verisium[0] == pytest.approx(0.1) and verisium[2] == 50 and "+50% (its quality)" in verisium[1]
    supply, _ = a.infusion_supply({a.VERISIUM: {}}, [], set(), verisium=verisium)
    assert supply["_verisium"] == (pytest.approx(0.1), 50)
    assert a.infusion_pool_seconds([], "", supply) == pytest.approx(30.0)  # (Verisium alone: 20 x 1.5)
    supply["Fire"] = 0.3
    assert a.infusion_pool_seconds([], "Fire", supply) == pytest.approx((0.3 * 20 + 0.1 * 30) / 0.4)
    # (a Verisium line lengthens only the Verisium part; an Elemental line all of it)
    lines = ["100% increased Verisium Infusion duration"]
    assert a.infusion_pool_seconds(lines, "Fire", supply) == pytest.approx((0.3 * 20 + 0.1 * 50) / 0.4)
    assert a.infusion_pool_seconds([], "Fire", {"Fire": 0.3, "Any": 0.0}) == 20.0
    assert a.infusion_pool_seconds(["Fire Infusions do not expire"], "Fire", supply) == pytest.approx(
        (0.3 * 30 + 0.1 * 30) / 0.4)  # (the never-expiring part at the longest finite one: a lower reading)
    assert a.infusion_pool_seconds(["Infusions do not expire"], "Fire", supply) == math.inf
    # (no Verisium quality: the 2-tuple some callers pass still works)
    plain, _ = a.infusion_supply({a.VERISIUM: {}}, [], set(), verisium=(0.1, "x"))
    assert a.infusion_pool_seconds([], "", plain) == pytest.approx(20.0)


def test_real_pob_powered_by_verisium_quality_reaches_verisium_infusions(engine):
    """Real PoB: Powered by Verisium 20/20's levelStats carry its quality's 50% increased Verisium Infusion duration
    (2.5 a point), and verisium_infusions gives it back with its Ward-paid casts."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    from builder.search import assumptions as a

    build = spark_with(engine, []).build
    build.call("addSkillGroup", text="Powered by Verisium 20/20 1")
    build.ward_sustain = {a.VERISIUM: (0.1, 0.05, 100)}
    rate, how, inc = a.verisium_infusions(build)
    assert inc == pytest.approx(50.0) and rate > 0 and "(its quality)" in how


def test_romiras_requital_recoups_what_its_companion_takes_for_you():
    """(recovery.COMPANION_REDIRECT_STAT) 200% of the 10% of hit damage the companion takes comes back as Life: 20% of the
    damage taken a second; nothing when no hit lands; only supported, enabled skills count, once a group."""
    from builder.search import recovery

    rows = [{"name": "Tame Beast", "group": 3, "supportStats": {recovery.COMPANION_REDIRECT_STAT: 10,
                                                                recovery.LOYALTY_RECOUP_STAT: 200}},
            {"name": "Tame Beast", "group": 3, "supportStats": {recovery.COMPANION_REDIRECT_STAT: 10,
                                                                recovery.LOYALTY_RECOUP_STAT: 200}},
            {"name": "Raging Spirits", "group": 4, "supportStats": {}},
            {"name": "Wolf", "group": 5, "disabled": True, "supportStats": {recovery.LOYALTY_RECOUP_STAT: 200}}]
    loyal = recovery.companion_recoups(rows)
    assert loyal == [(10.0, 200.0, "Tame Beast")]
    income = recovery.Income()
    recovery.hit_income(income, {"Life": 1000}, [], taken=500.0, companion_recoup=loyal)
    assert income.rates["LifeOnEventRecovery"] == pytest.approx(100.0)
    income = recovery.Income()
    recovery.hit_income(income, {"Life": 1000}, [], taken=0.0, companion_recoup=loyal)
    assert income.rates["LifeOnEventRecovery"] == 0 and "no boss hit lands" in income.parts[-1]


def test_real_pob_romiras_requital_stats_reach_support_stats(engine):
    """Real PoB: a companion skill supported by Romira's Requital carries its redirect and recoup stats in the bridge's
    supportStats, which companion_recoups reads."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    from builder.search import recovery

    build = spark_with(engine, []).build
    # (Wolf Pack, a Companion skill: in this skeleton PoB gave Summon Wolf no active-skill row: POE2T7174, probe 7177)
    build.call("addSkillGroup", text="Wolf Pack 20/0 1\nRomira's Requital 1/0 1")
    rows = build.call("activeSkills", supportStats=[recovery.COMPANION_REDIRECT_STAT,
                                                    recovery.LOYALTY_RECOUP_STAT])["skills"]
    assert (10.0, 200.0, "Wolf Pack") in recovery.companion_recoups(rows), [(r.get("name"), r.get("supportStats"))
                                                                             for r in rows]


def test_her_declaration_description_is_not_an_always_on_presence_intimidate():
    """(ENTERS_PRESENCE; MECH109's sweep, an OVERCOUNT) Her Declaration's description no longer reads as Intimidating the
    boss all fight while it stands in your Presence (her_declaration counts its entries); a plain "in your Presence"
    Intimidate line still does."""
    stats = {"PresenceRadius": 40, "_distance_choice": (2.0, "")}
    text = ("Supports Persistent Buff Skills, causing enemies that enter your Presence to become Intimidated while the "
            "Skill is active.")
    assert c._debuff_source(text, "conditionEnemyIntimidated", [text], stats) is None
    plain = "Enemies in your Presence are Intimidated"
    assert c._debuff_source(plain, "conditionEnemyIntimidated", [plain], stats)[0] == 1.0

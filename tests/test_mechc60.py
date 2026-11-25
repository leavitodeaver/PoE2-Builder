"""MECHC60 (U4, COV56's calibration of example build 11): Into the Breach's flame cadence from its own level, and the
supports' "chance to create an additional Remnant" (Harmonic Remnants II) on the supported skill's Remnants
(assumptions.breach_flame_rate / support_remnant_scale; an UNDERCOUNT before: nothing read the support's stat)."""
import faulthandler

import pytest

from builder.search import assumptions, recovery
from builder.search.assumptions import (BREACH, EXTRA_REMNANT_STAT, REMNANT_SOURCES, Assumptions, breach_flame_rate,
                                        support_remnant_scale)


def skill(name, level=None, support=None, disabled=False, **extra):
    return {"name": name, "disabled": disabled, "levelStats": level or {},
            "supportStats": {EXTRA_REMNANT_STAT: support} if support else {}, **extra}


def test_breach_rate_reads_its_own_level_and_falls_back_to_level_20():
    """1400 ms at level 1, 1060 at level 40 (IntoTheBreachPlayer's per-level stat); no level stat: level 20's 1210."""
    rate, how = breach_flame_rate([skill(BREACH, {"breach_flame_spawn_rate_ms": 1400})])
    assert rate == pytest.approx(1 / 1.4) and "1.40 s" in how
    rate, how = breach_flame_rate([skill(BREACH)])
    assert rate == pytest.approx(REMNANT_SOURCES[BREACH][0]) and "level 20" in how
    assert breach_flame_rate([skill(BREACH, disabled=True)])[0] == 0.0


def test_two_breach_copies_take_the_faster_one_not_both():
    """The tree's level-1 copy beside a socketed level-20 gem: one Breach at a time (skill_desired_amount_override 1)."""
    rate, _ = breach_flame_rate([skill(BREACH, {"breach_flame_spawn_rate_ms": 1400}),
                                 skill(BREACH, {"breach_flame_spawn_rate_ms": 1210})])
    assert rate == pytest.approx(1 / 1.21)


def test_harmonic_remnants_adds_to_the_text_lines_chance_not_on_top():
    """Harmonic Remnants II's 20% is the same stat as the passives' lines: (1 + g + s) / (1 + g) on the supported skill's
    rate, so with the text lines' 1 + g the total is 1 + g + s (not (1 + g)(1 + s))."""
    skills = [skill(BREACH, {"breach_flame_spawn_rate_ms": 1210}, support=20)]
    scale, support = support_remnant_scale(skills, BREACH, 0.2)
    assert support == pytest.approx(0.2) and scale == pytest.approx(1.4 / 1.2)
    rate, how = breach_flame_rate(skills, 0.2)
    assert rate * 1.2 == pytest.approx(1 / 1.21 * 1.4) and "+20%" in how
    assert support_remnant_scale([skill("Frost Bomb")], "Frost Bomb", 0.0) == (1.0, 0.0)  # (no support: unchanged)


def test_build_11_purple_flames_reach_the_cap_with_its_support():
    """Example build 11 (COV56's U4): Into the Breach 20 (+1 from Dialla's Desire: 1200 ms) with Harmonic Remnants II,
    its passives' +20% and Empowering Remnants' 15% double pickups, every flame Purple (Choice of Power). Before: 9.12
    of 10 stacks (the support unread, level 20's rate); now the cap, 10."""
    texts = ["10% chance to create an additional Remnant", "5% chance to create an additional Remnant",
             "5% chance to create an additional Remnant",
             "15% chance for Remnants you create to grant their effects twice",
             "All Flames of Chayula that you manifest are Purple"]
    skills = [skill(BREACH, {"breach_flame_spawn_rate_ms": 1200}, support=20),
              skill(BREACH, {"breach_flame_spawn_rate_ms": 1400})]

    class Build:
        def call(self, method, **kw):
            return {"sourceLines": {"lines": [{"text": t} for t in texts]},
                    "getConfig": {"input": {"purpleFlameStacks": 10}}}[method]

    hook = Assumptions.__new__(Assumptions)
    hook.build = Build()
    mods, notes = hook.purple_flames(skills)
    flames = 1 / 1.2 * 1.4 * 1.15  # (1 + 0.2 + 0.2) x 1.15
    assert assumptions.purple_stacks(flames) == 10.0
    assert mods == [] and notes[0]["value"].startswith("10.00 of 10")
    assert "+20% additional Remnants" in notes[0]["basis"] and "1.20 s" in notes[0]["basis"]
    skills[0]["supportStats"] = {}  # (without the support, level 21: 0.833 x 1.2 x 1.15 = 1.15/s, 9.2 stacks)
    _, notes = hook.purple_flames(skills)
    assert notes[0]["value"].startswith(f"{assumptions.purple_stacks(1 / 1.2 * 1.2 * 1.15):.2f} of 10")


def test_remnant_supply_scales_a_supported_source_only():
    """remnant_supply: a Frost Bomb with Harmonic Remnants II makes (1 + g + s) / (1 + g) of its Remnants; Into the
    Breach at its own level; Lingering Illusion's own 76% counted in the base it adds to."""

    class Build:
        def call(self, method, **kw):
            return {"sourceLines": {"lines": []}, "stats": {}}[method]

        def main_group(self):
            return None

    hook = Assumptions.__new__(Assumptions)
    hook.build = Build()
    hook.main_skill = lambda: None
    supply, used = hook.remnant_supply([skill("Frost Bomb", support=20, cooldownRecovery=1.0)], chance=0.0)
    assert supply == pytest.approx(REMNANT_SOURCES["Frost Bomb"][0] * 1.2)
    assert "+20% additional Remnants" in used[0]
    supply, _ = hook.remnant_supply([skill(BREACH, {"breach_flame_spawn_rate_ms": 1400})], chance=0.0)
    assert supply == pytest.approx(1 / 1.4)
    supply, _ = hook.remnant_supply([skill("Lingering Illusion", support=20)], chance=0.0)
    assert supply == pytest.approx(REMNANT_SOURCES["Lingering Illusion"][0] * 1.96 / 1.76)


def test_life_remnants_gain_their_supports_extra_remnants():
    """recovery.remnant_income: a Life Remnants info's "scale" (its supports' extra Remnants) on its own rate."""
    def run(scale):
        income = recovery.Income()
        recovery.remnant_income(income, {recovery.LIFE_REMNANTS: {"gain": 100.0, "levelStats": {}, "scale": scale}},
                                {"Life": 1000.0}, [], hits=2.0, crits=0.0, ailment_share=0.0, ailment_how="",
                                enemies=0.0)
        return income.rates["LifeRemnantRecovery"]

    assert run(1.2) == pytest.approx(1.2 * run(1.0))


def test_infusion_sources_gain_their_supports_and_the_builds_additional_remnants():
    """assumptions.infusion_supply: Frost Bomb with Harmonic Remnants II makes 20% more Cold Infusion Remnants; the
    build's "chance to create an additional Remnant" line (the same stat) adds to it; no line, no support: unchanged."""
    from builder.search.assumptions import infusion_supply

    base = REMNANT_SOURCES["Frost Bomb"][0]
    plain, _ = infusion_supply({"Frost Bomb": skill("Frost Bomb", cooldownRecovery=1.0)}, [], set())
    assert plain["Cold"] == pytest.approx(base)
    supported, used = infusion_supply({"Frost Bomb": skill("Frost Bomb", support=20, cooldownRecovery=1.0)},
                                      ["10% chance to create an additional Remnant"], set())
    assert supported["Cold"] == pytest.approx(base * 1.3)
    assert any("+30% additional Remnants" in u and "supports' 20%" in u for u in used)


def test_rage_sawtooth_holds_half_the_spend_not_zero():
    """(MECHC60, COV52's C list) A per-use spender spends 10 only while 10 are there: Molten Crash at 2 uses/s fed 5 Rage/s
    climbs 2.5 a use to 10 and drops to 0, 5 on average; a per-second channel (Rampage) isn't a sawtooth (None)."""
    from builder.search.conditions import rage_sawtooth

    held, how = rage_sawtooth(5.0, {"Molten Crash": 20.0}, 30)
    assert held == pytest.approx(5.0, abs=0.05) and "10 a use" in how
    assert rage_sawtooth(5.0, {"Rampage": 5.0}, 30) is None
    assert rage_sawtooth(0.0, {"Molten Crash": 20.0}, 30) is None
    # Rageforged II's 30% skip lets Rage climb past 10 more often: above the plain sawtooth, under 10 + a use's rise
    forged, _ = rage_sawtooth(5.0, {"Rageforged II": 12.2}, 30)
    assert 5.0 < forged < 10.0 + 5.0 / (12.2 / 7)
    # gains above the spend: below the maximum between spends, not at it
    high, _ = rage_sawtooth(12.0, {"Rageforged II": 3.0}, 30)
    assert 25.0 < high < 30.0


def test_rage_held_reads_the_sawtooth_with_per_use_spenders_only():
    """rage_held: Eternal Rage's 5/s (PoB's RageRegenRecovery) against Rageforged II asking 12.2/s: the sawtooth's average
    (was 0, 'spent as fast as it comes'); with gains above the spend, the sawtooth below the maximum (was the maximum)."""
    from builder.search.conditions import rage_held, rage_sawtooth

    base = {"Speed": 2.0, "HitChance": 100, "CritChance": 0, "MaximumRage": 30, "enemySkillTime": 0.7,
            "EvadeChance": 100, "_main_types": {"Attack", "Projectile"}, "RageRegenRecovery": 5.0}
    held, basis = rage_held([], {**base, "_rage_spend": {"Rageforged II": 12.2}})
    assert held == int(rage_sawtooth(5.0, {"Rageforged II": 12.2}, 30)[0]) and held > 5  # (whole Rage, as PoB's)
    assert "row, spent as fast as it comes" in basis
    high, basis = rage_held([], {**base, "RageRegenRecovery": 12.0, "_rage_spend": {"Rageforged II": 3.0}})
    assert 25 <= high < 30 and "row, the maximum all fight" in basis
    channel, _ = rage_held([], {**base, "_rage_spend": {"Rampage": 5.0}})
    assert channel == 0  # (a per-second spender: none held, as before)


def test_rageforged_more_damage_at_the_paid_share():
    """Assumptions.rageforged: PoB gives Rageforged's 35% only with its Rage box at 10 or more; the game on the uses that
    find 10 Rage. Box 6.4, 41% paid: +14.4% more (was none); box 30, 41% paid: x(1.144 / 1.35); all paid at 30: nothing."""
    class Build:
        def __init__(self, box, share):
            self.rage_paid = {"share": share, "paid": 5.0, "asked": 5.0 / share if share else 12.2}
            self.box = box

        def main_group(self):
            return {"gems": [{"name": "Twister", "support": False}, {"name": "Rageforged II", "support": True}]}

        def call(self, method, **kw):
            return {"getConfig": {"input": {"multiplierRage": self.box}}}[method]

    def run(box, share):
        hook = Assumptions.__new__(Assumptions)
        hook.build = Build(box, share)
        return hook.rageforged("Twister")

    mods, notes = run(6.4, 0.41)
    assert mods[0]["value"] == pytest.approx(35 * 0.41, rel=1e-3) and "PoB's box: 0%" in notes[0]["value"]
    mods, _ = run(30, 0.41)
    assert mods[0]["value"] == pytest.approx(((1 + 0.35 * 0.41) / 1.35 - 1) * 100, rel=1e-3)
    assert run(30, 1.0) == ([], [])


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


def test_real_pob_gives_the_breach_level_stat_and_the_support_chance(engine):
    """Real PoB (the bridge's activeSkills): Into the Breach's breach_flame_spawn_rate_ms at its level and Harmonic
    Remnants II's chance in supportStats, and the Purple Flame note reads both (level 1: 1.40 s; +20%)."""
    from test_engine_models import spark_with  # (tests/ is on sys.path under pytest)

    search = spark_with(engine, [])
    build = search.build
    build.call("addSkillGroup", text="Into the Breach 1/0 1\nHarmonic Remnants II 1/0 1")
    got = [s for s in build.call("activeSkills", levelStats=[BREACH], supportStats=[EXTRA_REMNANT_STAT])["skills"]
           if s["name"] == BREACH]
    assert got and got[0]["levelStats"].get("breach_flame_spawn_rate_ms") == 1400
    assert got[0]["supportStats"].get(EXTRA_REMNANT_STAT) == 20
    search.settle()
    note = next(n for n in search.assumptions.active if n["label"] == "Into the Breach: Purple Flame stacks*")
    assert "1.40 s" in note["basis"] and "+20% additional Remnants" in note["basis"]
    assert note["value"].startswith(f"{assumptions.purple_stacks(1 / 1.4 * 1.2 / 3):.2f} of 10")

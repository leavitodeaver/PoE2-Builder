"""MECH85 (MECH-A): the mechanics catalog's MECH-A entries with PoB status none / partial (MECH84's ranked tranche).
Unit cases need no PoB; the `engine` ones are the before / after probes (real PoB, in the queue)."""
import pytest

from builder.search import conditions


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


def _skeleton_search(engine, main: str, ascendancy: str):
    from builder.request.schema import BuildRequest, Choices
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=main, ascendancies=[ascendancy], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), [main])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


# Self Immolation: "Ignites you cause are reflected back to you" ---------------------------------------------------

def _ignite_stats(**over):
    stats = {"IgniteChancePerHit": 40.0, "Speed": 2.0, "HitChance": 100.0, "IgniteDuration": 4.0,
             "IgniteAvoidChance": 0.0, "SelfIgniteDuration": 100.0, "IgniteDPS": 600.0, "IgniteEffMult": 0.6,
             "SelfIgniteEffect": 60.0}
    stats.update(over)
    return stats


def test_self_immolation_ignites_you_at_the_bosses_ignited_share():
    line = ["Ignites you cause are reflected back to you"]
    stats = _ignite_stats()
    boss = conditions.ailment_uptime(stats, "Ignite")
    up, how = conditions.reflected_ignite_share(line, stats)
    assert 0 < boss <= 1 and up == pytest.approx(boss) and "Self Immolation" in how
    assert conditions.reflected_ignite_share([], stats)[0] == 0  # (no line, nothing)
    half, _ = conditions.reflected_ignite_share(line, _ignite_stats(IgniteAvoidChance=50.0))
    assert 0 < half < up  # (avoidance over the applications in one Ignite's duration)
    assert conditions.reflected_ignite_share(line, _ignite_stats(IgniteAvoidChance=100.0))[0] == 0
    assert conditions.reflected_ignite_share(line, _ignite_stats(IgniteChancePerHit=0.0))[0] == 0


def test_self_immolation_burns_you_at_your_ignite_before_the_bosses_mitigation():
    line = ["Ignites you cause are reflected back to you"]
    stats = _ignite_stats()
    up, _ = conditions.reflected_ignite_share(line, stats)
    burn, how = conditions.reflected_ignite_burn(line, stats)
    assert burn == pytest.approx(600.0 / 0.6 * 0.6 * up)  # (1,000 a second before the boss's mitigation, x 60% on you)
    assert "without the line: none" in how
    assert conditions.reflected_ignite_burn([], stats)[0] == 0


def test_self_immolation_turns_the_ignited_yourself_box_on():
    line = ["Ignites you cause are reflected back to you", "10% increased Damage while Ignited"]
    on, basis, up = conditions._self_ignited(line, _ignite_stats())
    assert on and up > 0.5 and "Self Immolation" in basis


def test_self_immolation_burns_a_real_build(engine):
    """Probe (before / after): Fireball with the Self Immolation notable (23630). Before MECH85 PoB left the line
    unparsed: no Fire degen on you; now the models hand PoB the reflected Ignite's burn as Fire damage taken a second."""

    from builder.search.assumptions import Assumptions

    search = _skeleton_search(engine, "Fireball", "Stormweaver")
    build = search.build

    def read():
        mods, notes, _ = Assumptions(build).models(build.call("activeSkills")["skills"])
        note = next((n for n in notes if str(n.get("label", "")).startswith("Your Ignites reflected back")), None)
        burn = [m for m in mods if m.get("line") == "1 Fire Damage taken per second"]
        return note, burn

    before_note, before_burn = read()
    build.call("allocate", add=[23630], fields=[])  # Self Immolation, with its path
    seen = build.call("stats", fields=list(conditions.REFLECTED_IGNITE_FIELDS) + ["FireBuildDegen", "TotalBuildDegen"])
    note, burn = read()
    build.call("setAssumptions", mods=burn, fields=[])
    after = build.call("stats", fields=["FireBuildDegen", "TotalBuildDegen", "FireResist"])
    build.call("setAssumptions", mods=[], fields=[])
    print("PROBE self immolation", seen, note, burn, after)
    assert before_note is None and not before_burn
    assert note is not None and seen["SelfIgniteEffect"] <= 61  # (the notable's 40% reduced; probe: 45 with its path)
    if seen.get("IgniteDPS"):
        assert burn and burn[0]["scale"] > 0 and after["FireBuildDegen"] > number0(seen.get("FireBuildDegen"))


def number0(v):
    return float(v or 0)


# Endless Circuit: "25% chance on Consuming a Shock on an Enemy to reapply it" ---------------------------------------

def test_endless_circuit_keeps_a_consumed_shock_longer():
    line = ["25% chance on Consuming a Shock on an Enemy to reapply it"]
    assert conditions.consume_reapply_chances(line) == {"Shock": 0.25}
    assert conditions.consume_reapply_chances(line * 5) == {"Shock": 0.99}  # (capped below 100%)
    assert conditions.consume_reapply_chances(["25% chance to Shock"]) == {}
    plain = conditions.consumed_share(2.0, 0.5)
    kept = conditions.consumed_share(2.0, 0.5, kept=0.25)
    last = 0.5 / 0.75
    assert kept == pytest.approx(last / (last + conditions.models.CONSUME_IMMUNE + 1.0 / (2.0 * 0.5)))
    assert plain < kept < 1
    stats = {"ShockChancePerHit": 50.0, "Speed": 2.0, "HitChance": 100.0, "ShockDuration": 4.0,
             "_consumes": {"Shock": "Lightning Conduit"}}
    before = conditions.ailment_uptime(stats, "Shock")
    after = conditions.ailment_uptime({**stats, "_consume_reapply": {"Shock": 0.25}}, "Shock")
    assert before == pytest.approx(plain) and after == pytest.approx(kept)
    assert conditions.shock_share({**stats, "_consume_reapply": {"Shock": 0.25}}) == pytest.approx(kept)


def test_endless_circuit_slows_a_side_consumer_too():
    stats = {"_side_consumes": {"Shock": (1.0, "another skill")}}
    lost = conditions.side_consumed(0.8, stats, "Shock", 2.0, 0.5)
    kept = conditions.side_consumed(0.8, {**stats, "_consume_reapply": {"Shock": 0.25}}, "Shock", 2.0, 0.5)
    assert lost < kept < 0.8
    assert kept == pytest.approx(0.8 / (1 + 0.8 * 0.75 * (conditions.models.CONSUME_IMMUNE + 1.0)))


# Frostwalker / Flamewalker: "Gain 15% of Damage as Extra <X> Damage while on Chilled / Ignited Ground" ----------------

def test_you_on_your_own_chilled_or_ignited_ground_follow_the_grounds_share_within_its_radius():
    frost = ["Gain 15% of Damage as Extra Cold Damage while on Chilled Ground"]
    flame = ["Gain 15% of Damage as Extra Fire Damage while on Ignited Ground"]
    near = {"_distance_choice": (1.0, "melee"), "_chilled_ground": (0.6, "Frost Wall"), "_ignited_ground": (0.5, "x")}
    on, basis, up = conditions._on_own_ground("Chilled")(frost, near)
    assert on and up == pytest.approx(0.6) and "without it: off" in basis
    on, _, up = conditions._on_own_ground("Ignited")(flame, near)
    assert on and up == pytest.approx(0.5)
    assert conditions._on_own_ground("Chilled")(flame, near)[0] == 0  # (the line decides which ground)
    far = {**near, "_distance_choice": (8.0, "ranged")}
    off, why = conditions._on_own_ground("Ignited")(flame, far)
    assert off == 0 and "outside your Ignited Ground" in why and "50%" in why  # (the standing-in-it row)
    none, why = conditions._on_own_ground("Chilled")(frost, {"_distance_choice": (1.0, "melee")})
    assert none == 0 and "no Chilled Ground of your own" in why


# Crushing Blows and Primed-for-Pin Pins: Heavy Stun / Pin once the boss is Primed (70% of the bar, a Unique) ----------

from builder.search import models  # noqa: E402


def _stun_stats(**over):
    stats = {"HeavyStunBuildupAvg": 4.0, "PinBuildupAvg": 4.0, "Speed": 2.0, "HitChance": 100.0, "CritChance": 0.0,
             "PhysicalHitAverage": 100.0}
    stats.update(over)
    return stats


def test_primed_hit_refill_is_seventy_percent_plus_the_next_hit_never_more_than_the_full_bar():
    assert models.primed_hit_refill(10.0, 2.0) == pytest.approx(7.5)
    assert models.primed_hit_refill(1.0, 2.0) == pytest.approx(1.0)  # (0.7 + 0.5 > 1: the full refill)
    assert models.primed_hit_refill(10.0, 0.0) == pytest.approx(7.0)


def test_crushing_blows_heavy_stun_the_boss_sooner():
    plain, _ = models.heavy_stun_rate(_stun_stats())
    crushed, how = models.heavy_stun_rate(_stun_stats(_crushing=(True, "test")))
    assert 0 < plain < crushed and "Primed at 70%" in how
    assert models.heavy_stun_rate(_stun_stats(_main_skill="Boneshatter"))[0] == pytest.approx(crushed)
    assert models.heavy_stun_rate(_stun_stats(_texts=["Your Hits are Crushing Blows"]))[0] == pytest.approx(crushed)
    assert models.heavy_stun_rate(_stun_stats(_crushing=(False, ""), _main_skill="Boneshatter"))[0] == pytest.approx(plain)
    assert models.crushing_source([], "Spark") == (False, "")


def test_primed_for_pinning_pins_the_boss_sooner():
    plain = models.immobilise_period(_stun_stats(), "Pin")
    primed = models.immobilise_period(_stun_stats(_pin_primed=True), "Pin")
    by_line = models.immobilise_period(_stun_stats(_texts=["Pin Enemies which are Primed for Pinning"]), "Pin")
    assert 0 < primed < plain and by_line == pytest.approx(primed)
    assert models.immobilised_share(_stun_stats(_pin_primed=True), "Pin")[0] > models.immobilised_share(_stun_stats(), "Pin")[0]


def test_crushing_blows_on_a_real_build(engine):
    """Probe (before / after): Staggering Palm's hits are Crushing Blows (the game's crushing_blow). With PoB's own
    Heavy Stun buildup, the boss is Heavy Stunned sooner once the bar need only reach 70% (Primed, a Unique)."""
    search = _skeleton_search(engine, "Staggering Palm", "Invoker")
    build = search.build
    stats = {**build.call("stats", fields=models.MODEL_FIELDS), **models.dazed_stats(build)}
    before, before_how = models.heavy_stun_rate({**stats, "_crushing": (False, "")})
    after, after_how = models.heavy_stun_rate({**stats, "_crushing": models.crushing_source([], "Staggering Palm")})
    print("PROBE crushing", stats.get("HeavyStunBuildupAvg"), stats.get("Speed"), before, before_how, "|", after, after_how)
    assert models.crushing_source([], "Staggering Palm")[0]
    if stats.get("HeavyStunBuildupAvg"):
        assert after > before > 0 and "Primed at 70%" in after_how


# Swift Interruption: "if you've successfully Parried Recently" -------------------------------------------------------

def test_parried_recently_follows_the_parry_plan():
    plan = models.ParryPlan(0.3, 0.15, "Parry the boss's telegraphed hits (test)")
    on, basis, up = conditions._parried_recently([], {"_parry_plan": plan})
    assert on and up == pytest.approx(4.0 / models.PARRY_EVERY) and "without parrying: off" in basis
    refute = models.ParryPlan(0.3, 0.05, "Refutation on its cooldown (test)")
    assert conditions._parried_recently([], {"_parry_plan": refute})[0] == 0
    assert conditions._parried_recently([], {"_parry_plan": models.ParryPlan(0.0, 0.0, "x: not used")})[0] == 0
    assert conditions._parried_recently([], {}) == (0.0, "")


# Bashing Beast and other "on Heavy Stun" debuffs: at the main skill's Heavy Stuns ----------------------------------

def test_debuffs_on_heavy_stun_follow_the_heavy_stun_rate():
    line = "Enemies you Heavy Stun while Shapeshifted are Intimidated for 6 seconds"
    var = "conditionEnemyIntimidated"
    stats = _stun_stats(_main_types={"Attack", "Bear", "Shapeshift"})
    rate, _ = models.heavy_stun_rate(stats)
    up, how = conditions._debuff_source(line, var, [line], stats)
    assert rate > 0 and up == pytest.approx(min(1.0, rate * 6.0)) and "without Heavy Stuns: 0" in how
    human = conditions._debuff_source(line, var, [line], _stun_stats(_main_types={"Attack"}))
    assert human[0] == 0 and "Shapeshift" in human[1]
    plain = "Intimidate Enemies on Heavy Stun"
    assert conditions._debuff_source(plain, var, [plain], _stun_stats(_main_types={"Attack"}))[0] > 0
    assert conditions._debuff_source(plain, var, [plain], _stun_stats(HeavyStunBuildupAvg=0.0))[0] == 0

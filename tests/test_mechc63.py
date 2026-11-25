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


def test_rage_held_reads_the_maximum_for_at_maximum_lines_when_it_is_the_common_state():
    """(RAGE_AT_MAX_LINE) Small costs against a fast refill: the build sits at its maximum most of the time, so with a
    line naming "at maximum Rage" (Rage III) the held Rage is the maximum (PoB's condition on), the average a row;
    without such a line, the average as before."""
    base = {"MaximumRage": 95, "RageRegenRecovery": 3.8, "_rage_spend": {COST: 1.5}, "_rage_cost_each": {COST: 3.0}}
    out: dict = {}
    mean, _ = c.rage_sawtooth(3.8, {COST: 1.5}, 95, each={COST: 3.0}, out=out)
    assert 94 < mean < 95 and out["at_max"] > 0.5
    plain, _ = c.rage_held([], dict(base))
    assert plain < 95
    rage3 = "Supported Skills have signficantly higher Attack speed while you or supported minion are not at maximum Rage."
    held, basis = c.rage_held([rage3], dict(base))
    assert held == 95 and "the more common state, the maximum" in basis

"""The what-if limit's gate: for every custom model, what a what-if says a move is worth must agree with committing the move
and resolving, within a stated tolerance. One test across all models, so a new model can't ship invisible to what-ifs
(builder/search/run.py MODEL_NAMES lists them; each needs a scenario here or a reason PoB's own what-ifs see it)."""

from __future__ import annotations

import faulthandler
import time

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.run import MODEL_NAMES
from builder.search.skeletons import enumerate_skeletons

TOLERANCE = 0.01  # what-if within 1% of the committed, resolved score

# Models whose inputs are passive lines turned into PoB conditions and multipliers (flag:/mult:), which PoB's own node
# what-ifs re-read, or that a scenario can't reach on a test skeleton (the reason says which)
SEEN_BY_POB = {
    "archon": "passive inputs (mult:/flag:); Stormweaver Spark what-if and commit agree (tests/test_engine_models.py)",
    "energy": "passive inputs and the meta gem's group; gem what-ifs re-resolve",
    "darkness": "computed from PoB's own Darkness stat in every score (Objective.prepare)",
    "traveller": "an attribute choice the attribute round tries for real",
    "elemental_expression": "trigger rate from crits; the expression group is in Full DPS",
    "leylines": "applied in the bridge for every calculation",
    "deadly_resolve": "a trigger rate from the boss's hits and your evade and block at each resolve: a what-if that changes them counts from the next resolve",
    "harmony": "passive flag (flag:HarmonyWithin); the bridge tests Mana > Life again on every calculation",
    "guided_hand": "passive flag (flag:GuidedHand); the bridge works the boosted share out again from each what-if's speed",
    "sanguine": "passive flag (flag:SanguineTides); the bridge works the gain out again from each what-if's Life cost",
}


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def spark(engine, extra_skills=()) -> Search:
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"],
                                        require=Choices(skills=list(extra_skills))))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def unique_raw(search: Search, name: str) -> str:
    return next(u["raw"] for u in search.build.ctx.data.uniques if u["name"] == name)


def rare(base: str, *lines: str) -> str:
    return "\n".join(["Rarity: Rare", "Test Item", base, "Item Level: 82", "Implicits: 0", *lines])


def agree(search: Search, slot: str, raw: str) -> tuple[float, float]:
    """(what-if score, committed and resolved score) of putting raw in slot."""
    build = search.build
    (what_if, _), = build.item_deltas([{"slot": slot, "raw": raw}])
    build.call("equip", slot=slot, raw=raw, fields=[])
    search.resolve()
    return what_if, build.refresh()


def scenario_resist_rings(engine):
    search = spark(engine)
    search.build.call("equip", slot="Ring 1", raw=unique_raw(search, "Safrin's Resolve"), fields=[])
    search.resolve()
    return agree(search, "Helmet", rare(_base(search, "Helmet"), "+30% to Fire Resistance", "+80 to maximum Energy Shield"))


def scenario_gem_stat_uptime(engine):
    search = spark(engine, ["Mana Tempest"])
    return agree(search, "Amulet", rare("Gold Amulet", "+1000 to maximum Mana", "Regenerate 400 Mana per second"))


def scenario_maelstrom(engine):
    search = spark(engine)
    return agree(search, "Flask 2", unique_raw(search, "Melting Maelstrom"))


def _base(search: Search, item_type: str) -> str:
    return next(b["name"] for b in search.build.ctx.data.bases if b["type"] == item_type and not b.get("hidden")
                and (b.get("armour") or {}).get("EnergyShield"))


SCENARIOS = {"resist_rings": scenario_resist_rings, "gem_stat": scenario_gem_stat_uptime, "maelstrom": scenario_maelstrom}


def test_every_model_is_seen_by_what_ifs_or_says_why():
    covered = set(SCENARIOS) | set(SEEN_BY_POB)
    missing = set(MODEL_NAMES) - covered
    assert not missing, f"models with neither a what-if scenario nor a reason PoB's what-ifs see them: {missing}"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_what_if_agrees_with_commit(engine, name):
    what_if, committed = SCENARIOS[name](engine)
    print(f"{name}: what-if {what_if:.4f}, committed {committed:.4f}")
    assert abs(what_if - committed) <= TOLERANCE * max(abs(committed), 1.0), (name, what_if, committed)


def test_rescoring_cost(engine):
    """The cost of the model inputs: item what-ifs per second on a build with a model on its own numbers (Mana Tempest's
    uptime), worked out in the bridge (today), against the real trials it needed before (models_on_stats); and the
    bridge's score against the trial's."""
    search = spark(engine, ["Mana Tempest"])
    build = search.build
    cases = [{"slot": "Amulet", "raw": rare("Gold Amulet", f"+{n} to maximum Mana", f"Regenerate {n} Mana per second")}
             for n in range(10, 210, 10)]

    def timed() -> tuple[float, list[float]]:
        started = time.perf_counter()
        scores = [score for score, _ in build.item_deltas(cases)]
        return time.perf_counter() - started, scores

    in_bridge, bridge_scores = timed()
    build.models_on_stats = True  # the old way: the best few what-ifs get a real trial
    trials, trial_scores = timed()
    build.models_on_stats = False
    print(f"\n20 item what-ifs: {in_bridge * 1000:.0f} ms with the inputs in the bridge ({len(cases) / in_bridge:.0f}/s), "
          f"{trials * 1000:.0f} ms with up to {build.RESCORE_TOP} real trials ({len(cases) / trials:.0f}/s)")
    best = max(range(len(cases)), key=lambda i: trial_scores[i])  # the best is always among the trials
    assert bridge_scores[best] == pytest.approx(trial_scores[best], rel=TOLERANCE)
    assert in_bridge < trials

"""Build._rescore_model_items' real trials (PERF23): between two trials the old item goes back without a recalculation
of its own (bridge.equip's `recalc = false`); the next trial's equip recalculates the whole build. The scores, the
stats and the build left behind must be what they are when every restore recalculates."""
from __future__ import annotations

import faulthandler
import json

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons


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


def rare(base: str, *lines: str) -> str:
    return "\n".join(["Rarity: Rare", "Test Item", base, "Item Level: 82", "Implicits: 0", *lines])


def left_behind(build) -> str:
    return json.dumps({"xml": build.call("exportXml")["xml"], "stats": build.call("stats"),
                       "equipped": build.call("equipped")["slots"], "groups": build.call("groups"),
                       "score": build.score, "search stats": build.stats}, sort_keys=True, default=repr)


def test_trials_put_the_old_item_back_without_a_recalculation_between(engine):
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"],
                                        require=Choices(skills=["Mana Tempest"])))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.models_on_stats = True  # every candidate counts as read by a model: the best RESCORE_TOP get a real trial
    cases = [{"slot": "Amulet", "raw": rare("Gold Amulet", f"+{n} to maximum Mana", f"Regenerate {n} Mana per second")}
             for n in range(10, 210, 10)]
    real, equips = build.call, []

    def every_restore_recalculates(method, **kwargs):
        if method == "equip":
            equips.append(kwargs.pop("recalc", True))
        return real(method, **kwargs)

    def counted(method, **kwargs):
        if method == "equip":
            equips.append(kwargs.get("recalc", True))
        return real(method, **kwargs)

    try:
        build.call = every_restore_recalculates
        old_way = build.item_deltas(cases)
        old_left = left_behind(build)
        asked = list(equips)
        equips.clear()
        build.call = counted
        build.models_on_stats = True  # (the models' own resolve, in the first run's trials, sets it back)
        new_way = build.item_deltas(cases)
        new_left = left_behind(build)
    finally:
        del build.call
        build.models_on_stats = False
    trials = build.RESCORE_TOP
    assert asked == [True, False] * (trials - 1) + [True, True]  # what the search asks for
    assert equips == asked  # ... and the second run asked the same, this time honoured
    assert json.dumps(new_way, sort_keys=True, default=repr) == json.dumps(old_way, sort_keys=True, default=repr)
    assert new_left == old_left

"""Required and banned passives follow the names a class sees: 59 start-area nodes become another
passive for some classes (PoB's PassiveSpec swaps in the node's option for the class, else for the
ascendancy). Node 30695 is Eagle Eye for a Huntress and Vile Wounds otherwise; node 19104 is Reflex
Action for a Huntress and Eagle Eye otherwise."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload

HUNTRESS_EAGLE_EYE, OTHERS_EAGLE_EYE = 30695, 19104


def context(**body) -> Context:
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    return Context(make_payload(BuildRequest(**body)))


def test_banned_passives_are_matched_by_the_name_the_class_sees():
    ctx = context(forbid={"passives": ["Eagle Eye"]})
    huntress = ctx.forbidden_nodes("Huntress", "Ritualist")
    assert HUNTRESS_EAGLE_EYE in huntress and OTHERS_EAGLE_EYE not in huntress
    ranger = ctx.forbidden_nodes("Ranger", "Deadeye")
    assert OTHERS_EAGLE_EYE in ranger and HUNTRESS_EAGLE_EYE not in ranger
    node = ctx.nodes[HUNTRESS_EAGLE_EYE]
    assert ctx.node_as(node, "Huntress")["name"] == "Eagle Eye" and ctx.node_as(node, "Ranger")["name"] == "Vile Wounds"


@pytest.fixture(scope="module")
def engine():
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


def test_a_huntress_requiring_eagle_eye_gets_her_eagle_eye(engine):
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], require={"passives": ["Eagle Eye"]})
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    assert HUNTRESS_EAGLE_EYE in search.tree.required and OTHERS_EAGLE_EYE not in search.tree.required
    passives = {p["name"] for p in engine.call("facts", fields=[])["passives"]}
    assert "Eagle Eye" in passives and "Reflex Action" not in passives

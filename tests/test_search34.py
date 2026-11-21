"""SEARCH34: open support sockets (gems.fill_open / open_sockets). The user, 20 Nov: an empty socket is a red flag the
search must justify by score (Uhtred's Augury and Spirit kept for the other Spirit skills are examples, not a list):
every open socket gets real trials, and each one left open is a warning with its reason."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search.context import Context, make_payload


@pytest.fixture(scope="module")
def ctx():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    return Context(make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices())))


@pytest.fixture(scope="module")
def engine(ctx):
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def _search(ctx, engine):
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def test_count_rule_reads_uhtreds_augury_from_its_game_text(ctx):
    from builder.search.gems import Gems

    gems = Gems.__new__(Gems)
    gems.ctx = ctx
    assert "exactly two other supports" in (gems.count_rule("Uhtred's Augury") or "")
    assert gems.count_rule("Controlled Destruction") is None


def test_open_sockets_get_real_trials_and_each_left_open_says_why(ctx, engine):
    search = _search(ctx, engine)
    build, gems = search.build, search.gems
    group = build.main_group()
    index = group["index"]
    spark = group["gems"][0]["name"]
    gems.commit(index, [spark])  # the main group bare: five open sockets
    before = build.score
    gems.fill_open(float("inf"))
    assert build.score >= before - 1e-9
    after = next(g for g in build.groups() if g["index"] == index)
    assert sum(g["support"] for g in after["gems"]) > 0, "a bare Spark: some support scores higher than empty"
    for o in gems.open_sockets():
        assert o["reasons"], o  # every open socket says why
    rows = gems.open_socket_rows(gems.open_sockets())
    assert all("WARNING" in r["label"] and r["counted"] is False for r in rows)


def test_uhtreds_group_is_tried_without_it_and_its_rule_is_the_reason(ctx, engine):
    search = _search(ctx, engine)
    build, gems = search.build, search.gems
    group = build.main_group()
    index = group["index"]
    spark = group["gems"][0]["name"]
    others = [c for c in gems.candidates(index, build.groups()) if c != "Uhtred's Augury"
              and not gems.families(c) & gems.families("Uhtred's Augury")][:2]
    # (Uhtred's Augury itself needn't be offered here, e.g. unpriced: the group is set by hand, as a search could leave it)
    assert len(others) >= 2
    gems.commit(index, [spark, "Uhtred's Augury", *others])
    before = build.score
    gems.fill_open(float("inf"))
    assert build.score >= before - 1e-9
    entry = next(e for (i, _), e in gems.fill_log.items() if i == index)
    assert "Uhtred's Augury" in entry["dropped"], "the group without Uhtred's Augury, filled, was scored"
    for o in gems.open_sockets():
        if o["group"] == index and "Uhtred's Augury" in o["supports"]:
            assert any("exactly two other supports" in r for r in o["reasons"])


def test_overwhelming_presence_gives_the_distance_choice_a_presence_line(ctx, engine, monkeypatch):
    """(MECHC48's routed item) Overwhelming Presence's debuff is off for a boss outside the Presence (assumptions'
    OverwhelmingOutside), so choose_distance offers the Presence edge for it, as for Blasphemy."""
    from builder.search import inner
    from builder.search.conditions import PRESENCE_LINE

    search = _search(ctx, engine)
    build = search.build
    seen = []
    real = inner.distance_candidates

    def spy(lines, *args, **kwargs):
        seen.append(list(lines))
        return real(lines, *args, **kwargs)

    monkeypatch.setattr(inner, "distance_candidates", spy)
    search.choose_distance()
    assert not any(l.startswith("Overwhelming Presence") for l in seen[-1])
    build.set_group(gems=[{"name": "Overwhelming Presence", "level": 20, "quality": 0}], label="test")
    search.choose_distance()
    assert any(l.startswith("Overwhelming Presence") and PRESENCE_LINE.search(l) for l in seen[-1])



def test_the_phylactery_is_a_jewel_slot_for_a_plain_rare_only(engine):
    """(SEARCH34; PLANS6 kw-basic-jewel, the manager's 03:05 / 03:25 notes) Lich's Crystalline Phylactery (node 17788,
    an ascendancy notable with containJewelSocket) holds a jewel slot PoB fills with basic non-unique jewels only; the
    search took only type "Socket" nodes of its tree list (no ascendancy, no granted passive), so it was never filled."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    try:
        lich = Context(make_payload(BuildRequest(main_skill="Spark", ascendancies=["Lich"], require=Choices())))
    except Exception as error:  # (no Lich skeleton for Spark: nothing to test here)
        pytest.skip(f"no Lich context: {error}")
    found = enumerate_skeletons(lich, ["Spark"])[0]
    if not found:
        pytest.skip("no Lich skeleton for Spark")
    search = Search(engine, None, make_payload(lich.request), found[0].as_dict(), 0)
    search.setup(quests=False)
    build, jewels = search.build, search.jewels
    build.allocate(add=[17788])
    sockets = {s["id"]: s for s in build.call("jewelSockets")["sockets"]}
    if 17788 not in sockets:
        pytest.skip("the Phylactery couldn't be allocated on this skeleton")
    assert sockets[17788]["basicOnly"] and sockets[17788]["nonUnique"] and sockets[17788]["ascendancy"]
    assert 17788 in jewels.sockets() and jewels.plain_only(17788)
    assert 17788 not in [n["id"] for n in build.call("allocatedNodes")["nodes"]]  # (the tree search's list is unchanged)
    jewels.fill(17788, float("inf"), rebuild=True)
    put = build.equipped.get(jewels.slot(17788))
    if put is not None:
        assert not put.unique and put.base in ("Ruby", "Emerald", "Sapphire")

"""The game-file datamining (builder.data.gamefiles, builder.data.liveness). The parser test needs nothing; the
others read the export of the installed game (`python -m builder.data.gamefiles`) and skip without it."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from builder.data import liveness

DATA = Path(__file__).resolve().parents[1] / "data"


def test_the_passive_graph_parser_reads_every_node_and_every_byte():
    header = bytes([3, 0, 10, 1, 12, 24, 24, 72, 72, 72, 24, 72, 144])
    roots = struct.pack("<I", 2) + struct.pack("<II", 100, 0) + struct.pack("<II", 200, 0)
    node = lambda nid, links: struct.pack("<IIII", nid, 0, 0, len(links)) + b"".join(  # noqa: E731
        struct.pack("<Ii", link, 0) for link in links)
    group = struct.pack("<ffIIB", 1.0, 2.0, 0, 0, 0) + struct.pack("<I", 2) + node(300, [100, 400]) + node(400, [])
    graph = header + roots + struct.pack("<I", 1) + group
    assert liveness.graph_nodes(graph) == {100, 200, 300, 400}
    with pytest.raises(ValueError):
        liveness.graph_nodes(graph + b"\0")  # a layout change is loud, never a silent partial read


@pytest.fixture(scope="module")
def live() -> dict:
    exports = sorted(DATA.glob("game-*/tables/SkillGems.json"))
    if not exports:
        pytest.skip("no game-file export; run `python -m builder.data.gamefiles`")
    out = exports[-1].parents[1]
    pobs = sorted(DATA.glob("pob-*/gems.json"))
    return liveness.analyse(out, pobs[-1].parent if pobs else None)


def test_meta_gems_from_the_game_files(live):
    """Settles the three the user found missing in game (7 Nov) and the item sources MECH1 relied on."""
    gems = live["gems"]
    for dead in ("Curse on Block", "Cast on Melee Stun", "Cast on Melee Kill", "Cast on Shock", "Cast while Channelling"):
        assert gems[dead]["status"] == "deprecated", dead
    assert any("Svalinn" in s for s in gems["Cast on Block"]["sources"])
    assert any("Mjolner" in s for s in gems["Thundergod's Wrath"]["sources"])
    assert "base item: Dueling Wand" in gems["Spellslinger"]["sources"]
    assert "drops (uncut gem tier 8)" in gems["Blasphemy"]["sources"]
    assert "drops (uncut gem tier 14)" in gems["Cast on Critical"]["sources"]
    assert any(s.startswith("passive: Called Shots") for s in gems["Called Shots"]["sources"])
    metas = {n for n, e in gems.items() if e["meta"] and e["status"].startswith("live")}
    assert len(metas) == 25 and {"Pounce", "Cast on Dodge", "Cast on Minion Death", "Spell Totem"} <= metas


def test_liveness_rules_on_the_rest(live):
    gems = live["gems"]
    assert gems["Bow Shot"]["status"] == "live"  # a weapon's basic skill
    assert gems["Coming Soon"]["status"] == "deprecated"  # placeholders
    assert live["passives"]["graph_nodes"] == live["passives"]["live"] > 4000
    check = live["crosscheck"]
    assert check["passive_nodes_in_pob_not_in_graph"] == 0  # every node PoB knows is in the current graph
    assert "Svalinn" in live["uniques"]["live"]

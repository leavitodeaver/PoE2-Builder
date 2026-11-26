"""COV61: a passive an item grants ("Allocates X": an Amulet anoint) reaches our models as the node's own lines
(bridge.sourceLines, from PoB's env.grantedPassives), so an anointed notable is read as an allocated one.

The manager, 26 Nov 12:33 (SPEED5's POE2T6618 / 6619, an OVERCOUNT): on 2b75e seed 1's delivered build Marked for
Sickness read x1.10 as an Amulet anoint but x1.000 as a tree node (mark_model knows the build's Freezing Mark is never
applied and cancels "Enemies you Mark take 10% increased Damage" when it reads the line); with Lasting Trauma anointed
not one model mod changed. Real PoB."""
from __future__ import annotations

import base64
import faulthandler
import html
import json
import re
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "codes" / "gate_2b75e_s1_delivered.json"
MARKED_FOR_SICKNESS = 63830


@pytest.fixture(scope="module")
def engine():
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


def delivered() -> tuple[str, dict, str, re.Match]:
    row = json.loads(FIXTURE.read_text(encoding="utf-8"))
    xml = zlib.decompress(base64.urlsafe_b64decode(row["code"] + "==")).decode("utf-8")
    slot = (re.search(r'<Slot[^>]*name="Amulet"[^>]*itemId="(\d+)"', xml)
            or re.search(r'<Slot[^>]*itemId="(\d+)"[^>]*name="Amulet"', xml))
    item = re.search(r'(<Item[^>]*id="%s"[^>]*>)(.*?)(</Item>)' % slot.group(1), xml, re.S)
    return xml, row["request"], html.unescape(item.group(2)), item


def anointed(xml: str, raw: str, item: re.Match, name: str) -> tuple[str, str]:
    from builder.search import anoints

    new = anoints.with_anoints(raw, [name])
    return xml[:item.start(2)] + html.escape(new, quote=False) + xml[item.end(2):], new


def passive_lines(engine, name: str) -> list[str]:
    return [l["text"] for l in engine.call("sourceLines")["lines"] if l["source"] == f"passive:{name}"]


def test_an_anointed_notable_is_listed_as_its_passive_lines(engine):
    """sourceLines gives an anointed notable's own lines (as an allocated node's), and only while it is anointed; the
    Amulet's "Allocates" line stays an item line. A notable already on the tree isn't listed twice."""
    xml, _, raw, item = delivered()
    engine.call("loadXml", xml=xml)
    assert passive_lines(engine, "Marked for Sickness") == []
    for name in ("Marked for Sickness", "Lasting Trauma"):
        engine.call("loadXml", xml=anointed(xml, raw, item, name)[0])
        got = passive_lines(engine, name)
        assert got, f"{name}'s lines missing from sourceLines when anointed"
        assert any(l["source"] == "item:Amulet" and l["text"] == f"Allocates {name}"
                   for l in engine.call("sourceLines")["lines"])
    engine.call("loadXml", xml=anointed(xml, raw, item, "Marked for Sickness")[0])
    assert any("Enemies you Mark take" in l for l in passive_lines(engine, "Marked for Sickness"))


@pytest.mark.own_pob_engine
def test_marked_for_sickness_anointed_moves_no_dps_as_on_the_tree(engine):
    """SPEED5's snapshot, scored as gate4 scores a delivered build, then each arm settled by our models: the anoint
    reads the same as the tree node and as no notable (x1.000: the build's Freezing Mark is never applied, so the
    Mark line is cancelled), where it read x1.100 before (the anoint's lines bypassed mark_model)."""
    sys.path.insert(0, str(ROOT / "benchmarks"))
    import gate4
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search, boss_config, boss_stats
    from builder.search.skeletons import enumerate_skeletons

    xml, request_dict, raw, item = delivered()
    request = BuildRequest.model_validate(request_dict)
    payload = make_payload(request)
    skeleton = enumerate_skeletons(Context(payload), [request.main_skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0, full=True)
    build = search.build
    build.objective.groups_enforced = False
    build.call("loadXml", xml=xml)
    build.call("resetConfig", **boss_config(request))
    build.boss_stats = boss_stats(request)
    build.refresh()
    gate4._score_finished(search)
    snap = build.snapshot()

    def settled(arm: str) -> float:
        build.restore(snap)
        if arm == "anoint":
            build.call("equip", slot="Amulet", raw=anointed(xml, raw, item, "Marked for Sickness")[1], fields=[])
            build.refresh()
        elif arm == "node":
            build.allocate(add=[MARKED_FOR_SICKNESS], attribute=search.tree.attribute)
        search.settle_still()
        return build.objective.dps(build.stats)

    control = settled("control")
    anoint = settled("anoint")
    node = settled("node")
    assert passive_lines(engine, "Marked for Sickness")  # the node arm lists it from the tree
    assert node == pytest.approx(control, rel=1e-6)
    assert anoint == pytest.approx(node, rel=1e-6), f"anoint x{anoint / control:.4f}, node x{node / control:.4f}"

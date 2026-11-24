"""Pathfinder's Path of the Sorceress / Warrior: "Can Allocate Passive Skills from the <class>'s starting point". The tree
check (bridge.treeCheck) follows an allocated Path node onto the main-tree passives next to that class's start, one way
only, and from nothing else. Node ids: PoB's tree 0_5; text also in PassiveSkills (build 26650308), poe2db, patch 0.4.0."""

from __future__ import annotations

import re

import pytest

SORCERESS, WARRIOR = 12795, 57253
STEPS = [(SORCERESS, 44871), (SORCERESS, 4739), (WARRIOR, 38646), (WARRIOR, 3936)]
PAST_THE_STEP = 30346  # (linked to 44871, not to the Path node or the Witch start)


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    pob = PobEngine()
    yield pob
    pob.close()


def pathfinder(engine, *add):
    engine.call("newBuild")
    engine.call("setClass", className="Ranger", ascendancy="Pathfinder")
    for node in add:
        engine.call("allocate", add=[node], fields=[])


def nodes_of(xml: str) -> list[int]:
    return [int(i) for i in re.search(r'<Spec\b[^>]*?\bnodes="([^"]*)"', xml).group(1).split(",") if i]


def reload_with(engine, add=(), remove=()) -> dict:
    """The build's XML with its node list edited, loaded as it stands (PoB keeps what a code lists, legal or not)."""
    xml = engine.call("exportXml")["xml"]
    kept = [n for n in nodes_of(xml) if n not in set(remove)] + [n for n in add if n not in nodes_of(xml)]
    xml = re.sub(r'(<Spec\b[^>]*?\bnodes=")[^"]*"', lambda m: m.group(1) + ",".join(map(str, kept)) + '"', xml, count=1)
    engine.call("loadXml", xml=xml)
    return engine.call("treeCheck")


def broken(check: dict) -> set[int]:
    return {b["id"] for b in check["broken"]}


def not_legally_held(engine, check: dict, node: int) -> bool:
    """PoB drops a passive with no route at all when it loads a code; one it keeps must be flagged by the check."""
    return node in broken(check) or node not in nodes_of(engine.call("exportXml")["xml"])


@pytest.mark.parametrize("path,step", STEPS)
def test_a_step_off_a_path_node_is_legal(engine, path, step):
    pathfinder(engine, path, step)
    assert step in nodes_of(engine.call("exportXml")["xml"])
    assert engine.call("treeCheck")["ok"]


@pytest.mark.parametrize("path,step", STEPS)
def test_without_the_path_node_the_step_is_broken(engine, path, step):
    pathfinder(engine, path, step)
    assert not_legally_held(engine, reload_with(engine, remove=[path]), step)


def test_a_passive_not_next_to_the_granted_start_stays_broken(engine):
    pathfinder(engine, SORCERESS)
    assert not_legally_held(engine, reload_with(engine, add=[PAST_THE_STEP]), PAST_THE_STEP)


def test_the_walk_carries_on_from_a_legal_step(engine):
    pathfinder(engine, SORCERESS, 44871, PAST_THE_STEP)
    assert engine.call("treeCheck")["ok"]


def test_no_route_from_the_main_tree_into_the_ascendancy(engine):
    pathfinder(engine, 44871)  # (reached the long way, from the Ranger's own start)
    assert engine.call("treeCheck")["ok"] and SORCERESS not in nodes_of(engine.call("exportXml")["xml"])
    assert SORCERESS in broken(reload_with(engine, add=[SORCERESS]))


def test_a_path_node_reached_only_from_the_main_tree_grants_nothing(engine):
    pathfinder(engine, 44871)
    assert 4739 not in nodes_of(engine.call("exportXml")["xml"])
    check = reload_with(engine, add=[SORCERESS, 4739])  # (probe POE2T8124: PoB's load drops the pair)
    assert not_legally_held(engine, check, SORCERESS) and not_legally_held(engine, check, 4739)

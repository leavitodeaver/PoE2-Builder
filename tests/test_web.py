"""Web page pieces that don't need PoB: the compact passive tree, and a run taking edits to its request."""

from __future__ import annotations

import threading
import time

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.web import runs as runs_module
from builder.web.runs import RunManager, changed_fields
from builder.web.treedata import NODE_TYPES, tree_payload


@pytest.fixture(scope="module")
def tree() -> dict:
    try:
        return GameData().tree
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")


def test_tree_payload_layers(tree):
    payload = tree_payload(tree)
    layer = {node[0]: node[6] for node in payload["nodes"]}
    assert all(node[3] in NODE_TYPES.values() for node in payload["nodes"])
    assert len(layer) == len(payload["nodes"])
    # No mastery art, and no edge between the main tree and an ascendancy (or two ascendancies)
    assert len(layer) < len(tree["nodes"])
    assert payload["edges"] and all(layer[a] == layer[b] for a, b in payload["edges"])
    assert len({tuple(sorted(edge)) for edge in payload["edges"]}) == len(payload["edges"])
    # Every class has a start node on the tree, and every ascendancy with nodes keeps them in one layer
    ids = set(layer)
    assert all(c["start"] in ids for c in payload["classes"])
    assert {n[6] for n in payload["nodes"] if n[6]} <= {a["name"] for a in payload["ascendancies"]}


def test_changed_fields_ignores_name_and_notes():
    base = BuildRequest()
    assert changed_fields(base, base.model_copy(update={"name": "other", "notes": "x"})) == []
    edited = base.model_copy(update={"max_buttons": 4, "dps_gate": 1000.0})
    assert sorted(changed_fields(base, edited)) == ["dps_gate", "max_buttons"]


def test_tree_payload_art_and_class_versions(tree):
    if not tree.get("sprites"):
        pytest.skip("the tree dump predates node art and class versions; run `uv run python -m builder.data dump`")
    payload = tree_payload(tree, {"x": {"ascendancies": None, "classes": ["Huntress"], "note": ""}})
    assert payload["rules"]["x"]["classes"] == ["Huntress"]
    rows = {row[0]: row for row in payload["nodes"]}
    assert all(len(row) == 12 for row in rows.values())
    # Vile Wounds: a notable with its own icon and frame, and the version a Huntress gets in its place
    vile = rows[30695]
    assert payload["icons"][vile[7]].endswith(".dds") and payload["frames"][vile[8]] == [
        "NotableFrameAllocated", "NotableFrameCanAllocate", "NotableFrameUnallocated"]
    assert vile[9] > 0 and vile[10] > vile[9]  # icon and frame sizes, in tree units
    (cls, name, stats, icon), = vile[11]
    assert (cls, name) == ("Huntress", "Eagle Eye") and stats and payload["icons"][icon].endswith("eagleeye.dds")
    # Ascendancy starts get the ascendancy's middle frame
    start = next(r for r in payload["nodes"] if r[3] == NODE_TYPES["AscendClassStart"])
    assert payload["frames"][start[8]] == ["AscendancyMiddle"] * 3


def test_tree_art_atlas(tree, tmp_path, monkeypatch):
    from builder.web import treeart

    data = GameData()
    if not tree.get("sprites") or not (treeart.sheet_dir(data) / "skills_64_64_BC1.dds.zst").exists():
        pytest.skip("PoB's tree sprite sheets aren't fetched")
    monkeypatch.setattr(treeart, "art_dir", lambda: tmp_path)
    atlas = treeart.ensure(data)
    assert treeart.ensure(data) == atlas  # the second call reads what the first built
    assert {f.name for f in tmp_path.iterdir()} >= {"atlas.json", *treeart.FILES}
    frames, icons = treeart.wanted(data)
    # Every frame and (apart from the mastery art nobody draws) every icon of the nodes is in the atlas
    assert not [f for f in frames if f not in atlas["frames"]]
    drawn = {n["icon"] for n in data.tree["nodes"] if n.get("icon") and n["type"] != "OnlyImage"}
    assert not [i for i in drawn if i not in atlas["icons"]]
    for x, y, w, h in list(atlas["frames"].values()) + list(atlas["icons"].values()):
        assert x >= 0 and y >= 0 and x + w <= treeart.ATLAS_WIDTH


class NoPrices:
    def table(self, league, refresh=False):
        return None


def wait(condition, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_edit_continues_from_the_best_build(monkeypatch, tmp_path):
    calls = []

    def fake_run(request, prices=None, workers=1, seconds=None, on_event=None, should_cancel=None):
        calls.append({"seed": request.seed_code, "seconds": seconds, "max_buttons": request.max_buttons})
        on_event({"kind": "status", "t": 0.5, "message": f"search {len(calls)}"})
        result = {"status": "ok", "evaluations": 100, "seconds": 1.0, "notes": [], "request": request.model_dump(mode="json")}
        if len(calls) == 1:
            while not should_cancel():
                time.sleep(0.01)
            result["code"] = "CODE-1"
        else:
            result["code"] = "CODE-2"
            result["notes"] = ["Your PoB code measured 5 boss DPS here."]
            result["label"] = "Mercenary · Ice Shot · from your PoB code"
            result["probes"] = [{"skeleton": "Mercenary · Ice Shot · from your PoB code"}]
        return result

    monkeypatch.setattr(runs_module, "web_run", fake_run)
    monkeypatch.setattr(runs_module, "RESULTS_DIR", tmp_path)
    manager = RunManager(NoPrices())
    request = BuildRequest(name="mine", effort_minutes=3)
    record = manager.start(request)
    wait(lambda: len(calls) == 1)

    # A name change alone restarts nothing and is kept for the result
    assert manager.update(record.id, request.model_copy(update={"name": "renamed"})) == {"restarting": False, "changed": []}
    assert len(calls) == 1 and record.pending is None

    edited = request.model_copy(update={"name": "renamed", "max_buttons": 4})
    assert manager.update(record.id, edited) == {"restarting": True, "changed": ["max_buttons"]}
    wait(lambda: record.status != "running")

    assert record.status == "done" and record.restarts == 1
    # The second search starts from the first one's best build, with the edit, for the time that is left
    assert calls[1]["seed"] == "CODE-1" and calls[1]["max_buttons"] == 4
    assert 0 < calls[1]["seconds"] <= 180
    result = record.result
    assert result["request"]["max_buttons"] == 4 and result["request"]["name"] == "renamed"
    assert result["request"]["seed_code"] is None  # the carried build is the search's seed, not the user's request
    assert result["evaluations"] == 200
    assert any("carried over" in note for note in result["notes"])
    assert "your PoB code" not in result["label"] and "your PoB code" not in result["probes"][0]["skeleton"]
    assert any("edited 1 time" in note for note in result["notes"])
    assert [e["kind"] for e in record.events if e["kind"] == "restart"] == ["restart"]
    assert (tmp_path / result["saved_as"]).exists()

    # A finished run takes no more edits
    with pytest.raises(RuntimeError):
        manager.update(record.id, edited.model_copy(update={"max_buttons": 5}))


def scripted_runs(monkeypatch, tmp_path, release):
    """Fake searches: the first waits for `release` or a cancel, any later one ends at once."""
    calls = []

    def fake_run(request, prices=None, workers=1, seconds=None, on_event=None, should_cancel=None):
        calls.append({"seed": request.seed_code, "seconds": seconds, "effort": request.effort_minutes,
                      "cancelled_early": False})
        result = {"status": "ok", "evaluations": 1, "seconds": 1.0, "notes": [], "code": f"CODE-{len(calls)}"}
        if len(calls) == 1:
            while not release.is_set() and not should_cancel():
                time.sleep(0.01)
            calls[0]["cancelled_early"] = should_cancel()
        return result

    monkeypatch.setattr(runs_module, "web_run", fake_run)
    monkeypatch.setattr(runs_module, "RESULTS_DIR", tmp_path)
    return calls


def test_more_search_time_extends_without_stopping(monkeypatch, tmp_path):
    release = threading.Event()
    calls = scripted_runs(monkeypatch, tmp_path, release)
    manager = RunManager(NoPrices())
    request = BuildRequest(effort_minutes=3)
    record = manager.start(request)
    wait(lambda: len(calls) == 1)
    answer = manager.update(record.id, request.model_copy(update={"effort_minutes": 10}))
    assert answer == {"restarting": False, "changed": ["effort_minutes"], "extended": True}
    time.sleep(0.2)
    assert record.pending is None and record.status == "running"  # the search in progress wasn't stopped
    release.set()
    wait(lambda: record.status != "running")
    # When it ended, the search carried on from its best build for the time added
    assert len(calls) == 2 and not calls[0]["cancelled_early"]
    assert calls[1]["seed"] == "CODE-1" and calls[1]["effort"] == 10 and 300 < calls[1]["seconds"] <= 600


def test_cutting_search_time_below_what_is_spent_ends_the_run(monkeypatch, tmp_path):
    calls = scripted_runs(monkeypatch, tmp_path, threading.Event())
    manager = RunManager(NoPrices())
    request = BuildRequest(effort_minutes=3)
    record = manager.start(request)
    wait(lambda: len(calls) == 1)
    record.started -= 100  # 100 s in: a minute is already used up
    assert manager.update(record.id, request.model_copy(update={"effort_minutes": 1}))["restarting"] is True
    wait(lambda: record.status != "running")
    assert len(calls) == 1 and calls[0]["cancelled_early"]  # stopped, with its result as the answer
    assert record.result["request"]["effort_minutes"] == 1 and record.restarts == 0

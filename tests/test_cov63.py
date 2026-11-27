"""COV63: the audit reads Zarokh's Gift's "Sinister Jewel Socket" as the socket it is (coverage.socket_label_status;
NINJA1's gap list, the manager 27 Nov 15:22). PoB counts a jewel in that socket: probe POE2T6993."""
import json

from builder.data import data_dir
from builder.ledger import coverage


def test_cov63_a_socket_nodes_label_is_pobs():
    assert coverage.socket_label_status("Sinister Jewel Socket", "jewel socket") == (
        "pob", None, coverage.SOCKET_LABEL_WHY)
    assert coverage.socket_label_status("Jewel Socket", "jewel socket")[0] == "pob"
    # only a socket node's own label: the same words on another kind of node, or any other line, stay as they were
    assert coverage.socket_label_status("Sinister Jewel Socket", "notable") is None
    assert coverage.socket_label_status("10% increased Damage", "jewel socket") is None


def test_cov63_zarokhs_gift_is_the_one_socket_line_and_a_sinister_socket_in_the_tree():
    from pathlib import Path

    root = data_dir()
    gaps = json.loads((root / "gaps.json").read_text(encoding="utf-8"))
    sockets = [(g["node"], g["id"], g["line"]) for g in gaps["passives"] if coverage.passive_kind(g) == "jewel socket"]
    assert sockets == [("Zarokh's Gift", 11184, "Sinister Jewel Socket")]
    tree = Path(__file__).resolve().parents[1] / "vendor" / "PathOfBuilding-PoE2" / "src" / "TreeData" / "0_5" / "tree.json"
    nodes = json.loads(tree.read_text(encoding="utf-8"))["nodes"]
    node = nodes["11184"] if isinstance(nodes, dict) else next(n for n in nodes if n.get("skill") == 11184)
    assert node["name"] == "Zarokh's Gift" and node["isJewelSocket"] and node["sinister"]

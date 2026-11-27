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


MARKED = {
    "model-read-cov63-martial-master": ["Gain Combo from all Attack Hits",
                                        "Skills can build and retain Combo regardless of Weapon Set"],
    "model-read-cov63-vivid-stampede-lines": ["Expend all Vivid Wisps to trigger Vivid Stampede when you Attack",
                                              "Gain a Vivid Wisp for every 10 metres you move, up to a maximum of 3",
                                              "Stags deal 20% more damage per leap",
                                              "Stags have 20% more Shock Magnitude per leap"],
}


def test_cov63_read_passive_lines_are_marked():
    from builder.ledger import model_entry
    from builder.search import models, usage

    # (the readers each entry names)
    assert usage.MARTIAL_MASTER == "Martial Master" and callable(models._combo_builder)
    from builder.search import conditions
    assert models.VIVID_PER_LEAP == 0.20 and models.VIVID_METRES_PER_WISP == 10.0
    assert conditions.STAG_SHOCK_LEAP.match("Stags have 20% more Shock Magnitude per leap")
    for entry, lines in MARKED.items():
        for line in lines:
            assert model_entry(line).id == entry, line
            assert coverage.ignored_line_status(line, True)[0] == "model", line


def test_cov63_the_unread_stay_open():
    for line in ("Slam Skills you use yourself have 30% increased Aftershock Area of Effect",
                 "Unique Tamed Beasts are Possessed by random Azmeri Spirits, changing every 20 seconds",
                 "Grants effect of Guided Meteoric Shrine", "Vivid Stags leap towards enemies",
                 "Gain Finality for 0.5 seconds per Combo expended when using Skills",
                 "Lose all Infernal Flame on reaching maximum Infernal Flame",
                 "25% of Infernal Flame lost per second if none was gained in the past 2 seconds"):
        assert coverage.ignored_line_status(line, line.startswith(("Slam", "Unique Tamed", "Vivid", "Lose all", "25%")))[0] == "none", line

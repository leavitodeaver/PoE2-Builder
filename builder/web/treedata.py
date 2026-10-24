"""The passive tree in the compact form the web page draws (/api/tree), and the page's summary of
a build for the live "best pick" panel.

Nodes are arrays, to keep the payload small: [id, x, y, type, name, stats, ascendancy]. The main
tree and each ascendancy sit in separate regions of the same coordinate space, so the page draws
them as separate layers; edges that cross between layers are left out.
"""

from __future__ import annotations

from builder.request.constraints import main_skill

NODE_TYPES = {"Normal": 0, "Notable": 1, "Keystone": 2, "Socket": 3, "ClassStart": 4, "AscendClassStart": 5}
# Mastery art ("OnlyImage") has no links or stats, so it isn't drawn

STAT_FIELDS = ("Life", "EnergyShield", "Mana", "Ward", "Armour", "Evasion", "FireResist", "ColdResist",
               "LightningResist", "ChaosResist", "SpiritUnreserved", "EffectiveBlockChance", "EvadeChance")


def tree_payload(tree: dict) -> dict:
    nodes = [n for n in tree["nodes"] if n["type"] in NODE_TYPES]
    layer_of = {n["id"]: n.get("ascendancy") or "" for n in nodes}
    ascendancies = []
    class_starts = {}
    for cls in tree["classes"]:
        class_starts[cls["name"]] = cls["startNode"]
        for asc in cls["ascendancies"]:
            ascendancies.append({"name": asc["name"], "class": cls["name"], "start": asc["startNode"]})
    edges = []
    for node in nodes:
        for other in node["linked"]:
            # Each link is listed from both ends: keep one, and only those inside a layer
            if node["id"] < other and layer_of.get(other) == layer_of[node["id"]]:
                edges.append([node["id"], other])
    return {
        "version": tree.get("treeVersion"),
        "classes": [{"name": name, "start": start} for name, start in class_starts.items()],
        "ascendancies": ascendancies,
        "nodes": [[n["id"], round(n["x"]), round(n["y"]), NODE_TYPES[n["type"]], n["name"], n["stats"],
                   n.get("ascendancy") or ""] for n in nodes],
        "edges": edges,
    }


def synopsis_of(facts: dict) -> dict:
    """What the live panel shows about a build: from the bridge's `facts` for its PoB file."""
    main = next((g for g in facts["skillGroups"] if g["main"]), None)
    supports = [g["name"] for g in (main["gems"] if main else []) if g["support"] and g["enabled"]]
    items = [{"slot": i["slot"], "name": i["name"], "base": i["base"], "rarity": i["rarity"]} for i in facts["items"]]
    return {
        "mainSkill": main_skill(facts), "className": facts["className"], "ascendClassName": facts["ascendClassName"], "level": facts["level"],
        "allocated": facts["allocated"], "ascendancyAllocated": facts["ascendancyAllocated"],
        "stats": {key: facts["stats"].get(key) for key in STAT_FIELDS},
        "supports": supports, "items": items,
    }

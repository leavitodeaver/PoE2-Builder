"""The passive tree in the compact form the web page draws (/api/tree), and the page's summary of
a build for the live "best pick" panel.

Nodes are arrays, to keep the payload small: [id, x, y, type, name, stats, ascendancy, icon, frame,
icon size, frame size, variants]. The main
tree and each ascendancy sit in separate regions of the same coordinate space, so the page draws
them as separate layers; edges that cross between layers are left out.
"""

from __future__ import annotations

from builder.request.constraints import main_skill

NODE_TYPES = {"Normal": 0, "Notable": 1, "Keystone": 2, "Socket": 3, "ClassStart": 4, "AscendClassStart": 5}
# Mastery art ("OnlyImage") has no links or stats, so it isn't drawn

STAT_FIELDS = ("Life", "EnergyShield", "Mana", "Ward", "Armour", "Evasion", "FireResist", "ColdResist",
               "LightningResist", "ChaosResist", "SpiritUnreserved", "EffectiveBlockChance", "EvadeChance")


def tree_payload(tree: dict, rules: dict | None = None) -> dict:
    """`rules` is constraints.passive_rules: which names exist only for some ascendancies or classes."""
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

    # Art: icons and frames by index, so the nodes stay small (the images come from /api/tree/art)
    icons: list[str] = []
    frame_sets: list[list[str]] = []

    def icon_id(path: str | None) -> int:
        if not path:
            return -1
        if path not in icons:
            icons.append(path)
        return icons.index(path)

    def frame_id(node: dict) -> int:
        frame = node.get("frame")
        if node["type"] == "AscendClassStart":
            names = ["AscendancyMiddle"] * 3  # as PoB draws them, whatever overlay the node data names
        elif frame:
            names = [frame["alloc"], frame["path"], frame["unalloc"]]
        else:
            return -1
        if names not in frame_sets:
            frame_sets.append(names)
        return frame_sets.index(names)

    rows = []
    for n in nodes:
        # The version of a node near a class start that a class gets in its place: [class, name, stats, icon]
        variants = [[cls, v["name"], v.get("stats") or [], icon_id(v.get("icon"))] for cls, v in (n.get("variants") or {}).items()]
        rows.append([n["id"], round(n["x"]), round(n["y"]), NODE_TYPES[n["type"]], n["name"], n["stats"], n.get("ascendancy") or "",
                     icon_id(n.get("icon")), frame_id(n), n.get("iconSize") or 0, n.get("frameSize") or 0, variants or None])
    return {
        "version": tree.get("treeVersion"),
        "classes": [{"name": name, "start": start} for name, start in class_starts.items()],
        "ascendancies": ascendancies,
        "icons": icons, "frames": frame_sets,
        "rules": rules or {},
        "nodes": rows,
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

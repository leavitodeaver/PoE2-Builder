"""What the current game can still reach, from the exported game files (builder.data.gamefiles).

The files keep removed content, so nothing counts as live just for being there. The rules, each with the files it
reads (also written into live.json so a result can cite them):

PASSIVES  live if the passive's PassiveSkillGraphId is a node of the current tree graph
          (Metadata/PassiveSkillGraph.psg, parsed here; PassiveSkills.PassiveSkillGraphId).
GEMS      live if any of these reaches it (SkillGems, named by BaseItemTypes.Name):
          - drops: the gem has an uncut-gem tier (the schema's SkillGems.CraftingLevel > 0; it matches poe2db's
            "Uncut ... Gem Tier N" for every gem checked, while the schema's Tier column is 0 for all);
          - a support the game offers: a SupportGems row whose second, unnamed column is above 0 (50 for 620
            supports, 0 for 17; read as the weight it is offered at: an inference, labelled), or IsLineage;
          - a base item: ItemInherentSkills lists it on a base type (Dueling Wand: Spellslinger);
          - a weapon's or a shapeshift form's basic skill: CharacterMeleeSkills, CharacterShapeShiftBasicSkills;
          - a passive: PassiveSkills.GrantedSkill on a node in the current graph (ascendancy notables);
          - a modifier that can roll: ModGrantedSkills, the mod being rollable (MODS below);
          - a unique: ModGrantedSkills on a unique mod (Mods.GenerationType 3). Which unique carries a unique mod is
            server-side, not in the client files, so this needs a unique in the collection (UNIQUES below) whose
            text grants the skill; the unique texts come from PoB's export (the only part not from the game files).
          Names tagged [DNT] / [UNUSED] (GGG's "do not translate" and unused markers) and placeholder gems (base ids
          ...GemUnknownN, named "Coming Soon") are never live. The files also hold content not released yet, so a
          gem live only by the support weight is "live (inferred)", and the cross-check lists those PoB lacks.
          - another live skill: GemEffects.AdditionalGrantedEffects (Shred grants Ice Fragments).
          Nothing is removed on the files' word alone (a rule that misses a source would drop a real skill). A gem none
          of these reach is "deprecated" only when a second current source agrees (GONE: poe2db's patch notes, the
          user's in-game check) or GGG marks it ([DNT], placeholders); "unresolved", and kept usable, when PoB's export
          carries it (a source these rules don't read yet: 0.5's league content, uniques PoB's text names differently);
          "dead in files" otherwise (in no current source we use, so nothing to remove).
UNIQUES   live if in the unique collection layout (UniqueStashLayout -> Words.Text).
MODS      rollable if Mods.GenerationType is prefix (1) or suffix (2) or any SpawnWeight_Values entry is above 0;
          essence-only if EssenceMods points at it; unique (3) mods are counted apart (carrier server-side).
"""

from __future__ import annotations

import json
import re
import struct
import unicodedata
from pathlib import Path

RULES = __doc__.split("reads (also written into live.json so a result can cite them):", 1)[1].strip()
DEAD_TAG = re.compile(r"\[(DNT|UNUSED)[^\]]*\]", re.I)
PLACEHOLDER = re.compile(r"GemUnknown\d+$")
INFERRED = "support the game offers"
# Gone by a second current source, besides the game files (the rule for calling anything deprecated)
GONE = {
    "Curse on Block": "poe2db: no tier, no source; the user found it in no item in game (7 Nov 2026)",
    "Cast on Melee Stun": "poe2db: no tier, no source (legacy page, fixed level 8)",
    "Cast on Melee Kill": "poe2db: no tier, no source (legacy page, fixed level 8)",
    "Cast on Shock": "poe2db, version 0.3.0: merged into Cast on Elemental Ailment",
    "Cast on Freeze": "poe2db, version 0.3.0: merged into Cast on Elemental Ailment",
    "Cast on Ignite": "poe2db, version 0.3.0: merged into Cast on Elemental Ailment",
}
UNIQUE_MOD, PREFIX, SUFFIX = 3, 1, 2

# poe2db's Meta gem tag page (https://poe2db.tw/us/Meta, 39 entries, read 7 Nov 2026), for the cross-check only
POE2DB_META = [
    "Blasphemy", "Cast on Dodge", "Pounce", "Ferocious Roar", "Cast on Minion Death", "Spellslinger", "Animus Splinters",
    "Mirage Archer", "Spell Totem", "Feral Invocation", "Reaper's Invocation", "Ancestral Warrior Totem", "Mortar Cannon",
    "Hand of Chayula", "Cast on Melee Kill", "Cast on Block", "Demon Magus", "Cast on Shock", "Cast on Freeze",
    "Cast on Ignite", "Cast on Elemental Ailment", "Cast on Death", "Cast on Critical", "Cast when Stunned",
    "Cast when Damage Taken", "Barrier Invocation", "Cast while Channelling", "Cast on Melee Stun", "Summon Companion",
    "Hydra Familiar", "Elemental Invocation", "Curse on Block", "Hollow Form", "Mirage Deadeye", "Called Shots",
    "Cast on Charm Use", "Thundergod's Wrath", "Fire Spell on Hit", "Spirit Vessel",
]


def plain(text: str) -> str:
    """Accents dropped, for matching names across sources (Mjölner, Mjolner)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def graph_nodes(psg: bytes) -> set[int]:
    """Node ids of a PoE2 passive graph (.psg): a short header, root nodes (u32 id, u32 0), then groups of
    (f32 x, f32 y, u32, u32, u8, u32 count, count x (u32 id, u32 radius, u32 position, u32 links, links x (u32 id,
    i32 curvature))). Read to the last byte, or it raises."""
    for start in range(1, 64):
        roots = struct.unpack_from("<I", psg, start)[0]
        if 0 < roots < 64 and all(struct.unpack_from("<I", psg, start + 8 + 8 * i)[0] == 0 for i in range(roots)):
            break
    else:
        raise ValueError("no root list in the passive graph")
    pos = start + 4 + 8 * roots
    nodes = {struct.unpack_from("<I", psg, start + 4 + 8 * i)[0] for i in range(roots)}
    groups = struct.unpack_from("<I", psg, pos)[0]
    pos += 4
    for _ in range(groups):
        pos += 17
        count = struct.unpack_from("<I", psg, pos)[0]
        pos += 4
        for _ in range(count):
            node, _radius, _position, links = struct.unpack_from("<IIII", psg, pos)
            nodes.add(node)
            pos += 16 + 8 * links
    if pos != len(psg):
        raise ValueError(f"passive graph layout changed: read {pos} of {len(psg)} bytes")
    return nodes


class Tables:
    def __init__(self, out: Path):
        self.dir = out / "tables"
        self._cache: dict[str, list[dict]] = {}

    def __getitem__(self, name: str) -> list[dict]:
        if name not in self._cache:
            self._cache[name] = json.loads((self.dir / f"{name}.json").read_text(encoding="utf-8"))
        return self._cache[name]


def as_list(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def analyse(out: Path, pob: Path | None = None) -> dict:
    t = Tables(out)
    base, gems, mods = t["BaseItemTypes"], t["SkillGems"], t["Mods"]
    meta_type = next(r["_index"] for r in t["ActiveSkillType"] if r["Id"] == "Meta")

    nodes = graph_nodes((out / "files" / "Metadata" / "PassiveSkillGraph.psg").read_bytes())
    passives = t["PassiveSkills"]
    live_passive = {p["_index"]: p["PassiveSkillGraphId"] in nodes for p in passives}

    words = t["Words"]
    uniques = sorted({words[r["WordsKey"]]["Text"] for r in t["UniqueStashLayout"]})
    live_uniques = {plain(n) for n in uniques}

    essence_mods = {m for r in t["EssenceMods"] for k, v in r.items() if k != "_index" and isinstance(v, int)
                    and k.lower().endswith("mod") for m in [v]}

    def mod_kind(m: dict) -> str:
        if m["GenerationType"] == UNIQUE_MOD:
            return "unique"
        if m["GenerationType"] in (PREFIX, SUFFIX) or any(w > 0 for w in as_list(m.get("SpawnWeight_Values"))):
            return "rollable"
        return "essence" if m["_index"] in essence_mods else "other"

    # Unique texts (PoB's export): which skills each unique grants
    unique_grants: dict[str, list[str]] = {}
    if pob is not None and (pob / "uniques.json").exists():
        for u in json.loads((pob / "uniques.json").read_text(encoding="utf-8"))["uniques"]:
            if plain(u["name"]) not in live_uniques:
                continue
            for skill in re.findall(r"Grants Skill: (?:Level \S+ )?([^\n{]+)", u.get("raw", "")):
                unique_grants.setdefault(plain(skill.strip()), []).append(u["name"])

    inherent: dict[int, list[str]] = {}
    for r in t["ItemInherentSkills"]:
        for g in as_list(r["SkillsGranted"]):
            inherent.setdefault(g, []).append(base[r["BaseItemType"]]["Name"])
    basic: dict[int, str] = {}
    for r in t["CharacterMeleeSkills"]:
        for g in as_list(r["SkillGems"]):
            basic[g] = "weapon basic skill"
    for r in t["CharacterShapeShiftBasicSkills"]:
        for g in as_list(r["Skill"]):
            basic[g] = "shapeshift basic skill"
    by_mod: dict[int, list[dict]] = {}
    for r in t["ModGrantedSkills"]:
        by_mod.setdefault(r["Skill"], []).append(mods[r["Mod"]])
    effect_gem = {}
    for g in gems:
        for e in as_list(g["GemEffects"]):
            effect_gem.setdefault(t["GemEffects"][e]["GrantedEffect"], g["_index"])
    granted_by: dict[int, list[str]] = {}
    for g in gems:
        for e in as_list(g["GemEffects"]):
            for extra in as_list(t["GemEffects"][e].get("AdditionalGrantedEffects")):
                if extra in effect_gem and effect_gem[extra] != g["_index"]:
                    granted_by.setdefault(effect_gem[extra], []).append(base[g["BaseItemType"]]["Name"])
    by_passive: dict[int, list[dict]] = {}
    for p in passives:
        if p.get("GrantedSkill") is not None:
            by_passive.setdefault(p["GrantedSkill"], []).append(p)

    offered_support = {r["SkillGem"]: r for r in t["SupportGems"]}
    effects, granted, active = t["GemEffects"], t["GrantedEffects"], t["ActiveSkills"]
    out_gems: dict[str, dict] = {}
    for g in gems:
        name = base[g["BaseItemType"]]["Name"]
        if not name:
            continue
        entry = out_gems.setdefault(name, {"sources": [], "meta": False, "support": False, "ids": []})
        entry["ids"].append(base[g["BaseItemType"]]["Id"].rsplit("/", 1)[-1])
        for e in as_list(g["GemEffects"]):
            effect = granted[effects[e]["GrantedEffect"]]
            entry["support"] |= bool(effect["IsSupport"])
            if effect.get("ActiveSkill") is not None and meta_type in as_list(active[effect["ActiveSkill"]]["ActiveSkillTypes"]):
                entry["meta"] = True
        sources = entry["sources"]
        if DEAD_TAG.search(name) or PLACEHOLDER.search(entry["ids"][-1]):
            continue
        if (g.get("CraftingLevel") or 0) > 0:
            sources.append(f"drops (uncut gem tier {g['CraftingLevel']})")
        support = offered_support.get(g["_index"])
        if support is not None and support["IsLineage"]:
            sources.append("lineage support (drops)")
        elif support is not None and (support.get("_unnamed1") or 0) > 0:
            sources.append(f"support the game offers (weight {support['_unnamed1']})")
        if g["_index"] in basic:
            sources.append(basic[g["_index"]])
        for item in inherent.get(g["_index"], []):
            sources.append(f"base item: {item}")
        for other in granted_by.get(g["_index"], []):
            sources.append(f"granted by the skill {other}")
        for p in by_passive.get(g["_index"], []):
            if live_passive[p["_index"]]:
                sources.append(f"passive: {p['Name']} ({p['Id']})")
        for m in by_mod.get(g["_index"], []):
            kind = mod_kind(m)
            if kind == "unique":
                carriers = unique_grants.get(plain(name), [])
                if carriers:
                    sources.append(f"unique: {', '.join(sorted(set(carriers)))} (mod {m['Id']})")
            elif kind in ("rollable", "essence"):
                sources.append(f"{kind} mod {m['Id']}")
    pob_carries: set[str] = set()  # any gem in PoB's export, socketable or granted (legacy ones aside)
    if pob is not None and (pob / "gems.json").exists():
        pob_carries = {plain(g["name"]) for g in json.loads((pob / "gems.json").read_text(encoding="utf-8"))["gems"]
                       if not g.get("legacy")}
    for name, entry in out_gems.items():
        entry["sources"] = list(dict.fromkeys(entry["sources"]))
        firm = [x for x in entry["sources"] if not x.startswith(INFERRED)]
        marked = bool(DEAD_TAG.search(name)) or bool(PLACEHOLDER.search(entry["ids"][-1]))
        entry["status"] = ("live" if firm else "live (inferred)" if entry["sources"] else "deprecated" if marked or name in GONE
                           else "unresolved" if plain(name) in pob_carries else "dead in files")
        if name in GONE and not entry["sources"]:
            entry["gone"] = GONE[name]

    kinds: dict[str, int] = {}
    for m in mods:
        kinds[mod_kind(m)] = kinds.get(mod_kind(m), 0) + 1
    dead_named = sorted({p["Name"] for p in passives if not live_passive[p["_index"]] and (p["IsNotable"] or p["IsKeystone"])
                         and p["Name"] and p.get("AtlasSubTree") is None})
    result = {
        "rules": RULES,
        "passives": {"graph_nodes": len(nodes), "rows": len(passives), "live": sum(live_passive.values()),
                     "dead_notables_and_keystones": dead_named},
        "gems": out_gems,
        "uniques": {"live": uniques},
        "mods": kinds,
    }
    if pob is not None:
        result["crosscheck"] = crosscheck(result, pob, nodes)
    return result


def crosscheck(result: dict, pob: Path, nodes: set[int]) -> dict:
    """Against PoB's export and poe2db's Meta list: what the files mark live that the others lack, and the reverse."""
    gems = result["gems"]
    live = {plain(n) for n, e in gems.items() if e["status"].startswith("live")}
    dead = {plain(n) for n, e in gems.items() if e["status"] == "deprecated"}
    pob_gems = json.loads((pob / "gems.json").read_text(encoding="utf-8"))["gems"]
    pob_names = {plain(g["name"]) for g in pob_gems}
    pob_offered = {plain(g["name"]) for g in pob_gems if not (g.get("fromItem") or g.get("fromTree") or g.get("legacy"))}
    pob_uniques = {plain(u["name"]) for u in json.loads((pob / "uniques.json").read_text(encoding="utf-8"))["uniques"]}
    live_uniques = {plain(n) for n in result["uniques"]["live"]}
    pob_nodes = {n["id"] for n in json.loads((pob / "tree.json").read_text(encoding="utf-8"))["nodes"] if "id" in n}
    meta_live = sorted(n for n, e in gems.items() if e["meta"] and e["status"].startswith("live"))
    return {
        "gems_live_not_in_pob": sorted(live - pob_names),
        "gems_in_pob_marked_dead": sorted(pob_names & dead),
        "gems_pob_offers_as_socketable_marked_dead": sorted(pob_offered & dead),
        "gems_unresolved": sorted(n for n, e in gems.items() if e["status"] == "unresolved"),
        "gems_inferred_not_in_pob": sorted(n for n, e in gems.items() if e["status"] == "live (inferred)"
                                           and plain(n) not in pob_names),
        "uniques_live_not_in_pob": sorted(live_uniques - pob_uniques),
        "uniques_in_pob_not_live": sorted(pob_uniques - live_uniques),
        "passive_nodes_in_pob_not_in_graph": len(pob_nodes - nodes),
        "passive_nodes_in_graph_not_in_pob": len(nodes - pob_nodes),
        "meta_live": {n: gems[n]["sources"] for n in meta_live},
        "meta_poe2db_marked_dead": sorted(n for n in POE2DB_META if plain(n) in dead),
        "meta_poe2db_not_in_files": sorted(n for n in POE2DB_META if plain(n) not in live | dead),
    }


def write(out: Path) -> dict:
    pobs = sorted((out.parent).glob("pob-*"), key=lambda p: p.stat().st_mtime)
    result = analyse(out, pobs[-1] if pobs else None)
    (out / "live.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    gems = result["gems"]
    return {"passives_live": result["passives"]["live"], "passive_rows": result["passives"]["rows"],
            "gems_live": sum(e["status"] == "live" for e in gems.values()), "gems": len(gems),
            "gems_live_inferred": sum(e["status"] == "live (inferred)" for e in gems.values()),
            "gems_unresolved": sum(e["status"] == "unresolved" for e in gems.values()),
            "metas_live": sum(e["meta"] and e["status"].startswith("live") for e in gems.values()),
            "uniques_live": len(result["uniques"]["live"]), "mods": result["mods"]}

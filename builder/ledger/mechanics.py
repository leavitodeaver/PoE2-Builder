"""The mechanics catalog: every mechanic of the game PoB doesn't simulate, or simulates wrongly, with a plan for each.

The coverage audit (coverage.py) is exhaustive at the level of lines. A mechanic is what many lines share: Parry,
Ancestral Boost, Presence, Tribute, Thorns, Heavy Stun, charge consumption. Modeling happens per mechanic, so this
catalog groups the lines under the mechanic they depend on and gives each one a plan.

Where the mechanics come from (not from PoB):
  1. the game's keyword glossary, datamined: poe2db's Keywords page (ledger/mechanics/keywords_poe2db.json, with its
     fetch date);
  2. capitalised game terms in every line the data dump holds and every gem description (the game capitalises its
     keywords: Parried, Ancestrally Boosted, Heavy Stun ...);
  3. PoB's Configuration options (src/Modules/ConfigOptions.lua): each is something PoB can't work out and asks the user
     to assert;
  4. systemic rules that are no single line (charge economy, stun on bosses, ailment thresholds, the boss's behaviour ...),
     written by hand in ledger/mechanics/catalog.yaml with their plans.
Catalog entries (4) hold the reviewed plans; every keyword or frequent term no entry covers becomes an automatic entry
marked "to plan", so nothing slips through.

What PoB does is worked out from evidence, not PoB's claims: the coverage audit's status of every linked line (parsed
and read by a calculation, parsed into nothing read, ignored, condition never on, a parse that lost part), and whether a
Configuration option is what switches it on.

    uv run python -m builder.ledger.mechanics      writes data/pob-*/mechanics.json and docs/MECHANICS.md
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import yaml

from builder import ROOT

MECH_DIR = ROOT / "ledger" / "mechanics"
DOC = ROOT / "docs" / "MECHANICS.md"
POB_SRC = ROOT / "vendor" / "PathOfBuilding-PoE2" / "src"
STATUS_ORDER = ("none", "wrong", "config-only", "partial", "full")
TERM = re.compile(r"(?<=[a-z,] )((?:[A-Z][a-z]+(?:'s)?)(?: (?:of |the |to )?[A-Z][a-z]+(?:'s)?){0,2})")
# Terms that are plain stats or grammar, not mechanics of their own (PoB counts them as stats)
PLAIN = set("""Damage Life Mana Armour Level Skills Skill Hit Hits Attacks Attack Enemies Enemy Strength Dexterity Intelligence
Spells Spell You Gain Second Chance Rate Effect Bonus Cast Weapon Weapons Modifier Requirement Slot Socket Supported
Gems Physical Fire Cold Lightning Chaos Elemental Extra Kill Allocated Attribute Attributes Radius Passive Skills Resistance
Resistances Duration Charges Charge Rare Recovery Regeneration Energy Avoid Shield Allies Cooldown Trigger""".split()) | {
    # stats PoB counts as such: their lines belong under the mechanic that makes them conditional, not a mechanic of their own
    "Energy Shield", "Evasion Rating", "Critical Hit Chance", "Attack Speed", "Physical Damage", "Critical Damage Bonus",
    "Cast Speed", "Elemental Damage", "Spell Damage", "Accuracy Rating", "Mana Regeneration Rate", "Area of Effect",
    "Chaos Damage", "Fire Damage", "Attack Damage", "Elemental Resistances", "Chaos Resistance", "Lightning Damage",
    "Cold Damage", "Fire Resistance", "Cold Resistance", "Lightning Resistance", "Movement Speed", "Maximum Life",
    "Projectiles", "Projectile", "Projectile Damage", "Projectile Speed", "Melee Damage", "Skill Speed", "Life Recovery",
    "Life Regeneration", "Rarity of Items", "Light Radius", "Passive Skills", "Global Evasion Rating", "Supported Skills",
    "Skill Effect Duration", "Cooldown Recovery Rate", "Stun Threshold", "Deflection Rating", "Runic Ward", "Attribute Requirements"}


def _load_yaml(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or [] if path.exists() else []


def catalog_entries() -> list[dict]:
    return _load_yaml(MECH_DIR / "catalog.yaml")


def keywords() -> list[dict]:
    path = MECH_DIR / "keywords_poe2db.json"
    return json.loads(path.read_text(encoding="utf-8"))["keywords"] if path.exists() else []


@lru_cache(maxsize=1)
def config_options(src: Path = POB_SRC) -> list[dict]:
    """PoB's Configuration options: var, label, kind, and what each sets."""
    text = (src / "Modules" / "ConfigOptions.lua").read_text(encoding="utf-8", errors="replace")
    starts = [m.start() for m in re.finditer(r'\{\s*var\s*=\s*"', text)]
    found = []
    for a, b in zip(starts, starts[1:] + [len(text)]):
        block = text[a:b]
        var = re.match(r'\{\s*var\s*=\s*"([^"]+)"', block).group(1)
        label = re.search(r'label\s*=\s*"([^"]*)"', block)
        kind = re.search(r'type\s*=\s*"([^"]*)"', block)
        sets = sorted(set(re.findall(r'NewMod\("((?:Condition|Multiplier):[\w\']+)"', block)))
        clean = re.sub(r"\^x[0-9A-Fa-f]{6}|\^\d", "", label.group(1)) if label else var
        found.append({"var": var, "label": clean, "kind": kind.group(1) if kind else "", "sets": sets})
    return found


def terms_of(text: str) -> list[str]:
    return [t for t in TERM.findall(text) if t not in PLAIN]


def _pattern(names: list[str]) -> re.Pattern:
    alts = sorted({re.escape(n) for n in names if n}, key=len, reverse=True)
    return re.compile(r"\b(?:%s)\w*" % "|".join(alts), re.I)


def build(data_dir: Path | None = None) -> dict:
    from builder.data import data_dir as default_dir
    from builder.search.conditions import CONDITIONS

    data_dir = data_dir or default_dir()
    report = json.loads((data_dir / "coverage-report.json").read_text(encoding="utf-8"))
    raw = json.loads((data_dir / "coverage.json").read_text(encoding="utf-8"))
    gems = json.loads((data_dir / "gems.json").read_text(encoding="utf-8"))["gems"]
    records = report["records"]
    # Every line PoB counts (parsed, read) as well: totals per mechanic, so a mechanic PoB handles shows as full
    counted = [(l["line"], "passive" if "node" in l else "unique") for l in raw["passives"] + raw["uniques"]]
    counted += [(l["line"], "affix") for l in raw["affixes"] if l.get("supported")]
    gem_texts = [(g["name"], g.get("description") or "") for g in gems]
    turned_on = {c.var for c in CONDITIONS} | {"usePowerCharges", "useFrenzyCharges", "useEnduranceCharges"}
    options = config_options()

    # 1-4: the mechanics: catalog entries first, then keywords and frequent terms no entry covers
    mechanics: list[dict] = []
    covered_terms: set[str] = set()
    for entry in catalog_entries():
        names = [entry["name"], *(entry.get("terms") or [])]
        covered_terms |= {n.lower() for n in names}
        mechanics.append({**entry, "origin": "catalog", "names": names})
    for kw in keywords():
        if kw["name"].lower() in covered_terms:
            continue
        covered_terms.add(kw["name"].lower())
        mechanics.append({"id": "kw-" + re.sub(r"\W+", "-", kw["name"].lower()).strip("-"), "name": kw["name"],
                          "category": "keyword (to plan)", "game": kw.get("definition", ""),
                          "game_source": "https://poe2db.tw/us/Keywords" + (" [legacy]" if kw.get("legacy") else ""),
                          "origin": "poe2db keyword", "names": [kw["name"]], "plan": None})
    term_counts = Counter(t for r in records if r["status"] == "none" for t in terms_of(r["line"]))
    for term, n in term_counts.most_common():
        if n < 3 or term.lower() in covered_terms or any(term.lower() in c for c in covered_terms):
            continue
        covered_terms.add(term.lower())
        mechanics.append({"id": "term-" + re.sub(r"\W+", "-", term.lower()).strip("-"), "name": term,
                          "category": "game term (to plan)", "game": "", "origin": "capitalised term in the lines",
                          "names": [term], "plan": None})

    # Link lines, gem stats, gems and Configuration options to each mechanic, and work out what PoB does
    for mech in mechanics:
        pat = _pattern(mech["names"])
        linked = [r for r in records if pat.search(r["line"].replace("_", " "))]
        none = [r for r in linked if r["status"] == "none"]
        total_counted = sum(1 for text, _ in counted if pat.search(text))
        gems_using = [name for name, desc in gem_texts if pat.search(desc)]
        configs = [o for o in options if pat.search(o["label"]) or pat.search(" ".join(o["sets"]))]
        configs += [o for o in options if o["var"] in (mech.get("configs") or []) and o not in configs]
        by_cat, by_kind, by_status = Counter(), Counter(), Counter()
        for r in linked:
            by_cat[r["category"]] += 1
            by_status[r["status"]] += 1
            if r["status"] == "none":
                by_kind[r["source_kind"]] += 1
        misparse = sum(1 for r in none if r["category"] == "misparse")
        never = sum(1 for r in none if r["category"] == "never-on")
        uncounted = len(none)
        if misparse and misparse >= uncounted / 2:
            computed = "wrong"
        elif never and configs and never >= uncounted / 2:
            computed = "config-only"
        elif uncounted == 0 and (total_counted or linked):
            computed = "full"
        elif uncounted and (total_counted + len(linked) - uncounted) > 0:
            computed = "partial"
        elif uncounted:
            computed = "none"
        else:
            computed = "no lines"
        weight = sum(r["weight"] * {"damage": 4, "defence": 3, "speed": 3, "recovery": 2.5}.get(r["mechanic"], 0.3) for r in none)
        mech.update({
            "pob_computed": computed,
            "lines": {"linked": len(linked), "not_counted": uncounted, "counted_by_pob_elsewhere": total_counted,
                      "by_category": dict(by_cat), "by_status": dict(by_status), "not_counted_by_source": dict(by_kind)},
            "examples": [{"line": r["line"], "source": r["source"], "category": r["category"], "status": r["status"],
                          "reason": r["reason"][:200]} for r in (none or linked)[:12]],
            "all_lines": [{"line": r["line"], "source": r["source"], "category": r["category"], "status": r["status"]}
                          for r in none[:400]],
            "gems": gems_using[:40], "gem_count": len(gems_using),
            "configs": [{"var": o["var"], "label": o["label"], "kind": o["kind"],
                         "optimizer": "on (condition resolver)" if o["var"] in turned_on else "off"} for o in configs[:20]],
            "impact_weight": round(weight, 1),
        })
        impact = mech.get("impact")  # 1-5, reviewed; automatic entries rank by their lines alone
        reach = weight + 2 * len(gems_using)
        mech["priority"] = round(reach * (impact or 1) / (1 if impact else 3), 1)
    mechanics.sort(key=lambda m: (-(m["priority"] or 0), m["name"]))
    summary = {
        "mechanics": len(mechanics), "reviewed": sum(1 for m in mechanics if m["origin"] == "catalog"),
        "by_pob": dict(Counter((m.get("pob") or m["pob_computed"]) for m in mechanics)),
        "by_origin": dict(Counter(m["origin"] for m in mechanics)),
        "config_options": len(options), "config_options_on": sum(1 for o in options if o["var"] in turned_on),
        "keywords": len(keywords()),
    }
    return {"data_dir": str(data_dir), "summary": summary, "mechanics": mechanics}


def markdown(cat: dict) -> str:
    s = cat["summary"]
    out = ["# PoE2 mechanics PoB doesn't simulate (or simulates wrongly)", "",
           "Generated by `uv run python -m builder.ledger.mechanics` from the coverage audit and `ledger/mechanics/`. "
           "Do not edit by hand.", "",
           f"{s['mechanics']} mechanics ({s['reviewed']} reviewed with a plan; the rest found automatically and still to plan). "
           f"Sources: poe2db's keyword glossary ({s['keywords']} keywords), capitalised terms in every line and gem "
           f"description, PoB's {s['config_options']} Configuration options ({s['config_options_on']} turned on by the "
           "optimizer), and systemic rules.", "",
           "PoB status, worked out from the audit's evidence: **none** (no line counted), **partial** (some), **config-only** "
           "(a Configuration option switches it on, which the optimizer leaves off), **wrong** (parses that lost part of "
           "the line), **full** (every linked line counted).", ""]
    groups = defaultdict(list)
    for m in cat["mechanics"]:
        groups[m["category"]].append(m)
    order = sorted(groups, key=lambda g: ("to plan" in g, g))
    out.append("| Category | Mechanics |")
    out.append("|---|---|")
    out += [f"| {g} | {len(groups[g])} |" for g in order]
    out.append("")
    for g in order:
        out.append(f"## {g}")
        out.append("")
        for m in groups[g]:
            if "to plan" in g and not m["lines"]["not_counted"] and not m["gem_count"]:
                continue  # automatic entries with nothing uncounted aren't listed in the document (they are in the JSON)
            pob = m.get("pob") or m["pob_computed"]
            ln = m["lines"]
            out.append(f"### {m['name']} — PoB: {pob}" + (f" (lines say: {m['pob_computed']})" if m.get("pob") and m.get("pob") != m["pob_computed"] else ""))
            out.append(f"Priority {m['priority']} · impact {m.get('impact', '?')} · effort {m.get('effort', '?')} · "
                       f"{ln['not_counted']} lines not counted of {ln['linked'] + ln['counted_by_pob_elsewhere']} linked · "
                       f"{m['gem_count']} gems mention it · what-ifs: {m.get('what_if', '?')}")
            out.append("")
            if m.get("game"):
                out.append(f"- **Game:** {m['game']}" + (f" ({m['game_source']})" if m.get("game_source") else ""))
            if m.get("pob_check"):
                out.append(f"- **PoB (checked):** {m['pob_check']}")
            if m.get("handling"):
                out.append(f"- **Now:** {m['handling']}")
            plan = m.get("plan")
            if plan:
                out.append(f"- **Plan:** {plan.get('approach', '')}: {plan.get('how', '')}"
                           + (f" Inputs: {', '.join(plan['inputs'])}." if plan.get("inputs") else "")
                           + (f" On while: {plan['sustain']}." if plan.get("sustain") else ""))
            else:
                out.append("- **Plan:** to plan")
            if m.get("guide_note"):
                out.append(f"- **Guide note:** {m['guide_note']}")
            if m["configs"]:
                out.append("- **PoB options:** " + ", ".join(f"`{c['var']}` ({c['optimizer']})" for c in m["configs"][:6]))
            if m["examples"]:
                out.append("- **Lines:** " + "; ".join(f"`{e['line'][:80]}` ({e['source'][:30]})" for e in m["examples"][:4]))
            out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    from builder.data import data_dir

    cat = build()
    out = data_dir() / "mechanics.json"
    out.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(markdown(cat), encoding="utf-8")
    s = cat["summary"]
    print(f"{s['mechanics']} mechanics ({s['reviewed']} reviewed); PoB: {s['by_pob']}; from: {s['by_origin']}")
    print(f"wrote {out} and {DOC}")
    for m in cat["mechanics"][:int(argv[argv.index('--top') + 1]) if '--top' in argv else 25]:
        print(f"  [{m['priority']:7.1f}] {m['name'][:40]:40} PoB {m.get('pob') or m['pob_computed']:12} "
              f"{m['lines']['not_counted']:4} lines not counted, {m['gem_count']} gems ({m['origin']})")


if __name__ == "__main__":
    main(sys.argv[1:])

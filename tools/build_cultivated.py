"""Build builder/data/cultivated.json: which uniques the Vaal Cultivation Orb works on and each one's outcomes (CULT1).

Sources, in the project's order:
- The game files (build 26476662): Mods.json's 204 'UniqueMutatedVaal*' rows with GenerationType 3 are the outcome mods
  (their stats and ranges); Incursion2MutatedUniqueModsClient's one row ('OriginalMods', 242 mods) lists the unique mods
  an orb may replace; Incursion2Crafting's 'Mutate Unique' row is the orb ("Replaces up to 2 modifiers on a [Corrupted]
  [UniqueCulture|Vaal Unique]"). The client files carry no outcome weights (the 204 rows' SpawnWeight lists are empty)
  and do not say which unique gets which outcome (that pairing is server-side).
- poe2db's Vaal Cultivation Orb page (CurrencyIncursionMutateUnique, saved 14 Nov 2026 by AUDIT32): "Cultivated
  Uniques /48", each with its explicit lines and its outcome lines. Its outcome texts are matched to the game's mods
  by text and range; poe2db's ranges lose to the game's where they differ (written down in "conflicts").
- PoB (vendor/PathOfBuilding-PoE2): ModItemExclusive.lua's text for each outcome mod (the text our item text uses),
  and data/pob-*/coverage.json (bridge.auditCoverage, a real engine run) for whether PoB parses each line whole.

Run: python tools/build_cultivated.py <poe2db vco.html> <export dir holding Incursion2MutatedUniqueModsClient.json>
"""
from __future__ import annotations

import html as H
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "data" / "game-26476662" / "tables"
POB = ROOT / "vendor" / "PathOfBuilding-PoE2" / "src" / "Data" / "ModItemExclusive.lua"
COVERAGE = ROOT / "data" / "pob-bb52d6b368" / "coverage.json"
OUT = ROOT / "builder" / "data" / "cultivated.json"

# poe2db outcome texts its page renders differently from PoB's text of the same game mod (checked by stat id).
OUTCOME_BY_HAND = {
    ("Rise of the Phoenix", "Burning Enemies you kill"): "UniqueMutatedVaalBurningEnemiesExplodeChance",
    ("Drillneck", "Arrows Pierce 1 additional"): "UniqueMutatedVaalAdditionalArrowPierce",
    ("Flesh Crucible", "Random 1 Keystone"): "UniqueMutatedVaalRandomKeystoneFromTable",
    # The Adorned's "(-30-30) to maximum Energy Shield": the one game mod with that range (base_maximum_energy_shield
    # -30..30); the other '+X to maximum Energy Shield' outcomes have other ranges.
    ("The Adorned", "(-30-30) to maximum Energy Shield"): "UniqueMutatedVaalIncreasedEnergyShieldForJewel",
}
# Unique lines whose poe2db text lost spaces or carries markers ('ofyour', 'Liferathpith surge [1]'), paired by meaning
# with the replaceable game mod (each id is checked to be in OriginalMods below).
LINE_BY_HAND = {
    ("Bloodbarrier", "Inflict Corrupted Blood"): "UniqueApplyCorruptedBloodOnBlock1",
    ("Rathpith Globe", "Non-Channelling Spells cost"): "UniqueNonChannellingSpellLifeCost1",
    ("Rathpith Globe", "Non-Channelling Spells deal"): "UniqueNonChannellingSpellDamage1",
    ("Rathpith Globe", "Non-Channelling Spells have 3% increased Critical"): "UniqueNonChannellingSpellCriticalChance1",
    ("The Adorned", "(0-150)% increased Effect of Jewel"): "CorruptedMagicJewelModEffectUnique__1",
    ("Flesh Crucible", "Random 1 Keystone"): "UniqueRandomKeystoneFromTable1",
    ("The Flesh Poppet", "Copy a random Modifier"): "UniqueGainAModifierFromEachEnemyInPresenceOnShapeshift1",
}
# Outcomes the search must not offer as a fixed line, with why (never dropped silently: they stay in the data).
NOT_OFFERED = {
    "UniqueMutatedVaalRandomKeystoneFromTable": "grants one of 33 random keystones (stat local_additional_vivisection_"
    "random_keystone_index 1-33): which keystone is decided per item, and PoB reads the line '(1-33)' as nothing; "
    "offering it needs one variant per keystone (CULT follow-up)",
}
# Fate of the Vaal outcome changes in 0.5.0's notes, against the game files (the game files win; both written down).
CONFLICTS = [
    {"unique": "Hateforge", "mod": "UniqueMutatedVaalMaximumRagePerGlorySkillUsed",
     "game": "+(8-10) maximum Rage if you've used a Skill that Requires Glory in the past 20 seconds (Mods.json, "
             "build 26476662; poe2db agrees)",
     "patch_notes": "0.5.0: 'can now roll +16-30 maximum Rage if you've used a Skill that Requires Glory in the past "
                    "20 seconds'",
     "used": "the game files' 8-10 (the lower number: no overcount if the notes are right about a later server value)"},
]


def text(s: str) -> str:
    s = re.sub(r'<span class="ndash">.*?</span>', "-", s)
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def poe2db_cards(page: str) -> dict:
    a = page.find('id="CultivatedUniques"')
    b = page.find('id="CultivatedMods"')
    out = {}
    for card in page[a:b].split('<div class="flex-grow-1 ms-2">')[1:]:
        name = text(re.match(r'\s*<(span|a) class="UniqueItems UniqueItem"[^>]*>(.*?)</\1>', card).group(2))
        cut = card.find("mutatedMods")
        out[name] = {
            "lines": [text(x) for x in re.findall(r'<div class="explicitMod">(.*?)</div>', card[:cut])],
            "outcomes": [text(x) for x in re.findall(r'<div class="poe2 mutatedMod"><div>(.*?)</div></div>', card[cut:])],
        }
    return out


def exact(s: str) -> str:
    s = s.replace("—", "-").replace("Skillscontaining", "Skills containing").replace("atenth", "a tenth")
    return re.sub(r"\s+", " ", s).strip().lower()


def loose(s: str) -> str:
    return re.sub(r"[+-]?\(?-?[\d.]+(?:-+[\d.]+)?\)?", "#", exact(s))


def main(vco_html: str, export_dir: str) -> None:
    mods = json.loads((GAME / "Mods.json").read_text(encoding="utf-8"))
    stats = json.loads((GAME / "Stats.json").read_text(encoding="utf-8"))
    by_id = {r["Id"]: r for r in mods}
    replaceable = {mods[i]["Id"] for i in json.loads(
        (Path(export_dir) / "Incursion2MutatedUniqueModsClient.json").read_text(encoding="utf-8"))[0]["Mods"]}
    outcome_rows = [r for r in mods if "Mutated" in r["Id"] and r["GenerationType"] == 3]
    pob_src = POB.read_text(encoding="utf-8")
    pob = {m.group(1): [x.replace("\\n", " ") for x in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))]
           for m in re.finditer(r'\["(\w+)"\] = \{ affix = "", (.*?), statOrder', pob_src)}
    coverage = {}
    for row in json.loads(COVERAGE.read_text(encoding="utf-8"))["affixes"]:
        coverage.setdefault(row["id"], []).append((row["line"], row["supported"]))

    def stat_ranges(r: dict) -> list:
        return [[stats[r[f"Stat{i}"]]["Id"], *r[f"Stat{i}Value"]] for i in range(1, 9)
                if r.get(f"Stat{i}") is not None and r.get(f"Stat{i}Value")]

    by_text: dict[str, list] = {}
    for r in outcome_rows:
        if r["Id"] in pob:
            by_text.setdefault(loose(" ".join(pob[r["Id"]])), []).append(r)
    orig_by_text: dict[str, list] = {}
    for oid in replaceable:
        for line in pob.get(oid, []):
            orig_by_text.setdefault(loose(line), []).append(oid)

    cards = poe2db_cards(Path(vco_html).read_text(encoding="utf-8", errors="replace"))
    uniques, problems = {}, []
    for name, card in cards.items():
        outcomes = []
        for o in card["outcomes"]:
            mod_id = next((v for (u, start), v in OUTCOME_BY_HAND.items() if u == name and o.startswith(start)), None)
            if mod_id is None:
                cands = by_text.get(loose(o), [])
                pick = [c for c in cands if exact(" ".join(pob[c["Id"]])) == exact(o)] if len(cands) > 1 else cands
                if len({json.dumps(stat_ranges(c)) for c in pick}) == 1:
                    pick = pick[:1]  # same stats and ranges: the same outcome
                if len(pick) != 1:
                    problems.append((name, o, [c["Id"] for c in cands]))
                    continue
                mod_id = pick[0]["Id"]
            row = by_id[mod_id]
            lines = pob.get(mod_id, [])
            covered = coverage.get(mod_id, [])
            outcomes.append({
                "mod": mod_id,
                "lines": lines,
                "stats": stat_ranges(row),
                "pob_reads": bool(covered) and all(ok for _, ok in covered),
                "pob_unread_lines": [line for line, ok in covered if not ok],
                **({"not_offered": NOT_OFFERED[mod_id]} if mod_id in NOT_OFFERED else {}),
            })
        lines = []
        for line in card["lines"]:
            ids = sorted(orig_by_text.get(loose(line), []))
            hand = next((v for (u, start), v in LINE_BY_HAND.items() if u == name and line.startswith(start)), None)
            if hand is not None:
                if hand not in replaceable:
                    raise SystemExit(f"{name}: {hand} is not in the game's OriginalMods")
                ids = [hand]
            lines.append({"text": line, "replaceable": bool(ids)})
        uniques[name] = {"lines": lines, "outcomes": outcomes}
    if problems:
        raise SystemExit(f"outcomes not matched to one game mod: {problems}")
    used = {o["mod"] for u in uniques.values() for o in u["outcomes"]}
    data = {
        "_about": {
            "what": "Vaal Cultivation Orb outcomes ('Cultivated' uniques; the game files say 'Mutated'): the orb "
                    "'Replaces up to 2 modifiers on a [Corrupted] [UniqueCulture|Vaal Unique]' (Incursion2Crafting, "
                    "Mutate Unique). Added in 0.4.0 (Fate of the Vaal), core since 0.5.0 (patch notes 0.5.0 line 173).",
            "sources": "game files build 26476662 (Mods.json GenerationType 3 'UniqueMutatedVaal*'; "
                       "Incursion2MutatedUniqueModsClient 'OriginalMods'); poe2db Vaal Cultivation Orb page (48 "
                       "uniques, saved 14 Nov 2026); PoB ModItemExclusive.lua text; data/pob-bb52d6b368/coverage.json",
            "weights": "UNVERIFIED: the client files carry no outcome weights (the 204 rows' SpawnWeight lists are "
                       "empty) and poe2db shows none; how many lines are replaced (1 or 2) and which line goes is random "
                       "(poe2db's wiki text: 'The modifier that gets replaced on the unique are random'), odds unknown.",
            "replaceable": "a line's 'replaceable' is true when its text matches one of the game's 242 OriginalMods; "
                           "false means only that the text did not match (UNVERIFIED, not 'cannot be replaced')",
            "conflicts": CONFLICTS,
            "outcome_mods_in_game_files": len(outcome_rows),
            "outcome_mods_on_listed_uniques": len(used),
            "outcome_mods_on_no_listed_unique": sorted(r["Id"] for r in outcome_rows if r["Id"] not in used),
        },
        "uniques": uniques,
    }
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(uniques)} uniques, {sum(len(u['outcomes']) for u in uniques.values())} outcomes, "
          f"{len(used)} distinct mods -> {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:3])

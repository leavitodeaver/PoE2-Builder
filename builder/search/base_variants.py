"""Runeforged / Runemastered copies of uniques (COV32; the manager, 15 Nov 08:42: the copies' data is COV-A's).

Patch 0.5.0 (Runes of Aldur): "After unlocking Unique Verisium Runeforging in Act 3 you can upgrade the base type of
Unique Weapons and Armours that drop at levels lower than 55. Upgraded Unique Weapons will have higher damage, and
Upgraded Armours will have increased base defences as well as Runic Ward"; 0.5.5: "The Verisium Anvil is now unlocked by
completing The Runeseeker quest in Act 4 outside of the Runes of Aldur League" (league-obtainable). PoB's text for such a
unique lists "Base Variant: Regular Base / Runeforged / Runemastered", tags the base-name line and any base-specific line
with {base:N} (an implicit, "5% increased Movement Speed", a Runemastered-only mod) and selects one with "Selected Base
Variant: N" (1, the regular base, by default). Nothing in the tool set it, so every one of the 269 uniques with such
versions was scored on its regular base only (checked: git grep "Base Variant" over builder/: readers only).

base_variant_copies gives each non-regular version as its own text: the same text with "Selected Base Variant: N", and
the base PoB will use for it (the {base:N} base-name line). copy_price (COV33) prices a copy: poe.ninja's own line for
that version, or the plain unique + the Verisium Anvil recipe's materials, the cheaper; no price, no offer. Offering
them is SEARCH's hook (COV32 report section 9, COV33 report).
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

BASE_VARIANT = re.compile(r"^Base Variant: (.+)$", re.M)
SELECTED = re.compile(r"^Selected Base Variant: \d+$", re.M)
TAGGED = re.compile(r"^((?:\{[^}]*\})*)(.*)$")
BASE_TAG = re.compile(r"\{base:([\d,]+)\}")


def base_variants(raw: str) -> list[str]:
    """The unique's base versions as PoB names them ("Regular Base", "Runeforged", "Runemastered"), in order (1-based)."""
    return BASE_VARIANT.findall(raw)


def variant_base(raw: str, index: int, base_names: set[str]) -> str | None:
    """The base PoB uses for version `index`: the {base:index}-tagged line that is a base name."""
    for line in raw.split("\n"):
        tags, text = TAGGED.match(line).groups()
        for m in BASE_TAG.finditer(tags):
            if index in {int(x) for x in m.group(1).split(",")} and text.strip() in base_names:
                return text.strip()
    return None


def select(raw: str, index: int) -> str:
    """The text with base version `index` selected."""
    if SELECTED.search(raw):
        return SELECTED.sub(f"Selected Base Variant: {index}", raw, count=1)
    lines = raw.split("\n")
    at = max(i for i, l in enumerate(lines) if l.startswith("Base Variant:")) + 1
    return "\n".join(lines[:at] + [f"Selected Base Variant: {index}"] + lines[at:])


def base_variant_copies(unique: dict, base_names: set[str]) -> list[dict]:
    """Each non-regular base version of a unique ({"name", "base", "raw"} as in uniques.json), as
    {"version": "Runeforged" / "Runemastered", "index": N, "base": its base name, "raw": the text with it selected}.
    A version whose base PoB doesn't have is left out (UNVERIFIED that any is: none found on 15 Nov)."""
    copies = []
    for index, label in enumerate(base_variants(unique["raw"]), start=1):
        if index == 1 or label == "Regular Base":
            continue
        base = variant_base(unique["raw"], index, base_names)
        if base is None:
            continue
        copies.append({"version": label, "index": index, "base": base, "raw": select(unique["raw"], index)})
    return copies


# --- H4-4 (COV33): one copy per base row where a Runemastered base has rows that differ -------------------------------
# PoB keeps one row per base name (Data/Bases: one key per name, the last row wins), and the game has more than one for
# 3 names a unique sits on. Game files (BaseItemTypes + ArmourTypes + Implicit_Mods, build 26476662) give each row; poe2db's
# "Unique Runeforging" recipes (15 Nov; each recipe's result names its row) give which rows the anvil makes for which
# unique: the player picks the row (one recipe per row, same cost). Grasping Mail's 4 rows are rare-able bases, no
# unique's (COV32 section 7). A row is scored as PoB's kept row plus "LedgerMod:<Defence> BASE <row - kept>" lines (PoB's
# Item.lua calcLocal adds an item's untagged Armour / Evasion / EnergyShield / Ward BASE mod to its own base, before its
# % increases) and the row's own implicit in place of the kept row's.
ROWS = {
    # Svalinn: all 4 rows have a recipe. PoB keeps row 4 (Ar 162 / Ward 35, "Mana before Life").
    "Svalinn": {
        "base": "Runemastered Crucible Tower Shield",
        "kept": {"Armour": 162, "Ward": 35}, "kept_implicit": "of Damage is taken from Mana before Life",
        "rows": [
            {"row": "Runic Ward 250", "id": "FourShieldStr9VerisiumUnique1", "defences": {"Ward": 250}, "implicits": []},
            {"row": "Armour 100, Runic Ward 100", "id": "FourShieldStr9VerisiumUnique2",
             "defences": {"Armour": 100, "Ward": 100},
             "implicits": ["{range:0.5}Recover (15-25) Runic Ward when you Block"]},  # SvalinnVerisiumImplicitRunicWardOnBlock1
            {"row": "Evasion 148, Runic Ward 35", "id": "FourShieldStr9VerisiumUnique3",
             "defences": {"Evasion": 148, "Ward": 35}, "implicits": []},
            {"row": "Armour 162, Runic Ward 35", "id": "FourShieldStr9VerisiumUnique4", "kept": True},
        ],
    },
    # Eyes of the Runefather: recipes make rows 1 (Ar 203), 2 (ES 135) and 3 (a Buckler: Evasion 148). PoB keeps row 4
    # (Ar 112 / Ev 102), which no recipe makes (poe2db's 3 recipes' results; how it is obtained now: UNVERIFIED), so it
    # is priced from its poe.ninja line only. Row 3 is a Buckler (ItemClass 94, not a Shield; 0.5.2: "only the version
    # with Parry" is a Buckler): PoB scores this base as a Shield with Raise Shield, not a Buckler with Parry, so it is
    # not offered (an UNDERCOUNT for a build that wants the Evasion Buckler, named).
    "Eyes of the Runefather": {
        "base": "Runemastered Venerable Defender",
        "kept": {"Armour": 112, "Evasion": 102, "Ward": 53}, "kept_anvil": False,
        "rows": [
            {"row": "Armour 203", "id": "FourShieldStrDexUnique1VerisiumUnique1",
             "defences": {"Armour": 203, "Ward": 53}, "implicits": []},
            {"row": "Energy Shield 135", "id": "FourShieldStrDexUnique1VerisiumUnique2",
             "defences": {"EnergyShield": 135, "Ward": 53}, "implicits": []},
            {"row": "Armour 112, Evasion 102", "id": "FourShieldStrDexUnique1VerisiumUnique4", "kept": True},
        ],
    },
    # The Unleashed: recipes make rows 1, 2 and 3; PoB keeps row 3 (Ar 164 / ES 45 / Ward 301, Geofri's Sanctuary's too).
    "The Unleashed": {
        "base": "Runemastered Revered Vestments",
        "kept": {"Armour": 164, "EnergyShield": 45, "Ward": 301},
        "rows": [
            {"row": "Arcane Surge", "id": "FourBodyStrInt11VerisiumUnique1",
             "defences": {"Armour": 232, "EnergyShield": 64, "Ward": 86},
             "implicits": ["{range:0.5}(30-50)% increased effect of Arcane Surge on you"]},  # ...ArcaneSurgeEffect1
            {"row": "Energy Shield Recharge", "id": "FourBodyStrInt11VerisiumUnique2",
             "defences": {"Armour": 199, "EnergyShield": 88, "Ward": 86},
             # TheUnleashedVerisiumImplicitBypassEnergyShield1 (0.5.1: "now grants 10-15% increased Energy Shield
             # Recharge Rate")
             "implicits": ["{range:0.5}(10-15)% increased Energy Shield Recharge Rate"]},
            {"row": "Runic Ward 301", "id": "FourBodyStrInt11VerisiumUnique3", "kept": True},
        ],
    },
}
DEFENCES = ("Armour", "Evasion", "EnergyShield", "Ward")


def _row_raw(raw: str, spec: dict, row: dict) -> str:
    """The copy's text on `row`: the kept row's implicit swapped for the row's, and the defence differences as lines."""
    lines = raw.split("\n")
    out, count_at = [], None
    for line in lines:
        if spec.get("kept_implicit") and spec["kept_implicit"] in line:
            continue
        out.append(line)
        if line.startswith("Implicits: "):
            count_at = len(out) - 1
    if count_at is None:
        raise ValueError("no Implicits line")
    count = int(out[count_at].split(":")[1]) - (len(lines) - len(out)) + len(row["implicits"])
    out[count_at] = f"Implicits: {count}"
    out[count_at + 1:count_at + 1] = row["implicits"]
    kept = spec["kept"]
    for d in DEFENCES:
        delta = row["defences"].get(d, 0) - kept.get(d, 0)
        if delta:
            out.append(f"LedgerMod:{d} BASE {delta}")
    return "\n".join(out)


def row_copies(unique_name: str, copy: dict) -> list[dict]:
    """The copy once per base row (ROWS), each with "row", "row_id" and "anvil" (a recipe makes it); other copies as
    they are, with anvil True."""
    spec = ROWS.get(unique_name)
    if spec is None or copy["base"] != spec["base"]:
        return [dict(copy, anvil=True)]
    out = []
    for row in spec["rows"]:
        if row.get("kept"):
            out.append(dict(copy, row=row["row"], row_id=row["id"], anvil=spec.get("kept_anvil", True)))
        else:
            out.append(dict(copy, row=row["row"], row_id=row["id"], anvil=True, raw=_row_raw(copy["raw"], spec, row)))
    return out


def offered_copies(unique: dict, base_names: set[str]) -> list[dict]:
    """Every copy the search may offer: base_variant_copies, split per base row where the rows differ (ROWS)."""
    return [r for c in base_variant_copies(unique, base_names) for r in row_copies(unique["name"], c)]


# --- What a copy costs (COV33; the manager, 15 Nov: "no price, no offer") -------------------------------------------
# Two routes, the cheaper one wins, both shown:
#  - market: poe.ninja lists the upgraded versions on their own (baseType "Runemastered Crucible Tower Shield" ...; 260
#    uniques in the 25 Oct cache), so a copy has a plain poe.ninja price when anyone lists it;
#  - craft: the unique on the recipe's input base (its own poe.ninja line) + the Verisium Anvil recipe's materials at
#    poe.ninja's exchange prices (Verisium, Exceptional Verisium and the four Kalguuran Crests are all on its "Verisium"
#    exchange). A recipe always succeeds (one try: the anvil shows the result before you forge it, 0.5.2 "the Verisium
#    Anvil would not preview ..."), so its cost is the materials once.
# Recipes: builder/data/runeforging.json (poe2db "Unique Runeforging" and "Runeforging", 15 Nov). A unique's own recipe
# is used where poe2db has one; otherwise the base's plain Runeforging recipe (Verisium only), which the game applies to
# unique armours too (0.5.0: "Unique Armours above level 55 can still be Runeforged to modify their defences to include
# Runic Ward"; poe.ninja lists Runeforged copies of low-level unique armours too, e.g. Blackbraid on a Runeforged Fur
# Plate). That a unique pays the plain base's Verisium amount is MEDIUM (poe2db lists the recipe by base; a guide says the
# cost depends on the base alone, playnews.gg "Rune Save"; no source on uniques). No route = no price = not offered.

RUNEFORGING = Path(__file__).resolve().parents[1] / "data" / "runeforging.json"
UPGRADED = ("Runeforged ", "Runemastered ")


def ascii_name(name: str) -> str:
    """poe2db writes "Mjölner", PoB "Mjolner": compare names without accents."""
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()


@lru_cache(maxsize=1)
def recipes() -> tuple[dict, dict]:
    """({(unique, result base): [recipe, ...]}, {result base: [recipe, ...]}) from runeforging.json."""
    doc = json.loads(RUNEFORGING.read_text(encoding="utf-8"))
    unique: dict = {}
    for r in doc["unique_recipes"]:
        unique.setdefault((ascii_name(r["unique"]), r["to"]), []).append(r)
    base: dict = {}
    for r in doc["base_recipes"]:
        base.setdefault(r["to"], []).append(r)
    return unique, base


def market_price(table, unique: str, base: str):
    """poe.ninja's most-listed uncorrupted line for this unique on this exact base, or None."""
    lines = [p for p in (table.uniques.get(unique) or []) if p.base == base and not p.corrupted]
    return max(lines, key=lambda p: p.listings or 0, default=None)


def materials_price(table, materials: dict) -> tuple[float | None, list[str]]:
    """The materials at poe.ninja's exchange prices (None if any has no price), and their text."""
    total, text = 0.0, []
    for name, n in materials.items():
        p = table.item(name)
        if p is None or p.divine is None:
            return None, [f"{name} x{n}: no poe.ninja price"]
        total += p.divine * n
        text.append(f"{name} x{n} ({p.divine * n:.3g} div)")
    return total, text


def copy_price(table, unique: str, base: str, _seen: frozenset = frozenset(), anvil: bool = True,
               row: str | None = None) -> dict | None:
    """What the unique on `base` (a Runeforged / Runemastered base, or its plain base) costs:
    {"divine", "listings", "confidence", "how", "market", "craft"} or None when neither route has a price.
    anvil=False: no recipe makes this copy (a row_copies row), so its poe.ninja line is its only price. row: the base
    row (row_copies); poe.ninja lists a base name once, so its price is for any row of it (said in "how")."""
    market = market_price(table, unique, base)
    best = None
    if market is not None:
        any_row = f"; the price is for any row of {base}: check it's the {row} one" if row else ""
        best = {"divine": market.divine, "listings": market.listings, "confidence": market.confidence,
                "how": f"poe.ninja: {unique} on {base} ({market.listings} listed{any_row})"}
    craft = None
    if anvil and base.startswith(UPGRADED) and base not in _seen:
        by_unique, by_base = recipes()
        options = by_unique.get((ascii_name(unique), base)) or by_base.get(base) or []
        for r in options:
            if r["from"] == base or r["from"] in _seen:
                continue  # poe2db shows Geofri's Sanctuary's recipe from its own Runemastered base: no route from it
            source = copy_price(table, unique, r["from"], _seen | {base})
            cost, text = materials_price(table, r["materials"])
            if source is None or cost is None:
                continue
            kind = "its own anvil recipe" if "unique" in r else "the base's Runeforging recipe (MEDIUM: priced as the base)"
            option = {"divine": source["divine"] + cost, "listings": source["listings"],
                      "confidence": source["confidence"],
                      "how": f"{source['how']} + {kind}: " + ", ".join(text)}
            if craft is None or option["divine"] < craft["divine"]:
                craft = option
    if craft is not None and (best is None or craft["divine"] < best["divine"]):
        best = dict(craft)
    if best is None:
        return None
    best["market"] = None if market is None else round(market.divine, 4)
    best["craft"] = None if craft is None else round(craft["divine"], 4)
    return best

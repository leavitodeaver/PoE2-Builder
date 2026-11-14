"""Cultivated uniques: the Vaal Cultivation Orb's outcomes, per unique (CULT1; the user, 14 Nov 2026: "Yes, now").

The orb "Replaces up to 2 modifiers on a [Corrupted] [UniqueCulture|Vaal Unique]" (game files, Incursion2Crafting's
'Mutate Unique' row). Its outcomes are the game's 'UniqueMutatedVaal*' mods. builder/data/cultivated.json (built by
tools/build_cultivated.py, sources in its "_about") lists the 48 uniques poe2db names, each one's explicit lines (and
whether each matches a line the game lets the orb replace) and its outcomes with the game's ranges, PoB's text, and
whether PoB parses the outcome whole (data/pob-*/coverage.json).

What the data cannot say (UNVERIFIED, from the game client and poe2db both): outcome weights, the odds of one line
replaced against two, and which line goes (random). Pricing and offering (CULT, next) must label that.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from builder import clock

DATA = Path(__file__).resolve().parents[1] / "data" / "cultivated.json"
ODDS = Path(__file__).resolve().parents[1] / "data" / "cultivated_odds.json"
ORB = "Vaal Cultivation Orb"
VAAL_ORB = "Vaal Orb"  # corrupts the plain copy the orb needs
LABEL = "Cultivated"
TOP_OUTCOMES = 3  # best single outcomes (by what-if gain) tried as whole variants


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def odds() -> dict:
    """The orb's odds as data (builder/data/cultivated_odds.json): the model the price uses and the comparison models
    the price note shows beside it. No game file gives them (LOW / UNVERIFIED, sources in the file)."""
    return json.loads(ODDS.read_text(encoding="utf-8"))


def uniques() -> dict[str, dict]:
    return load()["uniques"]


def cultivatable(name: str) -> bool:
    return name in uniques()


def outcome_lines(o: dict) -> list[str] | None:
    """An outcome's lines as an item carries them, through the same ledger corrections context.corrected_raw gives a
    plain unique's lines: a line PoB doesn't read whole gets the ledger's stand-in (ledger.translate, item entries:
    COV27's lines for the 30 outcomes land here) after it; a line PoB reads wrongly is replaced or switched off
    (ledger.wrong). None while a line PoB doesn't read has no stand-in: such an outcome can't be scored yet."""
    from builder.ledger import item_lines, translate, wrong

    unread = set(o.get("pob_unread_lines") or ([] if o["pob_reads"] else o["lines"]))
    lines: list[str] = []
    for line in o["lines"]:
        if line in unread:
            translated = translate(line)
            if not (translated and translated[0].on_items):
                return None
            lines += [line] + item_lines(translated[1])
        elif fixed := wrong(line):
            lines.append(fixed[1] if fixed[1] is not None else "{disabled}" + line)
        else:
            lines.append(line)
    return lines


def outcomes(name: str, offered: bool = True) -> list[dict]:
    """A unique's outcomes. offered=True keeps those the search can score as a fixed line: PoB reads them whole, or the
    ledger stands in for what it doesn't (outcome_lines), and nothing marks them 'not_offered'. The rest stay in the
    data (an outcome PoB doesn't read is a coverage gap for the ledger, not a dropped mechanic; Flesh Crucible's random
    keystone needs one variant per keystone)."""
    found = uniques().get(name, {}).get("outcomes", [])
    if not offered:
        return list(found)
    return [o for o in found if "not_offered" not in o and outcome_lines(o) is not None]


def replaceable_lines(name: str) -> list[str]:
    """The unique's explicit lines (poe2db's text) the orb may replace, as matched to the game's OriginalMods."""
    return [line["text"] for line in uniques().get(name, {}).get("lines", []) if line["replaceable"]]


def unread_outcomes() -> list[tuple[str, str, list[str]]]:
    """(unique, outcome mod, its lines) for every outcome PoB does not parse whole: the ledger's open items."""
    return [(name, o["mod"], o["pob_unread_lines"] or o["lines"])
            for name, u in uniques().items() for o in u["outcomes"] if not o["pob_reads"]]


# Prices ------------------------------------------------------------------------------------------------------------

def payload_prices(prices) -> dict:
    """The orb's and the Vaal Orb's poe.ninja prices, for the search's payload (context.make_payload). Empty without
    the orb's: then nothing is crafted (an unpriced orb is never free). Without the Vaal Orb's, only "orb"."""
    if prices is None or prices.item(ORB) is None:
        return {}
    out = {"orb": prices.item(ORB).divine}
    if prices.item(VAAL_ORB) is not None:
        out["vaal"] = prices.item(VAAL_ORB).divine
    return out


def _share(table: dict, count: int) -> float:
    return float(table.get(str(count), 0.0))


def success_chance(n_outcomes: int, k_lines: int, wanted: int, acceptable: int, model: dict | None = None) -> float:
    """One orb use's chance of exactly the item scored: `wanted` (1 or 2) specific outcomes of the unique's n (each
    equally likely, never the same twice: UNVERIFIED), replacing `wanted` lines all among the `acceptable` of its k
    replaceable lines (the line that goes is random). `model` is an odds model (cultivated_odds.json; its "model" when
    None): "paired" = one count for lines off and outcomes on; "separate" = independent counts added and removed. A
    result with an extra unwanted outcome, or a line lost that the build needs, is a miss (no overcount)."""
    model = model or odds()["model"]
    if wanted < 1 or n_outcomes < wanted or k_lines < wanted or acceptable < wanted:
        return 0.0
    outcomes_hit = 1.0 / math.comb(n_outcomes, wanted)
    lines_hit = math.comb(min(acceptable, k_lines), wanted) / math.comb(k_lines, wanted)
    if model["kind"] == "paired":
        return _share(model["lines"], wanted) * outcomes_hit * lines_hit
    if model["kind"] == "separate":
        return _share(model["add"], wanted) * outcomes_hit * _share(model["remove"], wanted) * lines_hit
    raise ValueError(f"unknown odds model kind {model['kind']!r}")


def craft_price(ctx, name: str, plain_price: float | None, chances: dict[str, float]) -> tuple[float, str] | None:
    """(divines, note) for one cultivated copy: the expected uses x (a fresh corrupted copy + one orb). The manager's
    rule (14 Nov ~05:10, the user's question "how do you price everything else that's not on poe ninja?") was the
    plain unique's poe.ninja price + the orb's x the expected uses; CULT5 found that every use needs a fresh copy: the
    game refuses an orb on a unique that already carries an outcome (ClientStrings
    ItemErrorIncursionAlreadyMutatedUniqueMods, "Target already has a Vaal Unique Modifier."; the orb needs a Corrupted
    Vaal unique, CurrencyItems row 58), so a miss keeps its wrong outcome for good and the next try starts on another
    copy. A copy = the plain unique's price + a Vaal Orb to corrupt it (the corruption's own outcome is not counted:
    UNVERIFIED). A miss is worth something on the market, but no price for it exists: not taken off (the cautious
    side: the craft may be priced high, never low). No plain price (a unique poe.ninja doesn't list is never offered),
    no orb price or a zero chance: None. `chances` is {model name: one use's chance}; the first is the model priced,
    the rest are shown as comparison rows."""
    costs = ctx.payload.get("cultivation_prices") or {}
    orb = costs.get("orb")
    names = list(chances)
    if plain_price is None or orb is None or not names or chances[names[0]] <= 0.0:
        return None
    vaal = costs.get("vaal")
    per_use = plain_price + (vaal or 0.0) + orb
    uses = 1.0 / chances[names[0]]
    price = per_use * uses
    rows = []
    for other in names[1:]:
        if chances[other] > 0.0:
            rows.append(f"{other}: ~{1.0 / chances[other]:.0f} uses, {per_use / chances[other]:.0f} div")
        else:
            rows.append(f"{other}: never")
    corrupt = f"a {VAAL_ORB} ({vaal:.2f} div)" if vaal is not None else f"a {VAAL_ORB} (no price: not counted)"
    note = (f"craft estimate, LOW: ~{uses:.0f} tries x (a fresh copy of the unique ({plain_price:.2f} div) + {corrupt}"
            f" + a {ORB} ({orb:.2f} div)): a cultivated unique can't take another orb, so each miss uses up a copy "
            f"(its resale not counted) ({names[0]}; outcomes equally likely, UNVERIFIED)")
    if rows:
        note += "; compare " + "; ".join(rows)
    return price, note


def chances(n_outcomes: int, k_lines: int, wanted: int, acceptable: int) -> dict[str, float]:
    """One use's chance under the odds model and each comparison model, by name (the model first)."""
    table = odds()
    return {m["name"]: success_chance(n_outcomes, k_lines, wanted, acceptable, m)
            for m in [table["model"], *table.get("compare", [])]}


# Item text ---------------------------------------------------------------------------------------------------------

HEADER = re.compile(r"^(?:Rarity|Source|Variant|Selected Variant|Base Variant|Selected Base Variant|Limited to|Radius|"
                    r"Quality|LevelReq|Item Level|Sockets|Rune|Implicits|League|Requires|Has Alt Variant)\b.*:")


def _key(s: str) -> str:
    """poe2db's and PoB's texts of one mod compared: numbers and ranges as '#', no spaces (poe2db's markup drops some:
    'ofyour'), poe2db's markers ('rathpith surge [1]', '[1,33]') gone."""
    s = re.sub(r"rathpith surge \[\d+\]|\[[\d,]+\]", "", s.replace("—", "-"))
    s = re.sub(r"[+-]?\(?-?[\d.]+(?:-+[\d.]+)?\)?", "#", s)
    return re.sub(r"\s+", "", s).lower()


def _explicit(raw: str) -> list[tuple[int, str]]:
    """(index, text without tags) of a unique's explicit lines in its selected variant (the last one when the text names
    none, as PoB does; a {group:g} line by that group's own selection): past the implicits (or the header when there's no Implicits line), no enchants, sockets,
    blanks or bare keywords."""
    lines = raw.split("\n")
    variants = [line for line in lines if line.startswith("Variant:")]
    selected = next((int(m.group(1)) for line in lines if (m := re.match(r"Selected Variant: (\d+)", line))),
                    len(variants) or None)
    start = next((i + 1 + int(m.group(1)) for i, line in enumerate(lines)
                  if (m := re.match(r"Implicits: (\d+)", line))), 3)
    # "Selected Variant Group: 1=3": the variant chosen in group 1 (Atziri's Splendour picks its Soul Core slot line and
    # its defence line this way, with no "Selected Variant" line)
    groups = {m.group(1): int(m.group(2)) for line in lines
              if (m := re.match(r"Selected Variant Group: (\d+)=(\d+)", line))}
    found = []
    for i in range(start, len(lines)):
        tags, text = re.match(r"^((?:\{[^}]*\})*)(.*)$", lines[i]).groups()
        if variant := re.search(r"\{variant:([\d,]+)\}", tags):
            group = re.search(r"\{group:(\d+)\}", tags)
            chosen = groups.get(group.group(1)) if group else selected  # a variant group's own choice (Splendour)
            if chosen is None or str(chosen) not in variant.group(1).split(","):
                continue
        text = text.strip()
        if not text or "{enchant}" in tags or text in ("Corrupted", "Mirrored") or HEADER.match(text):
            continue
        found.append((i, text))
    return found


def explicit_lines(name: str, raw: str, original: str | None = None,
                   ignored: set[str] = frozenset()) -> list[tuple[tuple[int, ...], str]]:
    """(indices in raw, text) of the lines the orb may replace: the unique's explicit lines (its `original` PoB text;
    the item's own text when not given) that match a line poe2db shows on it, each of which is in the game's OriginalMods
    (checked: tools/build_cultivated.py, all 234). A mod PoB writes over two lines is one entry (both go). A PoB line
    poe2db doesn't show is not offered as the line lost (UNVERIFIED whether the orb can replace it: the cautious side).
    Found in `raw` by its exact text, so the ledger's stand-ins (appended) and in-place fixes are never the line lost; a
    line PoB ignores (`ignored`, the ledger's gaps) is never offered either: losing it would look free."""
    # poe2db's lines for this unique, and any of the unique's own lines whose text is one of the game's OriginalMods (the
    # game files list The Vertex's attribute-requirement lines there, and 0.4.0b fixed the orb not modifying them: A34-10)
    wanted = {_key(t) for t in replaceable_lines(name)} | {_key(t) for t in load()["_about"].get("original_lines", [])}
    own = Counter(text for _, text in _explicit(original if original is not None else raw))
    lines = []
    for i, text in _explicit(raw):  # each of the unique's own lines once, its first copy: a later copy is the ledger's
        if own[text] > 0:
            own[text] -= 1
            lines.append((i, text))
    found, skip = [], False
    for n, (i, text) in enumerate(lines):
        if skip:
            skip = False
            continue
        if _key(text) in wanted:
            parts = [(i, text)]
        elif n + 1 < len(lines) and lines[n + 1][0] == i + 1 and _key(text + lines[n + 1][1]) in wanted:
            parts, skip = [(i, text), lines[n + 1]], True
        else:
            continue
        if not any(t in ignored for _, t in parts):
            found.append((tuple(j for j, _ in parts), " ".join(t for _, t in parts)))
    return found


def edited(raw: str, drop: list[int], add: list[dict]) -> str:
    """The text with lines `drop` removed, each outcome's lines added as {mutated} at the middle roll, and Corrupted."""
    gone = set(drop)
    lines = [line for i, line in enumerate(raw.rstrip("\n").split("\n")) if i not in gone]
    lines += ["{mutated}{range:0.5}" + line for o in add for line in (outcome_lines(o) or [])]
    if "Corrupted" not in lines:
        lines.append("Corrupted")
    return "\n".join(lines)


def label(add: list[dict], dropped: list[str]) -> str:
    outcomes_text = " + ".join(o["mod"].removeprefix("UniqueMutatedVaal") for o in add)
    return f"{LABEL}: {outcomes_text} (replaces: {' / '.join(dropped)})"


# The search's trial ------------------------------------------------------------------------------------------------

def trial(gear, slot: str, target, deadline: float):
    """A cultivated copy of the slot's unique, when one beats it after its price (the gear round's full rounds, after
    sockets, anoints and shards; gear.GearSearch.gear_round's hook). What-ifs, in one batch: the unique as it is, with
    each offered outcome added, and with each replaceable line removed. Only outcomes that raise the score go on (what
    touches the build's damage, floors or recovery, as the objective weighs them); each is tried in place of the line
    the build loses least by, and the best two together in place of the two cheapest lines. A variant must beat the
    unique by EPSILON after the budget weight on its craft price. A trial that found nothing isn't repeated until the
    score has moved RETRY_MOVE."""
    from builder.search.gems import RETRY_MOVE
    from builder.search.state import EPSILON

    ctx, build = gear.ctx, gear.build
    if os.environ.get("POE2_CULTIVATED") == "0":  # off, for measuring what the trial costs a run
        return target
    if (target is None or not target.unique or not cultivatable(target.name) or ctx.ssf
            or any(v.startswith(LABEL) for v in target.variants) or clock.now() > deadline
            or not ctx.payload.get("cultivation_prices")):
        return target
    failed = gear.__dict__.setdefault("_cultivated_failed", {})
    key = (slot, target.name, target.raw)
    if key in failed and abs(build.score - failed[key]) <= RETRY_MOVE:
        return target
    offered = outcomes(target.name)
    original = next((u["raw"] for u in ctx.data.uniques if u["name"] == target.name), None)
    lines = explicit_lines(target.name, target.raw, original, ctx.unique_gaps.get(target.name, set()))
    if not offered or not lines:
        failed[key] = build.score
        return target
    raws = [target.raw] + [edited(target.raw, [], [o]) for o in offered]
    raws += [edited(target.raw, list(at), []) for at, _ in lines]
    scored = [s for s, _ in gear.deltas(slot, raws)]
    base = scored[0]
    gains = sorted(((s - base, o) for s, o in zip(scored[1:1 + len(offered)], offered) if s - base > EPSILON),
                   key=lambda x: -x[0])
    costs = sorted(((base - s, at, text) for s, (at, text) in zip(scored[1 + len(offered):], lines)), key=lambda x: x[0])
    n_all = len(outcomes(target.name, offered=False))  # every outcome can roll, offered or not
    # Every explicit line of the selected variant counts as one the orb might take (more lines than it can take lowers
    # the chance: the cautious side, never an underpriced craft); a two-line mod counts twice for the same reason
    k = max(len(lines), len(_explicit(original if original is not None else target.raw)))
    variants = []
    for _, o in gains[:TOP_OUTCOMES]:
        acceptable = sum(1 for c in costs if c[0] <= costs[0][0] + EPSILON)
        variants.append((costs[:1], [o], chances(n_all, k, 1, acceptable)))
    if len(gains) >= 2 and k >= 2:
        acceptable = sum(1 for c in costs if c[0] <= costs[1][0] + EPSILON)
        variants.append((costs[:2], [gains[0][1], gains[1][1]], chances(n_all, k, 2, acceptable)))
    left = gear.left_for(slot)
    options = []
    for dropped, add, chance in variants:
        priced = craft_price(ctx, target.name, target.price, chance)
        if priced is None or priced[0] > ctx.price_cap or priced[0] > left + 1e-9:
            continue
        options.append(dataclasses.replace(
            target, raw=edited(target.raw, [i for _, at, _ in dropped for i in at], add), price=priced[0], confidence=priced[1],
            variants=tuple(target.variants) + (label(add, [t for _, _, t in dropped]),)))
    if not options or clock.now() > deadline:
        failed[key] = build.score
        return target
    rescored = gear.deltas(slot, [o.raw for o in options])
    best_value, best = max(((gear.adjusted(s, o), o) for (s, _), o in zip(rescored, options)), key=lambda x: x[0])
    if best_value > gear.adjusted(base, target) + EPSILON:
        return best
    failed[key] = build.score
    return target

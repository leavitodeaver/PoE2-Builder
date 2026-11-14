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
ORB = "Vaal Cultivation Orb"
VAAL_ORB = "Vaal Orb"
LABEL = "Cultivated"
# UNVERIFIED odds (no game file or poe2db gives them; the manager's cautious default, 14 Nov ~05:00): every outcome of a
# unique equally likely, every replaceable line equally likely to go, and one line replaced half the time, two the other
# half. A try counts as a success only when it replaced exactly the lines wanted with exactly the outcomes wanted, so a
# two-line result with one unwanted outcome is a failure (no overcount of the odds). LOW, in every price label.
ONE_LINE_SHARE = 0.5
TOP_OUTCOMES = 3  # best single outcomes (by what-if gain) tried as whole variants


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def uniques() -> dict[str, dict]:
    return load()["uniques"]


def cultivatable(name: str) -> bool:
    return name in uniques()


def outcomes(name: str, offered: bool = True) -> list[dict]:
    """A unique's outcomes. offered=True keeps those the search can score as a fixed line: PoB reads them whole and
    nothing marks them 'not_offered'. The rest stay in the data (an outcome PoB doesn't read is a coverage gap for
    the ledger, not a dropped mechanic; Flesh Crucible's random keystone needs one variant per keystone)."""
    found = uniques().get(name, {}).get("outcomes", [])
    if not offered:
        return list(found)
    return [o for o in found if o["pob_reads"] and "not_offered" not in o]


def replaceable_lines(name: str) -> list[str]:
    """The unique's explicit lines (poe2db's text) the orb may replace, as matched to the game's OriginalMods."""
    return [line["text"] for line in uniques().get(name, {}).get("lines", []) if line["replaceable"]]


def unread_outcomes() -> list[tuple[str, str, list[str]]]:
    """(unique, outcome mod, its lines) for every outcome PoB does not parse whole: the ledger's open items."""
    return [(name, o["mod"], o["pob_unread_lines"] or o["lines"])
            for name, u in uniques().items() for o in u["outcomes"] if not o["pob_reads"]]


# Prices ------------------------------------------------------------------------------------------------------------

def payload_prices(prices) -> dict:
    """What a craft costs, for the search's payload (context.make_payload): the orb, a Vaal Orb, and the cultivatable
    uniques' corrupted copies where poe.ninja lists one (its unique feed marks corrupted copies). Empty without the orb's
    price: then nothing is crafted (an unpriced orb is never free)."""
    if prices is None or prices.item(ORB) is None:
        return {}
    corrupted = {}
    for name in uniques():
        copies = [p for p in prices.uniques.get(name) or [] if p.corrupted]
        if copies:
            corrupted[name] = min(p.divine for p in copies)
    vaal = prices.item(VAAL_ORB)
    return {"orb": prices.item(ORB).divine, "vaal": vaal.divine if vaal else None, "corrupted": corrupted}


def success_chance(n_outcomes: int, k_lines: int, wanted: int, acceptable: int) -> float:
    """One try's chance of the wanted result under the UNVERIFIED equal odds (see ONE_LINE_SHARE): `wanted` (1 or 2)
    specific outcomes of the unique's n, each replacing one of the `acceptable` lines of its k replaceable lines."""
    if wanted == 1:
        if n_outcomes < 1 or k_lines < 1:
            return 0.0
        return ONE_LINE_SHARE * (min(acceptable, k_lines) / k_lines) / n_outcomes
    if n_outcomes < 2 or k_lines < 2 or acceptable < 2:
        return 0.0
    pair_of_outcomes = 1.0 / math.comb(n_outcomes, 2)
    pair_of_lines = math.comb(min(acceptable, k_lines), 2) / math.comb(k_lines, 2)
    return (1.0 - ONE_LINE_SHARE) * pair_of_outcomes * pair_of_lines


def craft_price(ctx, name: str, plain_price: float | None, chance: float) -> tuple[float, str] | None:
    """(divines, label) for crafting one cultivated copy: expected tries x (a corrupted copy, or a plain copy and a Vaal
    Orb, plus a Vaal Cultivation Orb). None when anything is unpriced or the chance is 0."""
    costs = ctx.payload.get("cultivation_prices") or {}
    orb = costs.get("orb")
    corrupted = (costs.get("corrupted") or {}).get(name)
    if corrupted is not None:
        base, how = corrupted, "a corrupted copy"
    elif plain_price is not None and costs.get("vaal") is not None:
        base, how = plain_price + costs["vaal"], "a plain copy + a Vaal Orb"
    else:
        return None
    if orb is None or chance <= 0.0:
        return None
    tries = 1.0 / chance
    price = tries * (base + orb)
    return price, (f"craft estimate, LOW: ~{tries:.0f} tries x ({how} + a {ORB}, {orb:.2f} div); outcome odds unknown, "
                   "assumed equal (UNVERIFIED)")


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
    none, as PoB does): past the implicits (or the header when there's no Implicits line), no enchants, sockets,
    blanks or bare keywords."""
    lines = raw.split("\n")
    variants = [line for line in lines if line.startswith("Variant:")]
    selected = next((int(m.group(1)) for line in lines if (m := re.match(r"Selected Variant: (\d+)", line))),
                    len(variants) or None)
    start = next((i + 1 + int(m.group(1)) for i, line in enumerate(lines)
                  if (m := re.match(r"Implicits: (\d+)", line))), 3)
    found = []
    for i in range(start, len(lines)):
        tags, text = re.match(r"^((?:\{[^}]*\})*)(.*)$", lines[i]).groups()
        if variant := re.search(r"\{variant:([\d,]+)\}", tags):
            if selected is None or str(selected) not in variant.group(1).split(","):
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
    wanted = {_key(t) for t in replaceable_lines(name)}
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
    lines += ["{mutated}{range:0.5}" + line for o in add for line in o["lines"]]
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
    k = len(lines)
    variants = []
    for _, o in gains[:TOP_OUTCOMES]:
        acceptable = sum(1 for c in costs if c[0] <= costs[0][0] + EPSILON)
        variants.append((costs[:1], [o], success_chance(n_all, k, 1, acceptable)))
    if len(gains) >= 2 and k >= 2:
        acceptable = sum(1 for c in costs if c[0] <= costs[1][0] + EPSILON)
        variants.append((costs[:2], [gains[0][1], gains[1][1]], success_chance(n_all, k, 2, acceptable)))
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

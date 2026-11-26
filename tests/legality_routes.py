"""Every crafting route, on stubbed scores that make each route win, so each one really produces items, and every item
text it scores or returns is read back and checked by the rare legality check (SEARCHC4). Moved out of
tests/test_legality.py by SEARCHC7 (PERF22's note, 24 Nov: those route tests set the parallel suite's floor): the two
test files tests/test_legality_routes_a.py and _b.py each take half of ROUTES on their own engine. Not a test file."""
from __future__ import annotations

import faulthandler
import random
from types import SimpleNamespace

import pytest

from builder import clock
from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import alloys, legality
from builder.search.context import Context, make_payload
from builder.search.gear import Gear, SlotSpec
from builder.search.legality import ILLEGAL
from builder.search.objective import Objective
from builder.search.rares import CATALYSTS
from builder.search.state import Build


def new_engine():
    """A module's own PoB engine (the fixture body test_legality.py had)."""
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def broken(found) -> set[str]:
    return {f.rule for f in found if f.status == ILLEGAL}



ROUTES = {
    "POE2_SEARCH_ALDUR": "aldur_trial",
    "POE2_SEARCH_CATALYST": "catalyst_trial",
    "POE2_SEARCH_SERLE": "serle_trial",
    "POE2_SEARCH_EXTRA_SOCKETS": "extra_socket_trial",
    "POE2_SEARCH_FLUX": "flux_trial",
    "POE2_SEARCH_AUGMENT_EFFECT": "augment_effect_trial",
    "POE2_SEARCH_PERFECT_FLUX": "perfect_flux_trial",
    "POE2_SEARCH_ASTRID": "astrid_trial",
}
# Each test file's half of ROUTES (by 24 Nov's timings, POE2T7696: ~124 s and ~114 s of route time)
HALVES = {
    "a": ("POE2_SEARCH_EXTRA_SOCKETS", "POE2_SEARCH_AUGMENT_EFFECT", "POE2_SEARCH_FLUX", "POE2_SEARCH_CATALYST"),
    "b": ("POE2_SEARCH_SERLE", "POE2_SEARCH_ASTRID", "POE2_SEARCH_ALDUR", "POE2_SEARCH_PERFECT_FLUX"),
}
SLOTS = [("Weapon 1", "Wand"), ("Weapon 1", "Staff"), ("Gloves", None), ("Boots", None), ("Helmet", None),
         ("Body Armour", None), ("Ring 1", None), ("Amulet", None), ("Belt", None)]


def route_gear(engine, weapon: str):
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    for rune in ctx.data.runes:
        ctx.augment_prices.setdefault(rune["name"], 0.01)
    ctx.payload.update({
        "catalyst_prices": {n: 0.01 for n in CATALYSTS},
        "breach_quality_prices": {"Essence of the Breach": 0.5, "Vaal Catalysing Infuser": 5.0},
        "flux_prices": {"Void Flux": 0.1, "Blazing Flux": 0.1, "Chilling Flux": 0.1, "Crackling Flux": 0.1},
        "augment_effect_prices": {"Essence of Horror": 1.0, "Omen of Dextral Crystallisation": 0.4},
        "serle_prices": {"Exalted Orb": 0.01, "Greater Exalted Orb": 0.1, "Perfect Exalted Orb": 1.0},
        "perfect_flux_price": 1.0, "cultivation_prices": {"vaal": 0.01}})
    ctx.alloy_prices = {name: 0.2 for name, _ in alloys.ALLOYS.values()}
    ctx.influence_prices = {**ctx.influence_prices, "Orb of Annulment": 0.3, "Chaos Orb": 0.1, "Exalted Orb": 0.01,
                            "Omen of Whittling": 10.0, "Omen of Dextral Exaltation": 0.1,
                            "Omen of Sinistral Exaltation": 0.1, "Omen of Dextral Annulment": 1.0,
                            "Omen of Sinistral Annulment": 1.0}
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Spark 20/0  1", main=True, fullDPS=True)
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec(weapon), None, random.Random(0))
    gear.lam = 0.0

    def stub(cases):  # (a longer item scores higher, so a route that adds something wins and is returned)
        return [(float(len(c["raw"])), {"s": float(len(c["raw"]))}) for c in cases]

    build.item_deltas = stub
    build.objective = SimpleNamespace(
        score=lambda st: float(st.get("s", 0.0)) * float(st.get(alloys.ALLOY_FIELD, 1.0) or 1.0),
        dps=lambda st: float(st.get("s", 0.0)))
    build.alloy_product = lambda slot=None: 1.0
    return ctx, gear


def check_route(engine, monkeypatch, check, switch, method):
    """Every item text a route scores while it runs (each candidate it considers, not only the one it keeps) is read back
    and checked, and so is every item it returns."""
    for name in ROUTES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(switch, "1")
    seen: dict[str, None] = {}
    kept: list[dict] = []
    for weapon in ("Wand", "Staff"):
        _, gear = route_gear(engine, weapon)
        real, inside = getattr(gear, method), [False]
        score = gear.build.item_deltas

        def spy(*a, real=real, **k):
            inside[0] = True
            try:
                out = real(*a, **k)
            finally:
                inside[0] = False
            if out is not None and out.rare:
                kept.append(out.describe())
            return out

        def watch(cases, score=score, inside=inside):
            if inside[0]:
                seen.update((c["raw"], None) for c in cases)
            return score(cases)

        gear.build.item_deltas = watch
        monkeypatch.setattr(gear, method, spy)
        for slot, kind in SLOTS:
            if kind and kind != weapon:
                continue
            for option in [o for o in gear.options(slot) if not o.unique][:8]:
                item = gear.build_rare(slot, clock.now() + 30, base_name=option.base)
                if item is not None and switch == "POE2_SEARCH_EXTRA_SOCKETS":
                    gear.socketed(slot, item, clock.now() + 30)
        if switch == "POE2_SEARCH_FLUX":  # (a rare holding elemental resistances: what the Flux turns)
            from builder.search.gear import affix_limits
            boots = next(b for b in gear.ctx.data.bases if b["type"] == "Boots" and b.get("socketLimit"))
            pool = gear.pools.pool(boots["name"])
            chosen = [next(x for x in pool if x.type == "Suffix" and any(f"{e} Resistance" in l for l in x.lines))
                      for e in ("Fire", "Cold", "Lightning")]
            option = gear.options("Boots")[0]
            getattr(gear, method)("Boots", boots, option, gear.pools.item_level, gear.pools, affix_limits(boots), chosen,
                                  0.0, 1e6, clock.now() + 60)
    # (a scored text carries no route note: under the Flux switch, it is the Flux's output that turns resistances)
    route = ["Flux"] if switch == "POE2_SEARCH_FLUX" else []
    # (a Corrupted text comes only from the corrupted socket route, whose order is known: its bound runes (a NOT_IN_SEALED
    # one among them since SEARCHC7) went in before the Vaal Orb, every rune in the text after)
    items = [{**(item := legality.item_from_text(raw, check)),
              "craft_route": route + (["Sockets (corrupted): the route's order"] if item["corrupted"] else [])}
             for raw in seen] + kept
    # (SEARCHC5, test cost) one check per reading_key: items differing only in rolled numbers break the same rules
    keyed = {legality.reading_key(i): i for i in items}
    found = {key: check.check(i) for key, i in keyed.items()}
    bad = [(i["base"], i["affixes"], i["runes"], [f for f in found[key] if f.status == ILLEGAL])
           for key, i in keyed.items() if broken(found[key])]
    for row in bad[:4]:
        print("PROBE illegal", switch, *row)
    print("PROBE route", switch, "texts scored", len(seen), "items kept", len(kept), "readings checked", len(keyed))
    assert seen, f"{method} scored nothing: the test proves nothing"
    assert not bad, (len(bad), len(items), bad[:3])

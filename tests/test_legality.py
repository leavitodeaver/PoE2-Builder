"""The rare legality check (SEARCHC4; the manager, 23 Nov 21:27): each rule on a known-bad item, then every crafting route."""
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
from builder.search.legality import ILLEGAL, UNVERIFIED, Checker
from builder.search.objective import Objective
from builder.search.rares import ALDUR_RUNES, CATALYSTS, roll
from builder.search.state import Build


@pytest.fixture(scope="module")
def engine():
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


@pytest.fixture(scope="module")
def check():
    return Checker()


def rare(base: str, kind: str, affixes: list[str], runes: tuple[str, ...] = (), **more) -> dict:
    return {"name": f"Rare {kind}", "base": base, "type": kind, "unique": False, "rare": True, "affixes": affixes,
            "runes": list(runes), **more}


def broken(found) -> set[str]:
    return {f.rule for f in found if f.status == ILLEGAL}


def alloy_line(check: Checker, mod_id: str) -> str:
    return roll(next(m for m in check.data.mods["Item"] if m["id"] == mod_id)["1"], 0.5)


SUFFIXES = ["+30% to Fire Resistance", "+30% to Cold Resistance", "+30% to Lightning Resistance", "+20% to Chaos Resistance"]


def test_a_legal_rare_has_no_findings(check):
    assert broken(check.check(rare("Massive Mitts", "Gloves", SUFFIXES[:3] + ["+140 to maximum Life"]))) == set()


def test_a_fourth_suffix_is_illegal_and_serle_allows_it(check):
    item = rare("Massive Mitts", "Gloves", SUFFIXES)
    assert "affix_count" in broken(check.check(item))
    assert "affix_count" not in broken(check.check({**item, "runes": [legality.SERLE]}))


def test_two_skill_level_suffixes_are_one_family(check):
    item = rare("Acrid Wand", "Wand", ["+5 to Level of all Spell Skills", "+5 to Level of all Fire Spell Skills"])
    found = check.check(item)
    assert "mod_group" in broken(found), found


def test_two_of_one_group_are_illegal_and_two_separate_armour_lines_are_not(check):
    assert "mod_group" in broken(check.check(rare("Massive Mitts", "Gloves",
                                                  ["+140 to maximum Life", "+120 to maximum Life"])))
    # (these two lines also fit a hybrid that gloves can't roll: the reading as two separate modifiers is legal)
    assert broken(check.check(rare("Massive Mitts", "Gloves", ["+150 to Armour", "88% increased Armour"]))) == set()


def test_two_alloy_lines_need_astrid_and_one_does_not(check):
    """Two Belt Alloy lines no natural row reads (Runic Ward regeneration, flask charges per second)."""
    belt = next(b["name"] for b in check.data.bases if b.get("type") == "Belt")
    first, second = (alloy_line(check, i) for i in ("AlloyRunicWardRechargeRate1", "AlloyFlaskChargesPerSecond1"))
    for line in (first, second):
        assert all(r.kind == "alloy" for r in check.index["Item"][(legality.template(line),)])
    assert "crafted_limit" not in broken(check.check(rare(belt, "Belt", [first])))
    two = rare(belt, "Belt", [first, second])
    assert "crafted_limit" in broken(check.check(two)), two["affixes"]
    assert "crafted_limit" not in broken(check.check({**two, "runes": [legality.ASTRID]}))


def test_an_item_text_reads_back_as_its_entry(check):
    raw = chr(10).join(["Rarity: Rare", "Optimizer Gloves", "Massive Mitts", "Item Level: 82", "Quality: 20", "Sockets: S S",
                     "Rune: Serle's Triumph", "Implicits: 0", "{tags:x}+30% to Fire Resistance", "+140 to maximum Life",
                     "Corrupted"])
    item = legality.item_from_text(raw, check)
    assert item["base"] == "Massive Mitts" and item["type"] == "Gloves" and item["runes"] == ["Serle's Triumph"]
    assert item["affixes"] == ["+30% to Fire Resistance", "+140 to maximum Life"] and item["corrupted"]
    # (a text has no route order: the rune may have gone in before the Vaal Orb, so UNVERIFIED, never a failure)
    assert "sealed_rune" not in broken(check.check(item))
    assert [f.status for f in check.check(item) if f.rule == "sealed_rune"] == [UNVERIFIED]


def test_a_mod_the_base_cannot_roll_is_illegal(check):
    assert "base_allowed" in broken(check.check(rare("Pearl Ring", "Ring", ["88% increased Armour"])))


def test_a_sealed_rune_on_a_corrupted_item_is_illegal(check):
    item = rare("Massive Mitts", "Gloves", SUFFIXES[:3], (legality.SERLE,), craft_route=["Sockets (corrupted): ..."])
    assert "sealed_rune" in broken(check.check(item))


def test_a_sealed_rune_socketed_before_the_routes_vaal_orb_stays(check):
    """(0.5.4: such augments 'can no longer be socketed into' a Corrupted item; one already in stays) The corrupted
    socket route puts its bound runes in before the Vaal Orb and the rest after."""
    item = rare("Massive Mitts", "Gloves", SUFFIXES[:3] + [SUFFIXES[3]], (legality.SERLE,), bound_runes=[legality.SERLE],
                craft_route=["Sockets (corrupted): ..."])
    assert "sealed_rune" not in {f.rule for f in check.check(item)}


# THE USER's listing (24 Nov ~01:25, a trade screenshot): a Twice Corrupted Chiming Staff, Passion of Aldur socketed;
# a Celestial Alloy prefix (+175 Mana with +1 to all Spell Skills) beside a "+8 to Level of all Fire Spell Skills" suffix.
# Its "20% increased Explicit Fire Modifier Magnitudes" is Thrud's Might's Destruction mod (spawns only by that rune's tag;
# the trade site capitalises "Magnitudes"), and "39% increased Cast Speed" + "Gain 12% of Elemental Damage as Extra Cold
# Damage" is the Transcendent Alloy hybrid: a second crafted line, which needs Astrid's Creativity (0.5.0: one crafted
# modifier per item). Which augments the listing shows besides Passion of Aldur is the manager's question (SEARCHC5).
STAFF = ["235% increased Spell Damage", "Gain 67% of Damage as Extra Fire Damage", "+175 to maximum Mana",
         "+1 to Level of all Spell Skills", "+8 to Level of all Fire Spell Skills", "39% increased Cast Speed",
         "Gain 12% of Elemental Damage as Extra Cold Damage", "20% increased Explicit Fire Modifier Magnitudes"]


@pytest.mark.parametrize("aldur", [True, False])
def test_the_users_listed_staff_is_legal_with_and_without_its_aldur_rune(check, aldur):
    runes = (("Passion of Aldur",) if aldur else ()) + ("Thrud's Might", legality.ASTRID)
    found = check.check(rare("Chiming Staff", "Staff", STAFF, runes, corrupted=True))
    assert not [f for f in found if f.status == ILLEGAL], found
    assert {f.rule for f in found} == {"sealed_rune"}, "every line read; only the unknown socketing order is open"


def test_the_staffs_alloy_pair_is_never_hidden_as_an_unread_line(check):
    """Without Astrid's, every reading of all eight lines breaks a rule (two Alloy lines, or the Celestial pair read as
    two natural mods and four suffixes): said, not dropped behind the unread Cold line."""
    found = check.check(rare("Chiming Staff", "Staff", STAFF, ("Passion of Aldur", "Thrud's Might"), corrupted=True))
    assert not [f for f in found if f.status == ILLEGAL], found
    assert [f.rule for f in found if f.rule in ("unread", "full_reading")] == ["unread", "full_reading"], found


def test_two_natural_skill_level_affixes_stay_illegal_beside_the_staffs_lines(check):
    lines = STAFF[:2] + ["+4 to Level of all Spell Skills"] + STAFF[4:]
    found = check.check(rare("Chiming Staff", "Staff", lines, ("Thrud's Might", legality.ASTRID), corrupted=True))
    assert "mod_group" in broken(found), found


def test_items_with_one_reading_key_break_the_same_rules(check):
    """(the route test checks one item per key) Rolled numbers change no rule; the runes, the route and a line's text do."""
    one = rare("Chiming Staff", "Staff", STAFF, ("Passion of Aldur",), corrupted=True)
    two = {**one, "affixes": [line.replace("67%", "55%").replace("+8", "+(7-9)") for line in STAFF]}
    assert legality.reading_key(one) == legality.reading_key(two)
    assert [(f.rule, f.status) for f in check.check(one)] == [(f.rule, f.status) for f in check.check(two)]
    for other in ({**one, "runes": ["Thrud's Might"]}, {**one, "craft_route": ["Sockets (corrupted): x"]},
                  {**one, "affixes": STAFF[:-1]}, {**one, "corrupted": False}):
        assert legality.reading_key(other) != legality.reading_key(one)


def test_a_thruds_might_line_needs_the_rune(check):
    item = rare("Chiming Staff", "Staff", STAFF[7:], ("Passion of Aldur",))
    assert "base_allowed" in broken(check.check(item)), "an Aldur rune never makes a Destruction mod"
    assert not broken(check.check({**item, "runes": ["Thrud's Might"]}))


def test_a_line_no_table_holds_is_unverified_and_never_fails(check):
    item = rare("Massive Mitts", "Gloves", ["Wibbles the boss for 12 wobbles"])
    found = check.check(item)
    assert [f.status for f in found] == [UNVERIFIED] and found[0].rule == "unread"
    assert legality.check_gear({"Gloves": item}, check)["illegal_rares"] == 0


def test_an_aldur_rune_may_leave_two_lines_of_one_element(check):
    item = rare("Acrid Wand", "Wand", ["80% increased Fire Damage", "70% increased Fire Damage"])
    assert "mod_group" in broken(check.check(item))
    assert "mod_group" not in broken(check.check({**item, "runes": [ALDUR_RUNES["Fire"]]}))


# Every route, on stubbed scores that make each route win, so each one really produces items --------------------------

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


@pytest.mark.parametrize("switch, method", ROUTES.items())
def test_every_rare_a_route_makes_is_legal(engine, monkeypatch, check, switch, method):
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
    # (a Corrupted text comes only from the corrupted socket route, whose order is known: its bound runes, none of them
    # NOT_IN_SEALED (the route skips those), went in before the Vaal Orb, every rune in the text after)
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


def test_the_pruned_reading_walk_finds_what_trying_every_reading_finds(check, monkeypatch):
    """(SEARCHC5, test cost) readings leaving more lines unread than needed are tried only when needed: same findings."""
    items = [rare("Chiming Staff", "Staff", STAFF, runes, corrupted=True)
             for runes in ((), ("Passion of Aldur",), ("Thrud's Might", legality.ASTRID))]
    items += [rare("Massive Mitts", "Gloves", SUFFIXES + ["+140 to maximum Life", "Wibbles the boss for 12 wobbles"]),
              rare("Massive Mitts", "Gloves", ["Wibbles the boss", "+140 to maximum Life", "+30% to Fire Resistance"]),
              rare("Acrid Wand", "Wand", ["80% increased Fire Damage", "70% increased Fire Damage", "Wibbles"]),
              rare("Chiming Staff", "Staff", STAFF[:2] + ["+4 to Level of all Spell Skills"] + STAFF[4:])]
    pruned = [check.check(i) for i in items]
    monkeypatch.setattr(legality, "PRUNE", False)
    assert pruned == [check.check(i) for i in items]

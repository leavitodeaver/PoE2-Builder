"""A unique's own socket rules (COV28; AUDIT33 A33-2's flag scan): "Only Soul Cores / Only Runes can be Socketed in this
item" and fixed augment sockets, which PoB reads only in its socket menu, so the search's filler has to."""
from types import SimpleNamespace

from builder.data import GameData
from builder.search.rares import RuneCatalog, only_augment, socket_count


def _unique(data, name):
    return next(u for u in data.uniques if u["name"] == name)


def _catalog(data):
    ctx = SimpleNamespace(data=data, ssf=True, level=100, price_cap=1e9,
                          compiled=SimpleNamespace(filters=SimpleNamespace(forbidden_augments=set())))
    return RuneCatalog(ctx, {})


def _kinds(data, names, base):
    from builder.search.rares import rune_categories
    cats = rune_categories(base)
    by_name = {r["name"]: r for r in data.runes}
    return {e.get("type") for n in names for c, e in by_name[n]["slots"].items() if c in cats and isinstance(e, dict)}


def test_only_soul_cores_and_only_runes_limit_what_the_filler_offers():
    data = GameData()
    bases = {b["name"]: b for b in data.bases}
    catalog = _catalog(data)
    atziri = _unique(data, "Atziri's Splendour")
    rune_call = _unique(data, "Runeseeker's Call")
    assert only_augment(atziri["raw"]) == "SoulCore" and only_augment(rune_call["raw"]) == "Rune"

    body = bases[atziri["base"]]
    plain = catalog.candidates(body, True)
    cores = catalog.candidates(body, True, atziri["raw"])
    assert cores and set(plain) - set(cores)  # before: any augment a unique body armour takes (runes too)
    assert _kinds(data, cores, body) == {"SoulCore"}

    wand = bases[rune_call["base"]]
    runes = catalog.candidates(wand, True, rune_call["raw"])
    assert runes and _kinds(data, runes, wand) == {"Rune"}
    assert set(runes) <= set(catalog.candidates(wand, True))  # (Soul Cores' names vary: "... Thesis", so by type)


def test_fixed_augment_sockets_are_kept_and_jewel_sockets_are_not_filled():
    data = GameData()
    bases = {b["name"]: b for b in data.bases}
    for name, count in (("Atziri's Splendour", 6), ("Runeseeker's Call", 5), ("Morior Invictus", 4)):
        unique = _unique(data, name)
        base = bases[unique["base"]]
        assert socket_count(base, True, unique["raw"]) == count
        assert socket_count(base, True) < count  # the base's own count, what the filler used before
    tabula = _unique(data, "Tabula Rasa")  # poe2db: 6 JEWEL sockets, not augment sockets
    base = bases[tabula["base"]]
    assert socket_count(base, True, tabula["raw"]) == socket_count(base, True)
    assert socket_count(base, True, "Rarity: UNIQUE\nX\nY\nHas no Sockets") == 0


def test_as_though_lines_widen_what_the_filler_offers_for_the_selected_variant():
    """Atziri's Splendour's Soul Cores count "as though it was also a Helmet" (its selected group variant), Darkness
    Enthroned's augments as though it were a Shield (PoB's default, the last variant), The Bringer of Rain's as though
    a Body Armour (PoB Classes/Item.lua 1279 / 1280 / 1527, GetSocketedAugmentTypes 2387)."""
    from builder.search.rares import augment_types

    data = GameData()
    bases = {b["name"]: b for b in data.bases}
    catalog = _catalog(data)
    by_name = {r["name"]: r for r in data.runes}
    atziri = _unique(data, "Atziri's Splendour")
    assert augment_types(atziri["raw"]) == (None, {"helmet"})
    cores = catalog.candidates(bases[atziri["base"]], True, atziri["raw"])
    helmet_only = [n for n in cores if "body armour" not in by_name[n]["slots"] and "armour" not in by_name[n]["slots"]]
    assert helmet_only and all(by_name[n]["slots"]["helmet"]["type"] == "SoulCore" for n in helmet_only)
    # a different selected group variant (Boots) changes the extra type
    boots = atziri["raw"].replace("Selected Variant Group: 1=1", "Selected Variant Group: 1=3")
    assert augment_types(boots) == (None, {"boots"})

    darkness = _unique(data, "Darkness Enthroned")
    belt = bases[darkness["base"]]
    assert augment_types(darkness["raw"]) == ("shield", set())
    assert socket_count(belt, True, darkness["raw"]) == 2 and socket_count(belt, True) == 0
    offered = catalog.candidates(belt, True, darkness["raw"])
    assert offered
    for n in offered:
        entries = [e for c, e in by_name[n]["slots"].items() if c in ("armour", "shield")]
        assert entries and any(e.get("canSocketInJewellery") for e in entries)
    rain = _unique(data, "The Bringer of Rain")
    assert augment_types(rain["raw"]) == ("body armour", set())
    for name, count in (("Serle's Grit", 3), ("Greymake", 4), ("The Bringer of Rain", 4)):
        unique = _unique(data, name)
        assert socket_count(bases[unique["base"]], True, unique["raw"]) == count

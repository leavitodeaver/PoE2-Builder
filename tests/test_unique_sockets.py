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
    assert cores and set(cores) < set(plain)  # before: any augment a unique body armour takes
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

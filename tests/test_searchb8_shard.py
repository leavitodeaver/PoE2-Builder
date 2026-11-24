"""SEARCHB8 (JEWEL2's ask): Gear.shard_version builds the helmet with a Raven-Touched Shard once, for Gear.shard_trial and
Jewels.shard_helmet (which built it by a copy of shard_trial's code). Both must give the same item."""
import dataclasses
from types import SimpleNamespace

from builder.search import anoints
from builder.search.gear import Gear
from builder.search.jewels import Jewels
from builder.search.state import ItemOption

BASE = {"name": "Iron Greathelm", "type": "Helmet", "implicit": "", "socketLimit": 3}  # one socket (rares.socket_count)


class _Runes:
    def candidates(self, base, unique, raw):
        return [anoints.SHARD, "Desert Rune"]

    def limit(self, name):
        return ("shard", 1) if name == anoints.SHARD else None

    def price(self, name):
        return {anoints.SHARD: 0.5, "Desert Rune": 0.25}.get(name)


def _helmet(runes=(), bound=(), raw="Rarity: Normal\nIron Greathelm", price=1.0) -> ItemOption:
    return ItemOption(name="Iron Greathelm", base="Iron Greathelm", type="Helmet", sub_type=None, raw=raw, unique=False,
                      price=price, confidence="found", level=1, runes=tuple(runes), bound=tuple(bound))


def _pair(equipped=None):
    ctx = SimpleNamespace(bases_by_name={"Iron Greathelm": BASE})
    build = SimpleNamespace(equipped=equipped or {})
    gear = Gear.__new__(Gear)
    gear.ctx, gear.build, gear.runes = ctx, build, _Runes()
    jewels = Jewels.__new__(Jewels)
    jewels.gear, jewels.ctx, jewels.build = gear, ctx, build
    return gear, jewels


def _same(a, b):
    assert (a is None) == (b is None)
    if a is not None:
        assert (a.raw, a.runes, a.price) == (b.raw, b.runes, b.price)


def test_the_shard_version_matches_jewels_own_copy():
    gear, jewels = _pair()
    for helmet in (_helmet(), _helmet(runes=["Desert Rune"]), _helmet(price=None),
                   _helmet(runes=["Desert Rune"], bound=["Desert Rune"]),  # a socket-bound rune is never replaced
                   _helmet(runes=[anoints.SHARD]), _helmet(raw="Rarity: Normal\nIron Greathelm\nCorrupted")):
        _same(gear.shard_version("Helmet", helmet), jewels.shard_helmet("Helmet", helmet))
    shard = gear.shard_version("Helmet", _helmet(runes=["Desert Rune"]))
    assert shard.runes == (anoints.SHARD,) and shard.price == 1.0 + 0.5 - 0.25
    assert gear.shard_version("Helmet", _helmet(runes=["Desert Rune"], bound=["Desert Rune"])) is None


def test_one_shard_per_character():
    other = dataclasses.replace(_helmet(runes=[anoints.SHARD]), type="Gloves")
    gear, jewels = _pair({"Gloves": other})
    assert gear.shard_version("Helmet", _helmet()) is None and jewels.shard_helmet("Helmet", _helmet()) is None

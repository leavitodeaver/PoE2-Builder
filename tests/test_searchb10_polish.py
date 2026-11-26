"""SEARCHB10: Gear.polish (POE2_SEARCH_POLISH, OFF by default; the manager 26 Nov 05:34, SPEED4's ranked item 0): the
finished build's anoint / rune polish over every equipped slot, the Amulet first, each change kept only if the score
rises. Pure: a fake Gear and fake items, no PoB."""
from types import SimpleNamespace

import pytest

from builder.search import gear


def item(name, runes=(), bound=(), anoints=()):
    return SimpleNamespace(name=name, runes=tuple(runes), bound=tuple(bound), anoints=tuple(anoints), raw=name,
                           filler_runes=(), price=0.0)


class FakeBuild:
    def __init__(self, equipped, gains):
        self.equipped, self.gains, self.score, self.locked_slots = dict(equipped), gains, 10.0, set()

    def rescore(self):
        self.score = 10.0 + sum(self.gains.get(o.name, 0.0) for o in self.equipped.values())


def fake(equipped, gains, anointed=None, socketed=None, locked=()):
    build = FakeBuild(equipped, gains)
    build.locked_slots = set(locked)
    g = SimpleNamespace(build=build, slots={s: None for s in ["Weapon 1", "Helmet", "Amulet", "Ring 1", "Flask 1"]},
                        _anoint_screen=["stale"], visited=[])
    g.put = lambda slot, o: build.equipped.__setitem__(slot, o)
    g.settle = build.rescore
    g.anointed = lambda slot, o, d: (g.visited.append(slot), (anointed or {}).get(slot, o))[1]
    g.socketed = lambda slot, o, d: (socketed or {}).get(slot, o)
    g.commit_anoints = lambda slot, o, d: False
    return g


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(gear.SWITCH_POLISH, raising=False)
    assert not gear.polish_on()
    monkeypatch.setenv(gear.SWITCH_POLISH, "1")
    assert gear.polish_on()
    assert gear.polish_seconds(600.0) == gear.POLISH_SECONDS and gear.polish_seconds(20.0) == 20.0


def test_a_gaining_anoint_is_kept_amulet_first(monkeypatch):
    monkeypatch.setattr(gear.clock, "now", lambda: 0.0)
    amulet, weapon = item("amulet"), item("weapon")
    g = fake({"Weapon 1": weapon, "Amulet": amulet}, {"amulet+LT": 1.0},
             anointed={"Amulet": item("amulet+LT", anoints=["Lasting Trauma"])})
    rows = gear.Gear.polish(g, 100.0)
    assert g.visited[0] == "Amulet" and g._anoint_screen is None
    assert g.build.equipped["Amulet"].name == "amulet+LT" and g.build.score == 11.0
    assert rows == [{"slot": "Amulet", "before": 10.0, "after": 11.0, "kept": True, "what": "anoints",
                     "anoints": ["Lasting Trauma"]}]


def test_a_losing_change_goes_back(monkeypatch):
    monkeypatch.setattr(gear.clock, "now", lambda: 0.0)
    helmet = item("helmet", runes=["r"], bound=["r"])
    g = fake({"Helmet": helmet}, {"helmet+runes": -0.5}, socketed={"Helmet": item("helmet+runes")})
    rows = gear.Gear.polish(g, 100.0)
    assert g.build.equipped["Helmet"] is helmet and g.build.score == 10.0
    assert rows[0]["kept"] is False and rows[0]["what"] == "runes"


def test_out_of_time_notes_the_rest(monkeypatch):
    monkeypatch.setattr(gear.clock, "now", lambda: 200.0)
    g = fake({"Amulet": item("amulet")}, {})
    assert gear.Gear.polish(g, 100.0) == []
    assert any(r["step"] == "gear polish" for r in g.build.unfinished)

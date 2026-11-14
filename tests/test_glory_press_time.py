"""SEARCH15 (SEARCH14's Handover; glory_uses_capped's "not charged here", routed): a Glory skill the build counts a share
from is pressed, and its presses take the main skill's time: uses x 1 / its own speed, in objective.glory_busy
(Objective.busy), except where that time is charged already (the main skill, a pressed Full DPS group: press_model; a
chosen press rate: Search.press_sync) or the player doesn't press it (a meta skill or a totem uses it)."""
from types import SimpleNamespace

import pytest

from builder.search import models
from builder.search.assumptions import Assumptions
from builder.search.objective import Objective

GEMS = {"Molten Blast": {"skillTypes": ["Attack", "Area"], "support": False},
        "Fireball": {"skillTypes": ["Spell", "Projectile"], "support": False},
        "Cast on Ignite": {"skillTypes": ["Meta", "Triggers"], "support": False}}


def gem(name, support=False):
    return {"name": name, "enabled": True, "support": support}


class Build:
    def __init__(self, full=False, meta=False, choices=None):
        self.objective = SimpleNamespace(full=full, press_choices=dict(choices or {}), glory_busy=0.0)
        self.ctx = SimpleNamespace(gems=GEMS)
        blast = [gem("Molten Blast")] + ([gem("Cast on Ignite")] if meta else [])
        self._groups = [{"index": 1, "main": False, "enabled": True, "fullDPS": full, "fromItem": False, "gems": blast},
                        {"index": 2, "main": True, "enabled": True, "fullDPS": True, "fromItem": False,
                         "gems": [gem("Fireball")]}]

    def groups(self):
        return self._groups

    def call(self, name, **kw):
        assert name == "useRates"
        return {"rates": [{"index": 1, "speed": 4.0}]}


def busy(monkeypatch, **kw) -> float:
    monkeypatch.setattr(models, "glory_uses", lambda names, *more: (2.0 if "Molten Blast" in names else 0.0, "x"))
    a = Assumptions.__new__(Assumptions)
    a.build = Build(**kw)
    total, how = a.glory_uses_capped(["Molten Blast", "Fireball"], {}, models.Skill(name="Fireball"))
    assert total == pytest.approx(2.0)
    return a.build.objective.glory_busy


def test_pressing_a_glory_skill_takes_the_main_skills_time(monkeypatch):
    assert busy(monkeypatch) == pytest.approx(2.0 / 4.0)  # (2 uses a second, each 1 / 4 s)


def test_glory_press_time_is_never_charged_twice_or_for_a_skill_nobody_presses(monkeypatch):
    assert busy(monkeypatch, full=True) == 0.0  # (a pressed Full DPS group: press_model charges it)
    assert busy(monkeypatch, choices={"Molten Blast": 1.0}) == 0.0  # (a chosen rate: press_sync charges it)
    assert busy(monkeypatch, meta=True) == 0.0  # (a meta skill uses it: no press)


def test_objective_busy_adds_the_glory_press_time():
    o = Objective.__new__(Objective)
    o.press_busy, o.glory_busy = 0.25, 0.5
    assert o.busy() == pytest.approx(0.75)
    o.glory_busy = 0.9
    assert o.busy() == 1.0


def test_a_pressed_glory_skill_is_held_to_the_uses_its_glory_pays_for(monkeypatch):
    """(MECHB21, SEARCH15's open OVERCOUNT) press_caps holds a Glory skill outside the main group to models.glory_plan's
    uses, so press_model never counts it at its duration or the search's chosen rate beyond them; the main group's own
    Glory skill is left to glory_main."""
    from builder.search import assumptions as assumptions_mod

    monkeypatch.setattr(assumptions_mod, "usage_rules", lambda build: {})
    monkeypatch.setattr(Assumptions, "glory_caps", lambda self: {"Molten Blast": (0.4, "one per 2.5 s")})
    a = Assumptions.__new__(Assumptions)
    a.build = Build(full=True)
    a.build._groups[1]["gems"].append(gem("Molten Blast"))
    caps = a.press_caps(a.build._groups, {1, 2})
    assert set(caps) == {1} and caps[1].uses == pytest.approx(0.4) and caps[1].how.startswith("Glory")
    real, how, _ = models.press_group_rate({"speed": 4.0}, cap=caps[1], chosen=2.0)
    assert real == pytest.approx(0.4) and "Glory" in how
